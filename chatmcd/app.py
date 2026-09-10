"""chatMCD — the public API and the two front ends.

This process is the only thing that holds the Hugging Face token. Browsers talk
to it; it talks to the ZeroGPU Space.

    GET  /              full chat app
    GET  /embed         compact widget, framable from marcdeller.com / mdeller.com
    POST /api/chat      {message, history} -> SSE token stream
    GET  /api/presets   preset prompt list
    GET  /api/health    warm state + version
    POST /api/feedback  thumbs up/down on an answer
    GET  /llms.txt      what this page is, for crawlers
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

from flask import (Flask, Response, abort, g, jsonify, render_template, request,
                   send_from_directory, stream_with_context)

from .config import EMBED_PRESETS, GREETING, LINKS, PRESETS, Config
from .hf_client import KeepWarm, MockClient, SpaceClient

log = logging.getLogger("chatmcd")

# Scrub anything that looks like contact detail out of the question log. The log
# exists to find gaps in the training data, not to keep records about visitors.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def scrub(text: str) -> str:
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text))


# Phrases that mean the bot declined. Used only to flag a row in the digest, so
# Marc can see at a glance which questions the writing does not yet cover.
DECLINED = re.compile(
    r"\b(?:don'?t|do not|doesn'?t)\s+(?:have|know|hold)\b"
    r"|\bnot something I\b|\bisn'?t something\b|\boutside (?:what|the|my)\b"
    r"|\bno (?:record|information|mention|detail)\b|\bnot covered\b"
    r"|\bI'?m afraid\b|\bcan'?t (?:say|help|provide)\b", re.I)


def client_ip() -> str:
    """The visitor's address, not nginx's.

    Everything reaches Flask through the local proxy, so remote_addr is always
    127.0.0.1 and logging it would give a database full of one address. nginx
    sets both headers (see deploy/nginx-chatmcd.conf); X-Forwarded-For is a
    chain, and the client is its FIRST entry, with any downstream proxy appended
    after it. Trusting the last entry instead is the classic way to log your own
    load balancer.
    """
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()[:45]
    return (request.headers.get("X-Real-IP") or request.remote_addr or "")[:45]


# --------------------------------------------------------------------- storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id         INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    source     TEXT NOT NULL,          -- app | iframe | shortcode | api
    question   TEXT NOT NULL,
    turn       INTEGER NOT NULL DEFAULT 0,
    said_idk   INTEGER NOT NULL DEFAULT 0,
    ip         TEXT,                   -- resolved to a country only in the digest
    user_agent TEXT,
    answer     TEXT,                   -- written back when the stream finishes
    latency    REAL,                   -- seconds to the last token
    ref        TEXT                    -- referring page, for the embedded widget
);
CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    source     TEXT NOT NULL,
    question   TEXT NOT NULL,
    vote       INTEGER NOT NULL,       -- +1 / -1
    note       TEXT
);
CREATE INDEX IF NOT EXISTS questions_ts ON questions(ts);
CREATE INDEX IF NOT EXISTS feedback_ts  ON feedback(ts);
"""


# Columns added after the table already existed in production. SQLite's
# ADD COLUMN is cheap and non-destructive, but it errors if the column is
# already there, so each one is attempted individually and its "duplicate
# column" is ignored. A fresh database gets them from SCHEMA above and skips
# every one of these.
MIGRATIONS = [
    ("questions", "ip", "TEXT"),
    ("questions", "user_agent", "TEXT"),
    ("questions", "answer", "TEXT"),
    ("questions", "latency", "REAL"),
    ("questions", "ref", "TEXT"),
]


def migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
    conn.commit()


def connect(path_str: str) -> sqlite3.Connection:
    """Open the question log, creating and migrating it as needed.

    Separate from db() because the SSE generator finishes *after* the request
    has ended, so it cannot use the request-scoped connection to write back the
    answer it just streamed.
    """
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


def db(app: Flask) -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(app.config["DB_PATH"])
    return g.db


# ------------------------------------------------------------------- app factory


