"""Central configuration loader for the AI Project Architect system."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base paths
PROJECT_ROOT = Path(__file__).parent.parent
SCHEMAS_DIR = PROJECT_ROOT / "config" / "schemas"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
OUTPUT_DIR = PROJECT_ROOT / "output"
ADVISORY_OUTPUT_DIR = OUTPUT_DIR / "advisory"
TMP_DIR = PROJECT_ROOT / "tmp"

# Environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")

# Schema file paths
PROJECT_STATE_SCHEMA = SCHEMAS_DIR / "project_state.schema.json"
OUTLINE_SCHEMA = SCHEMAS_DIR / "outline.schema.json"
CHAPTER_SCHEMA = SCHEMAS_DIR / "chapter.schema.json"
FEATURE_SCHEMA = SCHEMAS_DIR / "feature.schema.json"

# Pipeline phase order (for transition validation)
PHASE_ORDER = [
    "idea_intake",
    "feature_discovery",
    "outline_generation",
    "outline_approval",
    "chapter_build",
    "quality_gates",
    "final_assembly",
    "complete",
]

# Chapter build limits
MAX_CHAPTER_REVISIONS = 2

# LLM configuration (for dynamic ideation conversation)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
LLM_ENABLED = os.getenv("LLM_ENABLED", "true").lower() in ("true", "1", "yes")

# Google Calendar booking configuration
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "ali@colaberry.com")
GOOGLE_SERVICE_ACCOUNT_EMAIL = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_EMAIL",
    "calendar-booking@colaberryenterpriseai.iam.gserviceaccount.com",
)
GOOGLE_PRIVATE_KEY = os.getenv("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n")
CALENDAR_OWNER_EMAIL = os.getenv("GOOGLE_CALENDAR_OWNER_EMAIL", "ali@colaberry.com")
CALENDAR_SLOT_DURATION = int(os.getenv("CALENDAR_SLOT_DURATION", "30"))
CALENDAR_BUFFER_MINUTES = int(os.getenv("CALENDAR_BUFFER_MINUTES", "15"))
CALENDAR_BUSINESS_START = int(os.getenv("CALENDAR_BUSINESS_START", "9"))
CALENDAR_BUSINESS_END = int(os.getenv("CALENDAR_BUSINESS_END", "17"))
CALENDAR_ATTENDEES = [
    e.strip() for e in os.getenv("CALENDAR_ATTENDEES", "ali@colaberry.com").split(",") if e.strip()
]
