"""Assert the Space publishes the API contract chatmcd/hf_client.py calls.

This is the test that did not exist while two contract bugs shipped, each of
which returned an empty answer with no error and cost a Space rebuild to find:

  * six arguments sent to a five-parameter endpoint (history -> temperature)
  * an endpoint published with `returns: []`, which ran the generator on a GPU
    and threw every token away

Both are visible in the published schema, so this reads the schema. It builds
the real ui.build_demo with a stub generator in place of Qwen3-8B, launches it
on a local port, and calls it exactly as the Flask app does.

    .venv-gradio/bin/python3 -m pytest space/test_api_contract.py -q

Needs a Python with gradio installed; the project venv is 3.14 and gradio's
pydantic pin has no wheel for it, hence the separate .venv-gradio (3.12).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from ui import CHAT_API_PARAMS, build_demo  # noqa: E402

STUB_TOKENS = ["Elora ", "Therapeutics", ", a pre-seed biotech he co-founded."]


def stub_chat(message, history=None, temperature=0.7, top_p=0.9,
              repetition_penalty=1.05, max_new_tokens=512):
    """Stands in for the real generator: same shape, no model.

    Echoes the arguments it received so a silently shifted parameter shows up as
    a wrong value rather than as a plausible answer.
    """
    yield f"[h={0 if not history else len(history)} t={temperature} " \
          f"p={top_p} r={repetition_penalty} n={int(max_new_tokens)}] "
    out = ""
    for tok in STUB_TOKENS:
        out += tok
        yield f"[h={0 if not history else len(history)} t={temperature} " \
              f"p={top_p} r={repetition_penalty} n={int(max_new_tokens)}] {out}"


@pytest.fixture(scope="module")
def client():
    from gradio_client import Client

    demo = build_demo(stub_chat, base_model="stub", adapter="", retrieval_on=True)
    demo.queue(max_size=4).launch(prevent_thread_lock=True, quiet=True,
                                  share=False, ssr_mode=False)
    try:
        yield Client(demo.local_url, verbose=False)
    finally:
        demo.close()


def endpoint(client, name):
    info = client.view_api(return_format="dict", print_info=False)
    named = info["named_endpoints"]
    assert name in named, f"{name} is not published; have {sorted(named)}"
    return named[name]


def test_chat_takes_the_six_arguments_flask_sends(client):
    """hf_client.py submits six positionals. The endpoint must accept six."""
    params = [p["parameter_name"] for p in endpoint(client, "/chat")["parameters"]]
    assert tuple(params) == CHAT_API_PARAMS


def test_chat_declares_an_output(client):
    """`returns: []` is the bug that ran the GPU and discarded the answer."""
    assert endpoint(client, "/chat")["returns"], \
        "/chat publishes no outputs: every call will return an empty tuple"


def test_chat_streams_and_returns_the_answer(client):
    """submit() must yield partials, and the last one must be the full answer."""
    job = client.submit("What is Elora Therapeutics?", None,
                        0.7, 0.9, 1.05, 256, api_name="/chat")
    partials = [p if isinstance(p, str) else str(p) for p in job]
    assert partials, "the endpoint yielded nothing"
    assert "".join(STUB_TOKENS) in partials[-1]


def test_arguments_are_not_shifted(client):
    """The stub echoes what it got. Non-default values must arrive intact."""
    answer = client.predict("hello", [{"role": "user", "content": "hi"}],
                            0.11, 0.22, 1.33, 128, api_name="/chat")
    text = answer if isinstance(answer, str) else str(answer)
    assert "h=1 t=0.11 p=0.22 r=1.33 n=128" in text, text[:200]


def test_chat_ui_is_still_published(client):
    """The debug UI's own endpoint, which must not collide with /chat."""
    assert endpoint(client, "/chat_ui")["parameters"]
