"""Settings. Secrets come from .env in the repo root (git-ignored), never from code."""
import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ENV = ROOT / ".env"
DB = DATA / "audit.db"

GRADE_MODEL = "google/gemini-3.6-flash"          # grader (OpenRouter)
FIX_MODEL = "google/gemini-3.7-flash"            # proposes fixes, persona, category (never the grader)
NAME_MODEL = "google/gemini-3.7-flash"           # names problem groups
EMBED_MODEL = "openai/text-embedding-3-small"    # if the gateway exposes it; TF-IDF fallback


def _env(key, default=None):
    if ENV.exists():
        m = re.search(rf'^{key}\s*=\s*["\']?([^"\'\s]+)', ENV.read_text(errors="ignore"), re.M)
        if m:
            return m.group(1)
    return os.environ.get(key, default)


def creds():
    base, key = _env("LLM_BASE_URL"), _env("LLM_API_KEY")
    if not (base and key):
        raise SystemExit("LLM_BASE_URL / LLM_API_KEY missing in .env")
    return base.rstrip("/"), key


SARVAM_API_KEY = _env("SARVAM_API_KEY")
SARVAM_BASE = _env("SARVAM_BASE", "https://api.sarvam.ai")
SARVAM_AGENT_ID = _env("SARVAM_AGENT_ID")
PUBLIC_URL = _env("PUBLIC_URL", "http://localhost:8800")   # what Sarvam calls for tools
