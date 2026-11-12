"""Central configuration, read from environment variables (and the .env file)."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Folders
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
SAMPLE_DIR = DATA_DIR / "sample"
MODELS_DIR = PROJECT_ROOT / "models"

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Security
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# Data sources
IDSP_ARCHIVE_URL = os.getenv(
    "IDSP_ARCHIVE_URL", "https://ncdc.mohfw.gov.in/includes/WeeklyOutbreaks.php"
)
OPEN_METEO_ARCHIVE_URL = os.getenv(
    "OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive"
)
OPEN_METEO_FORECAST_URL = os.getenv(
    "OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast"
)

# AI agent (Step 11) - Google Gemini API; the key lives only in .env
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "auto")   # "auto" = newest free Flash model available to the key
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")

# Back-up AI provider (Step 11): Groq, free tier, OpenAI-compatible API
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODELS = [m.strip() for m in os.getenv("GROQ_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b").split(",")
               if m.strip()]
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Web server
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"
PORT = int(os.getenv("PORT", "8050"))

# Modelling constants (see documentation, Section 6.7)
PREDICTION_HORIZON_WEEKS = 4
REPORTING_LAG_WEEKS = 8
