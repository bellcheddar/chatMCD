"""Environment-driven configuration for the chatMCD Flask app.

The Flask app is the only thing that holds the Hugging Face token. Nothing here
is ever serialised to a template or an API response.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str) -> list[str]:
    # An empty or whitespace-only value falls back to the default rather than
    # producing an empty list. A blank ALLOWED_ORIGINS or FRAME_ANCESTORS in a
    # .env would otherwise silently disable the protection it configures.
    raw = os.environ.get(name) or ""
    if not raw.strip():
        raw = default
    return [x.strip() for x in raw.split(",") if x.strip()]


class Config:
    # ---- Hugging Face -----------------------------------------------------
    # Never rendered into a page, never returned by an endpoint.
    HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    HF_SPACE_ID = os.environ.get("HF_SPACE_ID", "Dellboy/chatmcd-api")
    HF_TIMEOUT = float(os.environ.get("HF_TIMEOUT", "180"))

    # ZeroGPU sleeps aggressively. Ping the Space on this interval so a recruiter
    # does not land on a 40-second cold start.
    KEEPWARM_SECONDS = int(os.environ.get("KEEPWARM_SECONDS", "600"))
    KEEPWARM_ENABLED = _bool("KEEPWARM_ENABLED", True)

    # ---- Generation defaults ---------------------------------------------
    TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.7"))
    TOP_P = float(os.environ.get("TOP_P", "0.9"))
    REPETITION_PENALTY = float(os.environ.get("REPETITION_PENALTY", "1.05"))
    MAX_NEW_TOKENS = int(os.environ.get("MAX_NEW_TOKENS", "512"))
    MAX_HISTORY_TURNS = int(os.environ.get("MAX_HISTORY_TURNS", "8"))
    MAX_MESSAGE_CHARS = int(os.environ.get("MAX_MESSAGE_CHARS", "2000"))

    # ---- Web --------------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-a-secret")
    RATE_LIMIT = os.environ.get("RATE_LIMIT", "20 per minute")
    RATE_LIMIT_STORAGE = os.environ.get("RATE_LIMIT_STORAGE", "memory://")

    # Origins allowed to call the API and to frame /embed.
    ALLOWED_ORIGINS = _list(
        "ALLOWED_ORIGINS",
        "https://marcdeller.com,https://www.marcdeller.com,"
        "https://mdeller.com,https://www.mdeller.com,"
        "https://chatmcd.mdeller.com",
    )
    # Frame-ancestors additionally permits the wildcard subdomains.
    #
    # CSP keywords must be single-quoted ('self', 'none'). systemd's
    # EnvironmentFile strips surrounding quotes, so FRAME_ANCESTORS='self',...
    # in a .env arrives as bare `self` and the whole directive is invalid — which
    # silently breaks framing from marcdeller.com. Re-quote any bare keyword here
    # rather than depending on how the value survived the environment.
    FRAME_ANCESTORS = [
        f"'{x}'" if x in {"self", "none"} else x
        for x in _list(
            "FRAME_ANCESTORS",
            "self,https://marcdeller.com,https://*.marcdeller.com,"
            "https://mdeller.com,https://*.mdeller.com",
        )
    ]

    # ---- Feedback / logging ----------------------------------------------
    DB_PATH = os.environ.get("DB_PATH", str(REPO_ROOT / "var" / "chatmcd.sqlite"))
    LOG_QUESTIONS = _bool("LOG_QUESTIONS", True)

    VERSION = os.environ.get("APP_VERSION", "0.1.0")


# The five preset chips, served from /api/presets so no front end hardcodes them.
# Three rows' worth on a desktop, and they wrap to as many as they need on a
# phone. Deliberately mixed: a visitor who only ever sees the serious ones learns
# what Marc has done, and one who taps a light-hearted one learns what he is like.
# Row 1 is the professional opener, row 2 the substance, row 3 the character.
PRESETS = [
    # -- the opener ---------------------------------------------------------
    {"id": "resume", "label": "Marc's quick résumé",
     "prompt": "Give me Marc's quick résumé."},
    {"id": "pubs", "label": "Publications & structures",
     "prompt": "Tell me about Marc's publications and protein structures."},
    {"id": "elora", "label": "What is Elora Therapeutics?",
     "prompt": "What is Elora Therapeutics?"},
    {"id": "leadership", "label": "Leadership style",
     "prompt": "How would you describe Marc's leadership style?"},
    {"id": "hire", "label": "Why hire Marc?",
     "prompt": "What would Marc bring to a drug discovery team, and what is he "
               "best at?"},

    # -- the substance ------------------------------------------------------
    {"id": "career", "label": "Career so far",
     "prompt": "Walk me through Marc's career, company by company, and what he "
               "did at each."},
    {"id": "expertise", "label": "Technical expertise",
     "prompt": "What are Marc's main technical skills and methods?"},
    {"id": "patents", "label": "Patents & INDs",
     "prompt": "What patents is Marc named on, and what has he taken into the "
               "clinic?"},
    {"id": "ai", "label": "AI in structural biology",
     "prompt": "What is Marc's view on AI and machine learning in structural "
               "biology and drug discovery?"},
    {"id": "plastic", "label": "Plastic-eating enzymes",
     "prompt": "Explain the plastic-degrading enzyme work Marc is doing, and why "
               "it matters for human health."},

    # -- the character ------------------------------------------------------
    {"id": "fun", "label": "Fun facts about Marc",
     "prompt": "Tell me some fun facts about Marc."},
    {"id": "boltzmaker", "label": "Why 'BoltzMaker'?",
     "prompt": "Why is Marc's tool called BoltzMaker?"},
    {"id": "apps", "label": "What has he built?",
     "prompt": "What apps and software has Marc built, and what does each one do?"},
    {"id": "blog", "label": "What does he write about?",
     "prompt": "What kinds of things does Marc write about on his blog?"},
    {"id": "sixcs", "label": "The 6 C's",
     "prompt": "What are Marc's 6 C's, and what does each one mean to him?"},
]

# How many of the above the compact widget shows. Its chip strip is two rows
# tall, so the rest would be rendered and then hidden by CSS rather than simply
# not offered.
EMBED_PRESETS = 5

# Header link row.
#
# RESUME_URL is the July 2026 PDF, which is the newest one actually published on
# marcdeller.com (found in training/corpus/web). The build plan asks for the
# September 2026 PDF; when that is uploaded, set RESUME_URL in the environment
# rather than editing this file.
RESUME_URL = os.environ.get(
    "RESUME_URL",
    "https://marcdeller.com/wp-content/uploads/2026/07/marc_deller_resume_july_2026.pdf",
)

LINKS = [
    {"label": "mdeller.com", "href": "https://mdeller.com", "primary": False},
    {"label": "marcdeller.com", "href": "https://marcdeller.com", "primary": False},
    {"label": "LinkedIn", "href": "https://www.linkedin.com/in/marccdeller/", "primary": False},
    {"label": "ORCID", "href": "https://orcid.org/0000-0001-8070-6502", "primary": False},
    {"label": "Résumé", "href": RESUME_URL, "primary": True},
]

GREETING = (
    "Hello! I'm chatMCD — I answer questions about Marc Deller from his own papers, "
    "patents, thesis and notes. Ask me about his research, his structures, or why he "
    "named a tool after a beer."
)
