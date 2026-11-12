"""Check that the development environment is ready.

    python scripts/check_setup.py

Checks the Python version, required packages, the .env file and the
PostgreSQL connection, and prints what to fix if anything is missing.
"""

import importlib
import importlib.metadata
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PACKAGES = {
    "requests": "requests",
    "pdfplumber": "pdfplumber",
    "pandas": "pandas",
    "numpy": "numpy",
    "rapidfuzz": "rapidfuzz",
    "sqlalchemy": "SQLAlchemy",
    "psycopg": "psycopg",
    "flask": "Flask",
    "dash": "dash",
    "plotly": "plotly",
    "sklearn": "scikit-learn",
    "lightgbm": "lightgbm",
    "joblib": "joblib",
    "sqlglot": "sqlglot",
    "apscheduler": "APScheduler",
    "dotenv": "python-dotenv",
    "pytest": "pytest",
}

OK, FAIL = "[ OK ]", "[FAIL]"


def main() -> int:
    problems = 0

    print("\n1. Python version")
    if sys.version_info >= (3, 11):
        print(f"  {OK} Python {sys.version.split()[0]}")
    else:
        problems += 1
        print(f"  {FAIL} Python {sys.version.split()[0]} found; 3.11 or newer is required.")

    print("\n2. Python packages")
    for module, name in PACKAGES.items():
        try:
            importlib.import_module(module)
            print(f"  {OK} {name} {importlib.metadata.version(name)}")
        except ImportError:
            problems += 1
            print(f"  {FAIL} {name} is missing. Run: pip install -r requirements.txt")

    print("\n3. Configuration file")
    if (ROOT / ".env").exists():
        print(f"  {OK} .env found")
    else:
        problems += 1
        print(f"  {FAIL} .env not found. Copy .env.example to .env and set your database password.")

    print("\n4. PostgreSQL connection")
    try:
        from denguewatch.db.connection import check_connection

        ok, message = check_connection()
    except Exception as exc:  # noqa: BLE001
        ok, message = False, str(exc)
    if ok:
        print(f"  {OK} Connected: {message}")
    else:
        problems += 1
        print(f"  {FAIL} Could not connect: {message}")
        print("         Check that PostgreSQL is running and DATABASE_URL in .env is correct.")

    print("\n" + ("All checks passed. You are ready for Step 4." if problems == 0
                  else f"{problems} problem(s) found. Fix them and run this script again."))
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