def create_app(config_object: type = Config) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(config_object)
    # gunicorn's LOG_LEVEL convention is lowercase ("info"); Python's logging
    # requires uppercase and raises ValueError otherwise. The same variable feeds
    # both, so normalise here rather than requiring two spellings in .env — this
    # crashed every worker on first deploy while systemctl still reported active.
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}:
        level = "INFO"
    logging.basicConfig(level=level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    mock = os.environ.get("MOCK_SPACE", "0") in {"1", "true", "on"}
    # The quota state lives beside the question log, so a restart does not show
    # a green light on a Space that still has no GPU allowance.
    space = MockClient() if mock else SpaceClient(
        app.config["HF_SPACE_ID"], app.config["HF_TOKEN"], app.config["HF_TIMEOUT"],
        state_path=str(Path(app.config["DB_PATH"]).with_name("space-state.json")))
    if mock:
        log.warning("MOCK_SPACE=1 — answers are canned, no model is being called")
    app.extensions["space"] = space
    if not mock and app.config["KEEPWARM_ENABLED"] and app.config["HF_SPACE_ID"]:
        KeepWarm(space, app.config["KEEPWARM_SECONDS"]).start()

    _install_limiter(app)
    _install_security_headers(app)
    _install_asset_versioning(app)
    _register_routes(app)

    @app.teardown_appcontext
    def _close(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    return app


def _install_limiter(app: Flask) -> None:
    try:
        from flask_limiter import Limiter
        from flask_limiter.util import get_remote_address
    except ImportError:  # dev without the extra installed
        log.warning("flask-limiter not installed; rate limiting is OFF")
        app.extensions["limiter"] = None
        return
    limiter = Limiter(get_remote_address, app=app,
                      storage_uri=app.config["RATE_LIMIT_STORAGE"],
                      default_limits=[], headers_enabled=True)
    app.extensions["limiter"] = limiter


def _install_security_headers(app: Flask) -> None:
    allowed = set(app.config["ALLOWED_ORIGINS"])
    frame_ancestors = " ".join(app.config["FRAME_ANCESTORS"])

    @app.before_request
    def _nonce():
        # One inline script per page sets the theme before first paint, so the
        # page never flashes the wrong palette. It gets a nonce rather than
        # opening script-src to 'unsafe-inline'.
        g.csp_nonce = uuid.uuid4().hex

    @app.template_global()
    def csp_nonce() -> str:
        return getattr(g, "csp_nonce", "")

    @app.after_request
    def _headers(resp: Response) -> Response:
        origin = request.headers.get("Origin")
        if origin and origin in allowed:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            f"script-src 'self' 'nonce-{getattr(g, 'csp_nonce', '')}'; "
            "connect-src 'self'; "
            f"frame-ancestors {frame_ancestors}"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Flask sends no Cache-Control on a rendered template, and heuristic
        # caching then pins the old ?v= asset URLs so a deploy is invisible.
        if resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


def _install_asset_versioning(app: Flask) -> None:
    """Stamp every static URL with the file's mtime so a deploy is never
    invisible behind an immutable cache."""
    root = Path(app.static_folder)

    @app.template_global()
    def asset(filename: str) -> str:
        f = root / filename
        v = int(f.stat().st_mtime) if f.exists() else 0
        return f"/static/{filename}?v={v}"


# ----------------------------------------------------------------------- routes


def _register_routes(app: Flask) -> None:
    space: SpaceClient = app.extensions["space"]
    limiter = app.extensions["limiter"]

    def limit(rule):
        return limiter.limit(rule) if limiter else (lambda f: f)

    def page_ctx(**extra):
        # ?theme= is how an embedding page (the WordPress shortcode's `theme`
        # attribute) picks a palette. "auto" follows the visitor's system
        # setting; anything unrecognised falls through to the light default.
        theme = request.args.get("theme", "").lower()
        # The widget gets a SHORT list, not the full one clipped by CSS. The
        # embed styles cap the chip strip at two rows with overflow:hidden, so
        # rendering all fifteen there would leave ten of them in the DOM,
        # focusable by keyboard and invisible to everyone: the same bug as an
        # actions bar sitting below the fold. Fewer chips is the honest fix.
        n = len(PRESETS) if extra.get("mode") == "app" else EMBED_PRESETS
        return dict(links=LINKS, presets=PRESETS[:n], greeting=GREETING,
                    version=app.config["VERSION"],
                    forced_theme=theme if theme in {"light", "dark", "auto"} else "",
                    **extra)

    @app.get("/")
    def index():
        return render_template("index.html", **page_ctx(mode="app"))

    @app.get("/embed")
    def embed():
        return render_template("embed.html", **page_ctx(mode="embed"))

    @app.get("/embed/preview")
    def embed_preview():
        """The widget framed at 320 / 380 / 420 px with the postMessage height
        listener wired. This is how the widget gets reviewed and screenshotted:
        a headless browser clamps its viewport to 500 px, so the only honest way
        to see the widget at its real width is inside an iframe."""
        return render_template("embed_preview.html", **page_ctx(mode="preview"))

    @app.get("/api/presets")
    def presets():
        return jsonify({"presets": PRESETS, "greeting": GREETING})

    @app.get("/api/health")
    def health():
        # The status dot in the header reads this. It is deliberately cheap: the
        # client answers from what it last observed and falls back to a cached
        # handshake, so an open tab polling every 45 seconds never touches a GPU.
        h = space.health()
        return jsonify({
            "ok": h["state"] != "down",
            "status": h["state"],          # ok | degraded | down
            "reason": h.get("reason", ""),  # quota | error | unreachable | busy | cold
            "detail": h["detail"],
            "version": app.config["VERSION"],
            "space": app.config["HF_SPACE_ID"],
            "warm": h["warm"],
            # No "rag" field. It used to report this process's own RAG_ENABLED,
            # which nothing sets here, so health said retrieval was off while
            # retrieval was the entire architecture. The Flask app cannot see
            # the Space's configuration, and a health check that guesses is
            # worse than one that stays quiet.
        })

    @app.get("/llms.txt")
    def llms_txt():
        body = (
            "# chatMCD\n\n"
            "> A language model that answers from the papers, patents, thesis and "
            "notes of Marc C. Deller, D.Phil., a structural biologist and drug-discovery "
            "leader. It answers questions about Marc in the third person, and says so "
            "rather than guessing when a question is outside what it holds.\n\n"
            "- Person: Marc C. Deller, D.Phil.\n"
            "- Roles: Co-Founder and CSO, Elora Therapeutics; advisor, DeepCovalent\n"
            "- Previously: Incyte, Stanford, Scripps (JCSG), Pfizer, Yale, Oxford\n"
            "- Home page: https://marcdeller.com\n"
            "- Apps: https://mdeller.com\n"
            "- Contact: marc@marcdeller.com\n\n"
            "## Scope\n\n"
            "chatMCD answers about Marc only, and declines rather than guessing when "
            "something is outside what it was trained on.\n"
        )
        return Response(body, mimetype="text/plain")

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(app.static_folder, "img/favicon.svg",
                                   mimetype="image/svg+xml")

    # -- chat ---------------------------------------------------------------

    @app.route("/api/chat", methods=["POST", "OPTIONS"])
    @limit(app.config["RATE_LIMIT"])
    def chat():
        if request.method == "OPTIONS":
            return ("", 204)

        body = request.get_json(silent=True)
        # json.loads("3") is an int; guard before calling .get on it.
        if not isinstance(body, dict):
            abort(400, "expected a JSON object")

        message = str(body.get("message", "")).strip()
        if not message:
            abort(400, "message is required")
        if len(message) > app.config["MAX_MESSAGE_CHARS"]:
            abort(413, "message too long")

        history = _clean_history(body.get("history"), app.config["MAX_HISTORY_TURNS"])
        source = _source(body.get("source"))
        temperature = _num(body.get("temperature"), app.config["TEMPERATURE"], 0.0, 1.5)
        max_new = int(_num(body.get("max_tokens"), app.config["MAX_NEW_TOKENS"], 16, 2048))
        wants_stream = body.get("stream", True) is not False

        # The row goes in BEFORE the answer is generated, so a question is still
        # recorded if the stream dies half way. The answer, how long it took and
        # whether it declined are written back onto the same row at the end.
        row_id, started = None, time.time()
        if app.config["LOG_QUESTIONS"]:
            try:
                conn = db(app)
                cur = conn.execute(
                    "INSERT INTO questions (ts, source, question, turn, ip, "
                    "user_agent, ref) VALUES (?,?,?,?,?,?,?)",
                    (started, source, scrub(message)[:500], len(history) // 2,
                     client_ip(), (request.headers.get("User-Agent") or "")[:300],
                     (request.headers.get("Referer") or "")[:300]))
                row_id = cur.lastrowid
                conn.commit()
            except Exception:
                log.exception("question logging failed")

        def finish(answer: str) -> None:
            """Write the answer back. Runs after the response has been sent, so
            it opens its own connection rather than using the request's."""
            if row_id is None:
                return
            try:
                c = connect(app.config["DB_PATH"])
                c.execute("UPDATE questions SET answer = ?, latency = ?, "
                          "said_idk = ? WHERE id = ?",
                          (scrub(answer)[:4000], round(time.time() - started, 2),
                           int(bool(DECLINED.search(answer))), row_id))
                c.commit()
                c.close()
            except Exception:
                log.exception("answer logging failed")

        chunks = space.stream(
            message, history,
            temperature=temperature, top_p=app.config["TOP_P"],
            repetition_penalty=app.config["REPETITION_PENALTY"],
            max_new_tokens=max_new,
        )

        if not wants_stream:
            parts, status = [], None
            for c in chunks:
                if c.kind == "token":
                    parts.append(c.text)
                elif c.kind == "error":
                    return jsonify({"error": c.detail}), 502
                elif c.kind == "status":
                    status = c.text
            answer = "".join(parts).strip()
            finish(answer)
            return jsonify({"answer": answer, "status": status})

        @stream_with_context
        def events():
            rid = uuid.uuid4().hex[:12]
            yield _sse("meta", {"id": rid})
            parts = []
            try:
                for c in chunks:
                    if c.kind == "token":
                        parts.append(c.text)
                        yield _sse("token", {"t": c.text})
                    elif c.kind == "status":
                        yield _sse("status", {"state": c.text, "detail": c.detail})
                    elif c.kind == "error":
                        yield _sse("error", {"detail": c.detail})
                        return
                    else:
                        yield _sse("done", {})
            except GeneratorExit:  # client hit Stop
                raise
            except Exception as e:
                log.exception("stream failed")
                yield _sse("error", {"detail": str(e)[:200]})
            finally:
                # Runs on a clean finish, an error, and a visitor pressing Stop,
                # so a half-read answer is still recorded as what they saw.
                finish("".join(parts).strip())

        return Response(events(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",   # nginx must not buffer an SSE stream
            "Connection": "keep-alive",
        })

    @app.post("/api/feedback")
    @limit("30 per minute")
    def feedback():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400, "expected a JSON object")
        vote = 1 if body.get("vote") in (1, "1", "up", True) else -1
        question = scrub(str(body.get("question", "")))[:500]
        note = scrub(str(body.get("note", "")))[:500] or None
        conn = db(app)
        conn.execute(
            "INSERT INTO feedback (ts, source, question, vote, note) VALUES (?,?,?,?,?)",
            (time.time(), _source(body.get("source")), question, vote, note))
        conn.commit()
        return jsonify({"ok": True})


# ------------------------------------------------------------------- helpers


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _source(value) -> str:
    v = str(value or "app").lower()
    return v if v in {"app", "iframe", "shortcode", "api"} else "api"


def _num(value, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _clean_history(raw, max_turns: int) -> list[dict]:
    """Accept [{role, content}, ...] or [[user, assistant], ...] and normalise.

    A client-supplied system message is dropped: the Space injects the real one.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            role, content = item.get("role"), item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                out.append({"role": role, "content": content.strip()[:4000]})
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            u, a = item
            if isinstance(u, str) and u.strip():
                out.append({"role": "user", "content": u.strip()[:4000]})
            if isinstance(a, str) and a.strip():
                out.append({"role": "assistant", "content": a.strip()[:4000]})
    return out[-max_turns * 2:]


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8010")), debug=True)
