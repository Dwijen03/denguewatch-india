"""Step 11: the news-signal agent - early, UNVERIFIED hints of dengue outbreaks from the news.

Official IDSP reports arrive about 8 weeks late. Local news often mentions a rise in dengue
cases much sooner. Once a day this agent:

  1. reads Google News RSS search results for dengue in India (last 7 days),
  2. skips articles it has already seen (url is UNIQUE in news_signals),
  3. asks Gemini to read each headline and extract: is it a report of dengue cases in a specific
     Indian place? state, district, cases, deaths, confidence,
  4. matches the district to the Census 2011 list (same matcher as the IDSP pipeline),
  5. stores every article in news_signals - marked unverified, never mixed with official data,
  6. checks older signals against IDSP: if IDSP later lists an outbreak in that district within
     30 days, the signal is marked 'matches_idsp'.

Guardrails: the model only extracts fields into a fixed JSON format; numbers are range-checked;
it has no access to the database or the web.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine

from denguewatch.agent.llm import Gemini, default_client
from denguewatch.db.connection import get_engine

log = logging.getLogger(__name__)

QUERIES = ["dengue cases district", "dengue outbreak India", "dengue deaths India"]
RSS_URL = "https://news.google.com/rss/search?q={q}+when:7d&hl=en-IN&gl=IN&ceid=IN:en"
BATCH = 20
MAX_ARTICLES = 60

SYSTEM = """You extract structured facts from Indian news headlines about dengue.
For each headline decide if it reports dengue cases, deaths or an outbreak in a SPECIFIC place in India
(a district or city). General health advice, national totals, research, vaccines, other countries or other
diseases are NOT relevant. Use only what the headline says - never guess numbers.
Reply with JSON: {"items": [{"id": <id>, "relevant": true|false, "state": "<Indian state or null>",
"district": "<district or city or null>", "cases": <integer or null>, "deaths": <integer or null>,
"confidence": <0.0-1.0>, "summary": "<max 15 words>"}]}  - one item per headline, same ids."""


@dataclass
class Article:
    url: str
    headline: str
    source: str | None
    published_at: object  # datetime | None


def parse_rss(xml_text: str) -> list[Article]:
    """Articles from a Google News RSS feed."""
    out = []
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "").strip() or None
        if source and title.endswith(" - " + source):           # Google appends " - Source" to titles
            title = title[: -len(source) - 3].strip()
        try:
            published = parsedate_to_datetime(item.findtext("pubDate"))
        except (TypeError, ValueError):
            published = None
        if title and link:
            out.append(Article(link, title, source, published))
    return out


def fetch_articles(session: requests.Session | None = None) -> list[Article]:
    s = session or requests.Session()
    seen, out = set(), []
    for q in QUERIES:
        resp = s.get(RSS_URL.format(q=quote_plus(q)), timeout=30,
                     headers={"User-Agent": "DengueWatch-India academic project"})
        resp.raise_for_status()
        for a in parse_rss(resp.text):
            key = re.sub(r"\W+", " ", a.headline.lower()).strip()   # same story from several queries
            if a.url not in seen and key not in seen:
                seen.update({a.url, key})
                out.append(a)
    return out[:MAX_ARTICLES]


def _int(v, hi: int) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= hi else None


def extract(articles: list[Article], llm: Gemini) -> list[dict]:
    """Ask the model to read the headlines (in batches). Returns one dict per article."""
    results = []
    for i in range(0, len(articles), BATCH):
        batch = articles[i:i + BATCH]
        prompt = "\n".join(f"{n}. {a.headline} ({a.source or 'unknown source'})" for n, a in enumerate(batch))
        reply = llm.generate_json(SYSTEM, "Headlines:\n" + prompt)
        items = reply.get("items", []) if isinstance(reply, dict) else reply
        by_id = {int(x.get("id", -1)): x for x in items if isinstance(x, dict) and str(x.get("id", "")).isdigit()}
        for n, a in enumerate(batch):
            x = by_id.get(n, {})
            try:
                conf = min(max(float(x.get("confidence") or 0), 0.0), 1.0)
            except (TypeError, ValueError):
                conf = 0.0
            results.append({
                "article": a, "relevant": bool(x.get("relevant")), "state": (x.get("state") or None),
                "district": (x.get("district") or None), "cases": _int(x.get("cases"), 100000),
                "deaths": _int(x.get("deaths"), 5000), "confidence": round(conf, 2),
                "summary": str(x.get("summary") or "")[:200] or None})
    return results


def match_district(state: str | None, district: str | None) -> int | None:
    """Census 2011 district code for the place named in the news, or None if unsure."""
    from denguewatch.ingestion.names import match_state
    if not district:
        return None
    canonical = match_state(state) if state else None
    m = _matcher().match(canonical, district)
    if m.census_code is None or m.method in ("statewide", "unmatched"):
        return None
    if m.method == "other-state" and canonical:     # named state disagrees: do not guess
        return None
    return int(m.census_code)


_MATCHER = None


def _matcher():
    global _MATCHER
    if _MATCHER is None:
        from denguewatch.ingestion.districts import DistrictMatcher
        _MATCHER = DistrictMatcher()
    return _MATCHER


def store(results: list[dict], engine: Engine) -> int:
    rows = []
    for r in results:
        a = r["article"]
        code = match_district(r["state"], r["district"]) if r["relevant"] else None
        rows.append({"url": a.url[:2000], "headline": a.headline[:500], "source": (a.source or "")[:120] or None,
                     "published_at": a.published_at, "relevant": r["relevant"], "state": (r["state"] or "")[:80] or None,
                     "district": (r["district"] or "")[:120] or None, "code": code, "cases": r["cases"],
                     "deaths": r["deaths"], "conf": r["confidence"], "summary": r["summary"],
                     "status": "unverified" if r["relevant"] else "not_relevant"})
    if not rows:
        return 0
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO news_signals (url, headline, source, published_at, is_relevant, state_name_raw,
                   district_name_raw, district_id, extracted_cases, extracted_deaths, confidence, summary,
                   verification_status)
            VALUES (:url, :headline, :source, :published_at, :relevant, :state, :district,
                    (SELECT district_id FROM districts WHERE census_code = :code), :cases, :deaths, :conf,
                    :summary, :status)
            ON CONFLICT (url) DO NOTHING"""), rows)
    return sum(r["relevant"] for r in rows)


