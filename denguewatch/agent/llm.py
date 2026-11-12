"""Small client for the Google Gemini REST API (no SDK needed, only `requests`).

The API key is read from .env (GEMINI_API_KEY) and sent in a header, never in the URL.
With GEMINI_MODEL=auto the newest Flash model the key can use is picked automatically,
because Google renames and retires models often.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from functools import lru_cache

import requests

from denguewatch import config

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """The language model could not be reached or gave an unusable answer."""


class NotConfigured(LLMError):
    """No GEMINI_API_KEY in .env."""


SKIP = re.compile(r"live|tts|image|audio|embed|preview|exp|thinking|native|robotics|computer", re.I)


def _version(name: str) -> tuple:
    nums = re.findall(r"\d+(?:\.\d+)?", name)
    return tuple(float(n) for n in nums[:1]) or (0.0,)


def rank_models(names: list[str]) -> list[str]:
    """Usable Flash models, best first: newest stable version, full Flash before Flash-Lite."""
    flash = [n for n in names if "flash" in n and not SKIP.search(n)]
    if not flash:
        flash = [n for n in names if "flash" in n and "live" not in n and "tts" not in n and "image" not in n]
    return sorted(flash, key=lambda n: (_version(n), "lite" not in n, -len(n)), reverse=True)


def pick_model(names: list[str]) -> str:
    ranked = rank_models(names)
    if not ranked:
        raise LLMError("No Gemini Flash model is available to this API key")
    return ranked[0]


# Models that hit a quota limit are skipped for a while (shared by all clients in the app).
# Free-tier limits are per model, so moving to another model usually works.
_cooldown: dict[str, float] = {}
_cool_lock = threading.Lock()


def _cool(name: str, seconds: float) -> None:
    with _cool_lock:
        _cooldown[name] = time.monotonic() + seconds


def _cooling(name: str) -> bool:
    with _cool_lock:
        return _cooldown.get(name, 0) > time.monotonic()


def _daily_quota(resp) -> bool:
    """True if a 429 is the per-day free-tier limit (not the per-minute one)."""
    return "PerDay" in (getattr(resp, "text", "") or "")


class Gemini:
    """Gemini client. If a model is overloaded (503) or too slow, the next Flash model is tried."""

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None,
                 timeout: float = 40.0, max_models: int = 8):
        self.api_key = api_key if api_key is not None else config.GEMINI_API_KEY
        self.base = (base_url or config.GEMINI_BASE_URL).rstrip("/")
        self._model = model or config.GEMINI_MODEL
        self.timeout = timeout                      # seconds to wait for one answer
        self.max_models = max_models
        self._fallbacks: list[str] | None = None
        self.last_model: str | None = None
        self.session = requests.Session()
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        if not self.api_key:
            raise NotConfigured("GEMINI_API_KEY is not set - run setup_agent.bat")
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def available_models(self) -> list[str]:
        resp = self.session.get(f"{self.base}/models", headers=self._headers(), params={"pageSize": 200},
                                timeout=(10, 30))
        if resp.status_code != 200:
            raise LLMError(f"Could not list Gemini models ({resp.status_code}): {resp.text[:200]}")
        return [m["name"].split("/", 1)[-1] for m in resp.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]

    def _candidates(self) -> list[str]:
        """The configured (or best) model first, then other Flash models as back-ups (minus those on cool-down)."""
        return [m for m in self._all_candidates() if not _cooling(m)]

    def _all_candidates(self) -> list[str]:
        with self._lock:
            if self._fallbacks is None:
                try:
                    ranked = rank_models(self.available_models())
                except LLMError:
                    if self._model and self._model != "auto":
                        ranked = []
                    else:
                        raise
                first = [self._model] if self._model and self._model != "auto" else []
                self._fallbacks = (first + [m for m in ranked if m not in first])[: self.max_models]
                if not self._fallbacks:
                    raise LLMError("No Gemini Flash model is available to this API key")
                log.info("Gemini models to use: %s", ", ".join(self._fallbacks))
            return list(self._fallbacks)

    @property
    def model(self) -> str:
        return (self._candidates() or self._all_candidates())[0]

    def _promote(self, name: str) -> None:
        """Remember a model that worked, so the next request starts with it."""
        with self._lock:
            if self._fallbacks and self._fallbacks[0] != name:
                self._fallbacks.remove(name)
                self._fallbacks.insert(0, name)

    def generate_json(self, system: str, prompt: str, retries: int = 2, quick: bool = False) -> dict | list:
        """Send one prompt and return the model's reply parsed as JSON.

        quick=True: one try on one model only (used when a back-up provider is waiting), so a busy
        or used-up Gemini costs seconds, not minutes."""
        if quick:
            retries = 1
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        problems = []
        quota_hit = False
        candidates = self._candidates()
        for name in candidates[:1] if quick else candidates:
            url = f"{self.base}/models/{name}:generateContent"
            for attempt in range(1, retries + 1):
                try:
                    resp = self.session.post(url, headers=self._headers(), json=body,
                                             timeout=(10, min(self.timeout, 20) if quick else self.timeout))
                except requests.Timeout:
                    _cool(name, 300)                            # slow right now: rest it for 5 minutes
                    problems.append(f"{name}: no answer in time")
                    break                                   # slow model: go to the next one
                except requests.RequestException as exc:
                    problems.append(f"{name}: {type(exc).__name__}")
                    if attempt == retries:
                        break
                    time.sleep(2)
                    continue
                if resp.status_code == 200:
                    self._promote(name)
                    self.last_model = name
                    return parse_json(_text(resp.json()))
                if resp.status_code == 429:
                    quota_hit = True
                    problems.append(f"{name}: quota (429)")
                    if _daily_quota(resp):
                        _cool(name, 6 * 3600)                  # used up for today: skip this model
                        log.warning("Gemini %s: daily free quota used up - using another model", name)
                        break
                    wait = _retry_delay(resp) or 20
                    if wait <= 30 and attempt < retries:       # per-minute limit: a short wait is enough
                        time.sleep(wait)
                        continue
                    _cool(name, max(wait, 60))
                    break                                   # each model has its own free quota
                if resp.status_code in (500, 503, 504):
                    problems.append(f"{name}: busy ({resp.status_code})")
                    _cool(name, 300)                            # overloaded: rest it for 5 minutes
                    if attempt < retries:
                        log.warning("Gemini %s busy (%s) - retrying", name, resp.status_code)
                        time.sleep(3)
                        continue
                    break
                if resp.status_code == 404:
                    _cool(name, 24 * 3600)                      # listed but not usable with this key
                    problems.append(f"{name}: not available (404)")
                    break
                raise LLMError(f"Gemini error {resp.status_code}: {resp.text[:300]}")
            log.warning("Gemini model %s failed - trying the next one", name)
        if quota_hit or not problems:
            raise LLMError("The free Gemini quota is used up for now - try again in a minute (or tomorrow)")
        raise LLMError("Gemini is busy or not answering right now (" + "; ".join(problems[-3:]) + "). "
                       "Try again in a few minutes, or run check_gemini.bat to pick a faster model.")


def _text(payload: dict) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError) as exc:
        reason = payload.get("promptFeedback", {}).get("blockReason") or payload.get("candidates", [{}])[0].get("finishReason")
        raise LLMError(f"Gemini returned no text ({reason})") from exc
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _retry_delay(resp) -> float | None:
    try:
        for d in resp.json()["error"].get("details", []):
            if "retryDelay" in d:
                return float(str(d["retryDelay"]).rstrip("s"))
    except Exception:  # noqa: BLE001
        return None
    return None


def parse_json(text: str):
    """JSON from the model, tolerating ```json fences or text around it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    raise LLMError(f"Gemini did not return valid JSON: {text[:200]}")


class Groq:
    """Back-up provider: Groq's free tier through its OpenAI-compatible chat API."""

    name = "groq"

    def __init__(self, api_key: str | None = None, models: list[str] | None = None, base_url: str | None = None,
                 timeout: float = 40.0):
        self.api_key = api_key if api_key is not None else config.GROQ_API_KEY
        self.models = models or config.GROQ_MODELS
        self.base = (base_url or config.GROQ_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.last_model: str | None = None
        self.session = requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def model(self) -> str:
        return self.models[0]

    def generate_json(self, system: str, prompt: str) -> dict | list:
        if not self.api_key:
            raise NotConfigured("GROQ_API_KEY is not set")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        problems, quota_hit = [], False
        for name in [m for m in self.models if not _cooling("groq:" + m)]:
            body = {"model": name, "temperature": 0,
                    "messages": [{"role": "system", "content": system + "\nReply with JSON only."},
                                 {"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"}}
            if "gpt-oss" in name:
                body["reasoning_effort"] = "low"          # faster answers, fewer tokens from the free quota
            for attempt in (1, 2):
                try:
                    resp = self.session.post(f"{self.base}/chat/completions", headers=headers, json=body,
                                             timeout=(10, self.timeout))
                except requests.RequestException as exc:
                    problems.append(f"{name}: {type(exc).__name__}")
                    break
                if resp.status_code == 200:
                    try:
                        content = resp.json()["choices"][0]["message"]["content"] or ""
                    except (KeyError, IndexError, ValueError) as exc:
                        raise LLMError("Groq returned an unexpected reply") from exc
                    self.last_model = name
                    return parse_json(content)
                if resp.status_code == 400 and attempt == 1 and ("response_format" in body or "reasoning_effort" in body):
                    body.pop("response_format", None)        # some models lack JSON mode: ask in plain text
                    body.pop("reasoning_effort", None)
                    continue
                if resp.status_code == 429:
                    quota_hit = True
                    wait = float(resp.headers.get("retry-after", 0) or 0)
                    problems.append(f"{name}: quota (429)")
                    if 0 < wait <= 20 and attempt == 1:
                        time.sleep(wait)
                        continue
                    _cool("groq:" + name, 6 * 3600 if "per day" in resp.text.lower() else max(wait, 60))
                    break
                if resp.status_code in (500, 502, 503, 504):
                    problems.append(f"{name}: busy ({resp.status_code})")
                    if attempt == 1:
                        time.sleep(2)
                        continue
                    break
                problems.append(f"{name}: error {resp.status_code}")
                break
        if quota_hit:
            raise LLMError("The free Groq quota is used up for now")
        raise LLMError("Groq is busy or not answering (" + "; ".join(problems[-3:]) + ")")


class FallbackLLM:
    """Try each configured provider in order (Gemini first, then Groq)."""

    def __init__(self, providers: list):
        self.providers = providers
        self.last_model: str | None = None

    @property
    def configured(self) -> bool:
        return any(p.configured for p in self.providers)

    @property
    def model(self) -> str:
        return " -> ".join(f"{type(p).__name__}:{p.model}" for p in self.providers if p.configured)

    def generate_json(self, system: str, prompt: str):
        """Round 1: one quick try per provider (Gemini's best model, then Groq).
        Round 2, only if both failed: Gemini's remaining models with normal retries."""
        errors, tried = [], False
        live = [p for p in self.providers if p.configured]
        for rnd in (1, 2):
            for i, p in enumerate(live):
                is_last = i == len(live) - 1
                if rnd == 2 and (is_last or not isinstance(p, Gemini)):
                    continue
                try:
                    tried = True
                    if isinstance(p, Gemini):
                        out = p.generate_json(system, prompt, quick=(rnd == 1 and not is_last))
                    else:
                        out = p.generate_json(system, prompt)
                    self.last_model = f"{type(p).__name__.lower()}/{p.last_model}"
                    return out
                except NotConfigured:
                    continue
                except LLMError as exc:
                    log.warning("%s failed (%s) - switching provider", type(p).__name__, str(exc)[:120])
                    errors.append(str(exc))
        if not tried:
            raise NotConfigured("No AI key set - run setup_agent.bat")
        quota = all("quota" in e.lower() for e in errors)
        raise LLMError(("The free AI quota is used up for now - try again later" if quota else
                        "The AI services are busy right now - try again in a few minutes") +
                       " (" + " | ".join(errors)[:300] + ")")


@lru_cache(maxsize=1)
def default_client() -> FallbackLLM:
    return FallbackLLM([Gemini(), Groq()])
