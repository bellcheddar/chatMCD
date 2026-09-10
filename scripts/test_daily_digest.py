"""Tests for the daily digest: the env file it reads and the markdown it renders.

Both have a failure mode that produces a digest which looks fine and is wrong:

  * /opt/chatmcd/.env is a systemd EnvironmentFile, not a shell script, so it
    can legally contain `RATE_LIMIT=20 per minute`. Sourcing it runs `per` as a
    command; under `set -e` that killed a deploy once already.
  * The answer is stored exactly as the model wrote it, which is markdown, so
    escaping it straight into the mail shows literal **bold** to the reader.

    .venv/bin/python3 -m pytest scripts/test_daily_digest.py -q
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("dd", ROOT / "scripts/daily_digest.py")
dd = importlib.util.module_from_spec(spec)
sys.modules["dd"] = dd
spec.loader.exec_module(dd)


# ------------------------------------------------------------------- the env

def test_a_systemd_env_file_with_spaces_is_read_without_a_shell(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# a comment\nRATE_LIMIT=20 per minute\nDIGEST_TO=a@b.com\n'
                 'QUOTED="hello world"\nEMPTY=\n')
    for k in ("RATE_LIMIT", "DIGEST_TO", "QUOTED", "EMPTY"):
        monkeypatch.delenv(k, raising=False)
    dd.load_env(str(f))
    assert os.environ["RATE_LIMIT"] == "20 per minute"
    assert os.environ["DIGEST_TO"] == "a@b.com"
    assert os.environ["QUOTED"] == "hello world", "systemd strips the quotes"
    assert os.environ["EMPTY"] == ""


def test_an_existing_variable_wins_over_the_file(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("DIGEST_TO=file@example.com\n")
    monkeypatch.setenv("DIGEST_TO", "cli@example.com")
    dd.load_env(str(f))
    assert os.environ["DIGEST_TO"] == "cli@example.com"


def test_a_missing_env_file_is_not_an_error():
    assert dd.load_env("/nonexistent/.env") == 0


# -------------------------------------------------------------- the rendering

def test_bold_becomes_bold_rather_than_asterisks():
    out = dd.as_email_html("He co-founded **Elora Therapeutics** in 2025.")
    assert "<b>Elora Therapeutics</b>" in out
    assert "**" not in out


def test_a_bullet_list_becomes_a_list():
    out = dd.as_email_html("The tally:\n\n- 400+ structures\n- 60+ papers")
    assert out.count("<li>") == 2 and "<ul" in out and "</ul>" in out


def test_a_numbered_list_becomes_a_list():
    out = dd.as_email_html("1. first\n2. second")
    assert out.count("<li>") == 2


def test_a_table_collapses_to_a_readable_line():
    out = dd.as_email_html("| Role | Where |\n|---|---|\n| CSO | Elora |")
    assert "Role · Where" in out and "CSO · Elora" in out
    assert "---" not in out, "the separator row should not be printed"


def test_markup_from_the_model_is_escaped():
    out = dd.as_email_html("<script>alert(1)</script> and <b>raw</b>")
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_an_empty_answer_says_so_rather_than_rendering_nothing():
    assert "no answer recorded" in dd.as_email_html("")


# --------------------------------------------------------------- the document

def _row(**kw):
    base = {"id": 1, "ts": 1_760_000_000.0, "source": "app", "question": "Who?",
            "turn": 0, "said_idk": 0, "ip": "203.0.113.9", "user_agent": "Mozilla/5.0",
            "answer": "An **answer**.", "latency": 4.2, "ref": ""}
    base.update(kw)
    return base


def test_the_digest_renders_with_no_geolocation_at_all():
    """ip-api can fail or be switched off; the digest must still go out."""
    out = dd.build_html([_row()], {}, {}, 24)
    assert "Who?" in out and "<b>answer</b>" in out and "unknown" in out


def test_a_quiet_day_produces_a_digest_rather_than_a_crash():
    out = dd.build_html([], {}, {}, 24)
    assert "Nobody asked" in out


def test_a_busy_day_is_capped_and_says_so():
    rows = [_row(id=i, question=f"Q{i}") for i in range(200)]
    out = dd.build_html(rows, {}, {}, 24, limit=10)
    assert "Showing the 10 most recent of 200" in out
    assert out.count("<tr>") <= 14, "the cap did not apply"
    assert "Q199" in out and "Q0" not in out, "newest first"


def test_a_declined_answer_is_flagged():
    out = dd.build_html([_row(said_idk=1)], {}, {}, 24)
    assert "declined" in out


def test_the_device_is_read_from_the_user_agent():
    assert dd.device("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)") == "mobile"
    assert dd.device("Mozilla/5.0 (Macintosh; Intel Mac OS X)") == "desktop"
    assert dd.device("Googlebot/2.1") == "bot"
    assert dd.device("") == ""


def test_private_addresses_are_never_sent_for_geolocation(monkeypatch):
    sent = []
    monkeypatch.setattr(dd.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert dd.geolocate(["127.0.0.1", "10.0.0.5", "192.168.1.1", "::1"]) == {}
    assert sent == []
