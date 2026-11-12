"""Test which Gemini models answer quickly with your API key, and save the best one in .env.

    python scripts/check_gemini.py

Free-tier models are sometimes overloaded (error 503) or slow. This sends a tiny request to each
Flash model, measures the time, and writes GEMINI_MODEL=<fastest reliable model> to .env.
"""

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from denguewatch import config  # noqa: E402
from denguewatch.agent.llm import Gemini, LLMError, parse_json, rank_models, _text  # noqa: E402


def try_model(llm: Gemini, name: str) -> tuple[str, float | None]:
    body = {"contents": [{"role": "user", "parts": [{"text": 'Reply with JSON {"ok": true}'}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    t0 = time.perf_counter()
    try:
        r = llm.session.post(f"{llm.base}/models/{name}:generateContent", headers=llm._headers(), json=body,
                             timeout=(10, 40))
    except requests.Timeout:
        return "too slow (>40 s)", None
    except requests.RequestException as exc:
        return f"not reachable ({type(exc).__name__})", None
    secs = time.perf_counter() - t0
    if r.status_code != 200:
        return {429: "quota used up (429)", 503: "overloaded (503)", 404: "not available (404)"}.get(
            r.status_code, f"error {r.status_code}"), None
    try:
        parse_json(_text(r.json()))
    except LLMError:
        return "bad reply", None
    return "OK", secs


def save_model(name: str) -> None:
    env = ROOT / ".env"
    lines = [ln for ln in env.read_text(encoding="utf-8").splitlines() if not re.match(r"\s*GEMINI_MODEL\s*=", ln)]
    env.write_text("\n".join(lines + [f"GEMINI_MODEL={name}"]).strip() + "\n", encoding="utf-8")


def main() -> int:
    llm = Gemini(model="auto")
    if not llm.configured:
        print("No GEMINI_API_KEY in .env - run setup_agent.bat first.")
        return 1
    try:
        names = rank_models(llm.available_models())
    except LLMError as exc:
        print(f"Could not reach Gemini with this key: {exc}")
        return 1
    print(f"Flash models for your key: {', '.join(names) or 'none'}\n")
    results = []
    for name in names[:6]:
        status, secs = try_model(llm, name)
        print(f"  {name:<32} {status}{f'  {secs:.1f} s' if secs else ''}")
        results.append((name, status, secs))
    ok = [(n, s) for n, st, s in results if s is not None]
    if not ok:
        print("\nNo model answered right now. Google's free models may be busy - try again in 10-15 minutes.")
        return 1
    # Fastest full Flash model if it is reasonably quick, otherwise the fastest model of all.
    full = [x for x in ok if "lite" not in x[0] and x[1] < 15]
    best = min(full or ok, key=lambda x: x[1])[0]
    save_model(best)
    print(f"\nSaved GEMINI_MODEL={best} in .env (back-ups are used automatically if it is busy).")
    print("Restart the app (start_app.bat) so it uses this model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