def verify_against_idsp(engine: Engine) -> int:
    """Mark signals whose district had an IDSP-reported outbreak starting within 30 days of the news."""
    with engine.begin() as conn:
        return conn.execute(text("""
            UPDATE news_signals n SET verification_status = 'matches_idsp'
            WHERE n.is_relevant AND n.verification_status = 'unverified' AND n.district_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM outbreaks o
                          WHERE o.district_id = n.district_id
                            AND o.start_date BETWEEN n.published_at::date - 30 AND n.published_at::date + 30)
        """)).rowcount


def run(llm: Gemini | None = None, engine: Engine | None = None, articles: list[Article] | None = None) -> dict:
    """One pass of the news agent. Returns counts."""
    llm = llm or default_client()
    engine = engine or get_engine()
    if not llm.configured:
        return {"skipped": "no GEMINI_API_KEY in .env"}
    articles = fetch_articles() if articles is None else articles
    with engine.connect() as conn:
        known = {r[0] for r in conn.execute(text("SELECT url FROM news_signals WHERE url = ANY(:u)"),
                                            {"u": [a.url for a in articles]})}
    new = [a for a in articles if a.url not in known]
    results = extract(new, llm) if new else []
    relevant = store(results, engine)
    matched = verify_against_idsp(engine)
    return {"articles": len(articles), "new": len(new), "relevant": relevant, "newly_matched_idsp": matched}
