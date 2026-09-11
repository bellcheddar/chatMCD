"""Tests for what the question log records, and for the schema migration.

Two things here are easy to get wrong in a way that looks fine:

  * Behind nginx every request arrives from 127.0.0.1, so logging remote_addr
    gives a database full of one address. The visitor is the FIRST entry of
    X-Forwarded-For; trusting the last one logs your own proxy.
  * The extra columns were added to a table that already had rows in it, so the
    migration has to be safe to run against both an old database and a new one,
    repeatedly, without losing anything.

    .venv/bin/python3 -m pytest chatmcd/test_capture.py -q
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chatmcd.app import DECLINED, SCHEMA, client_ip, connect, migrate  # noqa: E402
from chatmcd.config import EMBED_PRESETS, PRESETS  # noqa: E402


# ------------------------------------------------------------------ the client

@pytest.fixture
def ctx():
    from flask import Flask
    return Flask(__name__).test_request_context


def test_the_visitor_is_the_first_forwarded_entry(ctx):
    """nginx appends each hop, so the client is at the front of the chain."""
    with ctx(headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.5, 172.16.0.1"}):
        assert client_ip() == "203.0.113.9"


def test_x_real_ip_is_the_fallback(ctx):
    with ctx(headers={"X-Real-IP": "198.51.100.4"}):
        assert client_ip() == "198.51.100.4"


def test_no_proxy_headers_falls_back_to_the_socket(ctx):
    with ctx(environ_base={"REMOTE_ADDR": "192.0.2.7"}):
        assert client_ip() == "192.0.2.7"


def test_a_long_forged_header_cannot_blow_up_the_column(ctx):
    with ctx(headers={"X-Forwarded-For": "9" * 5000}):
        assert len(client_ip()) <= 45


# ----------------------------------------------------------------- the decline

@pytest.mark.parametrize("answer", [
    "I don't have information about that.",
    "I do not know what car Marc drives.",
    "That isn't something I cover, I'm afraid.",
    "That's outside what I know.",
    "There is no record of that in his writing.",
])
def test_a_decline_is_recognised(answer):
    assert DECLINED.search(answer), answer


@pytest.mark.parametrize("answer", [
    "He has been a structural biologist for over twenty-five years.",
    "Elora Therapeutics is a pre-seed biotech he co-founded in 2025.",
    "He has determined over 400 protein structures.",
])
def test_a_real_answer_is_not_flagged_as_a_decline(answer):
    assert not DECLINED.search(answer), answer


# ---------------------------------------------------------------- the schema

def test_migration_adds_the_columns_to_an_old_database(tmp_path):
    """The production table already had rows when the columns were added."""
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE questions (id INTEGER PRIMARY KEY, ts REAL NOT NULL,
            source TEXT NOT NULL, question TEXT NOT NULL,
            turn INTEGER NOT NULL DEFAULT 0, said_idk INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE feedback (id INTEGER PRIMARY KEY, ts REAL NOT NULL,
            source TEXT NOT NULL, question TEXT NOT NULL, vote INTEGER NOT NULL,
            note TEXT);
    """)
    conn.execute("INSERT INTO questions (ts, source, question) VALUES (1, 'app', 'hi')")
    conn.commit()
    conn.close()

    c = connect(str(db))
    cols = {r[1] for r in c.execute("PRAGMA table_info(questions)")}
    assert {"ip", "user_agent", "answer", "latency", "ref"} <= cols
    assert c.execute("SELECT count(*) FROM questions").fetchone()[0] == 1, \
        "the migration lost a row"


def test_migration_is_safe_to_run_twice(tmp_path):
    db = tmp_path / "twice.sqlite"
    c = connect(str(db))
    migrate(c)          # would raise "duplicate column" if it were not guarded
    migrate(c)
    assert c.execute("SELECT count(*) FROM questions").fetchone()[0] == 0


def test_a_fresh_database_has_every_column_without_migrating(tmp_path):
    conn = sqlite3.connect(tmp_path / "new.sqlite")
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    assert {"ip", "user_agent", "answer", "latency", "ref"} <= cols


# ---------------------------------------------------------------- the presets

def test_the_widget_offers_fewer_chips_than_it_would_have_to_hide():
    """Its chip strip is two rows tall with overflow:hidden, so anything past
    the cap would be in the DOM, focusable, and invisible."""
    assert EMBED_PRESETS < len(PRESETS)
    assert EMBED_PRESETS >= 3


def test_every_preset_has_a_label_and_a_prompt():
    for p in PRESETS:
        assert p["id"] and p["label"] and p["prompt"], p
    assert len({p["id"] for p in PRESETS}) == len(PRESETS), "duplicate preset id"


# ------------------------------------------------------------------ the scrubber
# It exists to keep contact details out of the log. It used to take date ranges
# with them: "2018-2024" is nine characters of digits and a dash, the pattern
# matched, and the log and digest read "Incyte ([phone])".

from chatmcd.app import scrub  # noqa: E402


@pytest.mark.parametrize("text", [
    "Incyte (2018-2024), Stanford (2015-2018)",
    "Leeds 1991-1995 1995-1999",
    "400+ structures, 60+ publications, 7+ patents",
    "PDB 10PI at 1.54 Å",
])
def test_dates_and_numbers_survive(text):
    assert scrub(text) == text


@pytest.mark.parametrize("text", [
    "call (302) 555-0142",
    "ring +44 115 496 0123",
    "phone 302-555-0142 please",
])
def test_phone_numbers_are_still_removed(text):
    assert "[phone]" in scrub(text), scrub(text)


def test_emails_are_still_removed():
    assert scrub("write to someone@example.com") == "write to [email]"
