"""Step 11: set up and test the AI agent.

    python scripts/setup_agent.py            # ask for the Gemini API key, save it in .env, test
    python scripts/setup_agent.py --groq     # add the back-up Groq key (free: https://console.groq.com/keys)
    python scripts/setup_agent.py --test     # only test (keys already in .env)

Get a free key at https://aistudio.google.com/apikey (sign in with Google -> "Create API key").
The key is stored only in .env, which Git ignores, so it is never uploaded.
"""

import argparse
import logging
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def save_key(key: str, var: str = "GEMINI_API_KEY") -> None:
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    lines = [ln for ln in lines if not re.match(rf"\s*{var}\s*=", ln)]
    lines += ["", "# AI agent (Step 11) - keep this secret", f"{var}={key}"]
    env.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    print("Saved the key in .env (not uploaded to GitHub).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test", action="store_true", help="skip entering a key; test the ones in .env")
    parser.add_argument("--groq", action="store_true", help="enter the back-up Groq API key")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    if not args.test:
        var, site = (("GROQ_API_KEY", "https://console.groq.com/keys") if args.groq
                     else ("GEMINI_API_KEY", "https://aistudio.google.com/apikey"))
        print(f"Get a free key at {site}")
        print("Paste it here (right-click, or Ctrl+V) and press Enter. The screen is cleared afterwards.")
        # getpass() blocks pasting in the Windows console, so a normal prompt is used and then cleared.
        key = input("API key: ").strip().strip('"').strip("'")
        os.system("cls" if os.name == "nt" else "clear")
        if not re.fullmatch(r"[A-Za-z0-9_\-]{20,100}", key):
            print("That does not look like an API key (expected 20-100 letters/digits). Nothing saved.")
            return 1
        save_key(key, var)

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
    import importlib

    from denguewatch import config
    importlib.reload(config)
    from denguewatch.agent.llm import FallbackLLM, Gemini, Groq
    from denguewatch.db.loader import create_schema

    print("\n1. Database: adding the Step 11 tables (agent_queries, news columns, v_risk_latest) ...")
    create_schema()
    print("   OK")

    print("2. AI providers ...")
    gemini = Gemini(api_key=config.GEMINI_API_KEY, model=config.GEMINI_MODEL)
    groq = Groq(api_key=config.GROQ_API_KEY, models=config.GROQ_MODELS)
    working = 0
    for label, p in (("Gemini (main)", gemini), ("Groq (back-up)", groq)):
        if not p.configured:
            print(f"   {label:<15} no key in .env" + ("  (optional: setup_agent.bat --groq)" if p is groq else ""))
            continue
        try:
            p.generate_json("You reply with JSON.", 'Reply with {"ok": true}')
            print(f"   {label:<15} OK - {p.last_model}")
            working += 1
        except Exception as exc:  # noqa: BLE001
            print(f"   {label:<15} FAILED: {str(exc)[:150]}")
    if not working:
        print("   No AI provider is working - check the keys, or try again later (free quotas).")
        return 1
    llm = FallbackLLM([gemini, groq])

    print("3. Test question ...")
    from denguewatch.agent.sql_agent import ask
    res = ask("How many dengue outbreaks were reported in Kerala in 2025?", llm=llm)
    print(f"   Status: {res['status']}  ({res.get('duration_ms', 0) / 1000:.1f} s, {res.get('model')})")
    print(f"   SQL:    {res['sql']}")
    print(f"   Answer: {res['answer']}")
    if res["status"] != "answered":
        return 1

    print("4. News agent: reading today's dengue news ...")
    from denguewatch.agent import news_agent
    from denguewatch.db.loader import pipeline_run
    try:
        with pipeline_run("news_signals") as run:          # logged like the scheduled job
            out = news_agent.run(llm=llm)
            run["rows"] = out.get("relevant", 0)
        print(f"   OK - {out['new']} new articles, {out['relevant']} relevant, "
              f"{out['newly_matched_idsp']} matched IDSP")
    except Exception as exc:  # noqa: BLE001
        print(f"   News agent failed (the Ask feature still works): {exc}")
    print("\nAll set. Start the app (start_app.bat) and open the 'Ask AI' tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
