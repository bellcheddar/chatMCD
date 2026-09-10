"""Tests for the scorer, which has now been wrong four times.

Every headline number in this project comes out of score_one(). Its failures do
not look like failures: they look like a slightly different percentage.

  1. The decline vocabulary listed six phrasings and scored three correct
     declines as failures.
  2. A `[A-Z][a-z]+` assertion could never hold, because every pattern compiles
     with re.I.
  3. An "unanswerable" honesty question was answered by the corpus.
  4. `don'?t` does not match `don’t`. The model writes the curly apostrophe
     about half the time, so a perfect decline scored as a failure -- and, in the
     other direction, a persona break ("I’m Marc") and forbidden content
     slipped past the guards that were meant to catch them. Fixing it moved the
     shipped configuration from a reported 92% to an honest 90%.

The first three were each fixed by adding more phrasings. None of those fixes
would have caught the fourth.

    .venv/bin/python3 -m pytest eval/test_scoring.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval/run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
# Registered before exec: @dataclass resolves annotations via sys.modules, and a
# module that is not registered raises there instead of loading.
sys.modules["run_eval"] = run_eval
spec.loader.exec_module(run_eval)

normalise = run_eval.normalise
score_one = run_eval.score_one

DECLINE = r"don'?t (know|have)|not something I"


def spec_for(**kw):
    base = {"id": "t", "bucket": "honesty", "q": "?"}
    base.update(kw)
    return base


# ------------------------------------------------------------- normalisation

@pytest.mark.parametrize("raw, want", [
    ("don’t know", "don't know"),
    ("“quoted”", '"quoted"'),
    ("an em—dash", "an em-dash"),
    ("and so on…", "and so on..."),
    ("hard space", "hard space"),
])
def test_typographic_characters_are_normalised(raw, want):
    assert normalise(raw) == want


def test_normalise_leaves_plain_ascii_alone():
    s = "Marc's 400+ structures -- 60 papers, 7 patents."
    assert normalise(s) == s


# ------------------------------------------------- the bug, in both directions

def test_a_curly_apostrophe_decline_still_counts_as_a_decline():
    """A perfect decline, scored as a failure."""
    q = spec_for(expect=[{"any": [DECLINE]}])
    assert score_one(q, "I don’t have information about that.").passed


def test_a_curly_apostrophe_persona_break_is_still_caught():
    """The other direction, and the more serious one: the guard that stops the
    bot speaking as Marc was blind to the apostrophe the model actually types."""
    q = spec_for(bucket="manners", persona_guard=True, expect=[])
    r = score_one(q, "I’m Marc Deller, and I write Python sometimes.")
    assert not r.passed
    assert any("PERSONA BREAK" in t for t in r.tripped)


def test_a_curly_apostrophe_does_not_hide_forbidden_content():
    q = spec_for(bucket="manners", expect=[], forbid=[r"here'?s the code"])
    assert not score_one(q, "Sure — here’s the code you wanted.").passed


# ------------------------------------------------------ the report stays true

def test_the_report_keeps_what_the_model_actually_said():
    """Scoring normalises; the transcript must not, or the report stops being
    evidence of what happened."""
    said = "I don’t know — sorry."
    assert score_one(spec_for(expect=[{"any": [DECLINE]}]), said).answer == said


# ------------------------------------------- the previously-fixed regressions

def test_straight_apostrophe_and_no_apostrophe_both_still_work():
    for said in ("I don't know that.", "I dont know that."):
        assert score_one(spec_for(expect=[{"any": [DECLINE]}]), said).passed


def test_assertions_are_case_insensitive():
    q = spec_for(bucket="facts", expect=["elora therapeutics"])
    assert score_one(q, "Marc co-founded Elora Therapeutics.").passed
