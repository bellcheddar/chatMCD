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
import time
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


# --------------------------------------------------------------- the status light
# The dot beside Marc's name reports these. The failure that matters is a green
# dot on a broken model, so each state is pinned rather than inferred.

def fresh(**kw):
    c = SpaceClient("Dellboy/chatmcd-api", None)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_a_recent_answer_is_green():
    c = fresh(_probe_at=time.time(), _probe_ok=True,
              _warm_at=time.time(), _ok_at=time.time())
    assert c.health()["state"] == "ok"


def test_a_recent_failure_is_red_even_if_it_answered_before():
    """Red must win: an answer from ten minutes ago does not mean it works now."""
    now = time.time()
    c = fresh(_probe_at=now, _probe_ok=True, _ok_at=now - 60, _fail_at=now)
    assert c.health()["state"] == "down"


def test_queued_or_warming_is_amber_not_green():
    now = time.time()
    c = fresh(_probe_at=now, _probe_ok=True, _warm_at=now, _ok_at=now, _slow_at=now)
    assert c.health()["state"] == "degraded"


def test_an_unreachable_space_is_red():
    c = fresh(_probe_at=time.time(), _probe_ok=False)
    assert c.health()["state"] == "down"


def test_reachable_but_cold_is_amber():
    """It will answer, but the first one will be slow: that is not 'live'.

    _recovery_at is set so the unknown-state probe below does not fire: this
    test is about a client that has already seen generation work.
    """
    c = fresh(_probe_at=time.time(), _probe_ok=True, _ok_at=time.time() - 100000,
              _recovery_at=time.time())
    assert c.health()["state"] == "degraded"


def test_a_handshake_alone_never_reports_green():
    """A Space with no GPU allowance answers the handshake perfectly and then
    returns nothing to every question, so 'reachable' must not mean 'working'
    until a generation has actually been seen to succeed."""
    c = fresh(_probe_at=time.time(), _probe_ok=True)   # never generated anything
    probed = []
    c._recovery_probe = lambda: (probed.append(1), False)[1]
    h = c.health()
    assert probed, "reported a state without checking whether it can generate"
    assert h["state"] == "down" and h["reason"] == "quota"


def test_a_successful_unknown_state_probe_reports_green():
    c = fresh(_probe_at=time.time(), _probe_ok=True)
    c._recovery_probe = lambda: (setattr(c, "_ok_at", time.time()), True)[1]
    assert c.health()["state"] == "ok"


def test_a_stale_failure_no_longer_holds_it_red():
    """An ordinary error does decay. Only an exhausted allowance is sticky."""
    c = fresh(_probe_at=time.time(), _probe_ok=True, _recovery_at=time.time(),
              _ok_at=time.time(), _warm_at=time.time(),
              _fail_at=time.time() - SpaceClient.FAIL_TTL - 1)
    assert c.health()["state"] != "down"


def test_health_never_calls_the_gpu():
    """Every open tab polls this. It must answer from what it already knows."""
    c = fresh(_probe_at=time.time(), _probe_ok=True, _warm_at=time.time(),
              _ok_at=time.time())
    c.client = lambda *a, **k: (_ for _ in ()).throw(AssertionError("connected"))
    c._connect = c.client
    assert c.health()["state"] == "ok"


def test_every_state_carries_a_human_explanation():
    for kw in ({"_probe_ok": True, "_ok_at": time.time(), "_warm_at": time.time()},
               {"_probe_ok": True},
               {"_probe_ok": False},
               {"_probe_ok": True, "_fail_at": time.time()}):
        h = fresh(_probe_at=time.time(), **kw).health()
        assert h["detail"], h
        assert h["state"] in {"ok", "degraded", "down"}


def test_a_successful_stream_clears_a_previous_failure():
    c = client_yielding(["Elora ", "Elora Therapeutics"])
    c._fail_at = time.time()
    list(c.stream("q", [], temperature=0.7, top_p=0.9,
                  repetition_penalty=1.05, max_new_tokens=64))
    assert c._fail_at == 0.0 and c._ok_at > 0


