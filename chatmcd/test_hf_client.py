"""Tests for the Space client's failure handling.

The case that matters here is the one that has no exception and no log line on
either side: ZeroGPU answers an over-quota request with `event: error` and a
null payload, gradio_client turns that into a generator that yields nothing and
reports Status.FINISHED, and the Flask app used to report that as a successful
answer of "". The Space was simultaneously answering the same question correctly
from a different IP, so every check short of reading the raw SSE said healthy.

    .venv/bin/python3 -m pytest chatmcd/test_hf_client.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chatmcd.hf_client import SpaceClient  # noqa: E402


class FakeStatus:
    code = "Status.FINISHED"


class FakeJob:
    """Stands in for gradio_client's Job: an iterable of cumulative partials."""

    def __init__(self, partials):
        self._partials = partials

    def __iter__(self):
        return iter(self._partials)

    def status(self):
        return FakeStatus()


def client_yielding(partials, token=None):
    c = SpaceClient("Dellboy/chatmcd-api", token)
    c._client = type("C", (), {"submit": lambda self, *a, **k: FakeJob(partials)})()
    return c


def run(c):
    return list(c.stream("q", [], temperature=0.7, top_p=0.9,
                         repetition_penalty=1.05, max_new_tokens=64))


def test_normal_completion_streams_deltas_and_finishes():
    chunks = run(client_yielding(["El", "Elora", "Elora Therapeutics"]))
    assert [c.kind for c in chunks] == ["token", "token", "token", "done"]
    assert "".join(c.text for c in chunks if c.kind == "token") == "Elora Therapeutics"


def test_empty_completion_is_an_error_not_an_empty_answer():
    """The whole point: zero partials must NOT read as a successful "" answer."""
    chunks = run(client_yielding([]))
    kinds = [c.kind for c in chunks]
    assert "done" not in kinds, "an empty completion was reported as success"
    assert kinds[-1] == "error"
    assert chunks[-1].detail, "the error carried no message for the visitor"


def test_the_visitor_error_does_not_leak_server_configuration():
    """The operator's diagnosis belongs in the log, not on a public page."""
    detail = [c for c in run(client_yielding([])) if c.kind == "error"][0].detail
    low = detail.lower()
    assert "token" not in low and "hugging face" not in low, detail


def test_whitespace_only_completion_is_also_an_error():
    chunks = run(client_yielding(["", "   ", "\n"]))
    assert [c.kind for c in chunks][-1] == "error"


def test_untokened_client_names_the_quota_in_the_status():
    """With no HF token the cause is almost always the anonymous quota, and the
    status line should say so rather than blame a busy GPU."""
    status = [c for c in run(client_yielding([])) if c.kind == "status"][0]
    assert "quota for this server" in status.detail


def test_tokened_client_gives_the_generic_queue_message():
    status = [c for c in run(client_yielding([], token="hf_x")) if c.kind == "status"][0]
    assert "quota for this server" not in status.detail
