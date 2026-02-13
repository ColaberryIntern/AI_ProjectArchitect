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