def test_an_empty_stream_records_a_failure():
    c = client_yielding([])
    list(c.stream("q", [], temperature=0.7, top_p=0.9,
                  repetition_penalty=1.05, max_new_tokens=64))
    assert c._fail_at > 0, "an empty answer must show red, not green"


# ------------------------------------------------------- the ZeroGPU allowance
# Running out of credits is the one outage with a known cause and a known end,
# and it must not decay like an ordinary error: letting it lapse after ten
# minutes put the light back to amber on a site that could not answer at all.

def with_state(tmp, **kw):
    c = SpaceClient("Dellboy/chatmcd-api", None,
                    state_path=str(tmp / "space-state.json"))
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_an_empty_completion_marks_the_credits_exhausted():
    c = client_yielding([])
    list(c.stream("q", [], temperature=0.7, top_p=0.9,
                  repetition_penalty=1.05, max_new_tokens=64))
    assert c.out_of_credits


def test_being_out_of_credits_is_red_with_its_own_reason(tmp_path):
    c = with_state(tmp_path, _probe_at=time.time(), _probe_ok=True,
                   _warm_at=time.time(), _ok_at=time.time())
    c.mark_quota_exhausted()
    c._recovery_at = time.time()          # do not probe during the test
    h = c.health()
    assert h["state"] == "down" and h["reason"] == "quota"
    assert "credit" in h["detail"].lower()


def test_it_does_not_decay_back_to_amber(tmp_path):
    """The bug this exists to prevent: FAIL_TTL let it lapse after ten minutes."""
    c = with_state(tmp_path, _probe_at=time.time(), _probe_ok=True,
                   _warm_at=time.time(), _ok_at=time.time())
    c.mark_quota_exhausted()
    c._quota_at = time.time() - 86400     # a whole day ago
    c._recovery_at = time.time()
    assert c.health()["state"] == "down"


def test_it_survives_a_restart(tmp_path):
    c = with_state(tmp_path)
    c.mark_quota_exhausted()
    fresh_client = with_state(tmp_path, _probe_at=time.time(), _probe_ok=True,
                              _warm_at=time.time())
    fresh_client._recovery_at = time.time()
    assert fresh_client.out_of_credits
    assert fresh_client.health()["reason"] == "quota"


def test_a_real_answer_clears_it(tmp_path):
    c = with_state(tmp_path)
    c.mark_quota_exhausted()
    c._client = type("C", (), {"submit": lambda self, *a, **k: FakeJob(["hello"])})()
    list(c.stream("q", [], temperature=0.7, top_p=0.9,
                  repetition_penalty=1.05, max_new_tokens=64))
    assert not c.out_of_credits
    assert with_state(tmp_path).out_of_credits is False, "the file still says out"


def test_the_recovery_probe_is_rate_limited(tmp_path):
    """It costs a real (tiny) generation, so it must not run on every poll."""
    c = with_state(tmp_path, _probe_at=time.time(), _probe_ok=True)
    c.mark_quota_exhausted()
    calls = []
    c._recovery_probe = lambda: (calls.append(1), False)[1]
    c._recovery_at = time.time()
    for _ in range(20):
        c.health()
    assert calls == [], "probed while inside the rate-limit window"
    c._recovery_at = time.time() - c.RECOVERY_TTL - 1
    c.health()
    assert len(calls) == 1


def test_a_corrupt_state_file_does_not_stop_the_app(tmp_path):
    (tmp_path / "space-state.json").write_text("{ not json")
    c = with_state(tmp_path, _probe_at=time.time(), _probe_ok=True,
                   _warm_at=time.time(), _ok_at=time.time())
    assert c.health()["state"] == "ok"


def test_every_health_reply_carries_a_reason_field(tmp_path):
    for kw in ({"_probe_ok": True, "_ok_at": time.time(), "_warm_at": time.time()},
               {"_probe_ok": True},
               {"_probe_ok": False},
               {"_probe_ok": True, "_fail_at": time.time()}):
        c = with_state(tmp_path, _probe_at=time.time(), **kw)
        assert "reason" in c.health(), c.health()
