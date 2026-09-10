"""The Gradio surface for the chatMCD Space: the debug UI and the /chat API.

Separate from app.py for one reason: app.py loads Qwen3-8B at import, so nothing
that imports it can run on a laptop, and the API contract therefore could not be
tested anywhere except in production. It was wrong in production twice.

  1. The Flask app sent six arguments to ChatInterface's five-parameter endpoint.
     `history` landed in `temperature`. Empty answer, no error.
  2. gr.api() derived the endpoint's OUTPUT signature from the function's return
     annotation. A streaming generator has none, so /chat published
     `returns: []`, burned a GPU call, discarded every yield and returned an
     empty tuple in 1.5 s. Again no error, anywhere.

Both were contract bugs, invisible to every test that did not read the published
API schema. test_api_contract.py now reads it, against this exact code, with a
stub generator in place of the model.
"""

from __future__ import annotations

from typing import Callable

import gradio as gr

# The six positional arguments chatmcd/hf_client.py sends, in order. The test
# asserts the published endpoint matches this exactly.
CHAT_API_PARAMS = ("message", "history", "temperature", "top_p",
                   "repetition_penalty", "max_new_tokens")


def build_demo(chat_fn: Callable, *, base_model: str, adapter: str,
               retrieval_on: bool) -> gr.Blocks:
    with gr.Blocks(title="chatMCD API", analytics_enabled=False) as demo:
        gr.Markdown(
            "## chatMCD — inference endpoint\n"
            f"`{base_model}` + `{adapter or 'no adapter'}`"
            f"{' · retrieval ON' if retrieval_on else ''}\n\n"
            "This is the debug surface. The product lives at "
            "[chatmcd.mdeller.com](https://chatmcd.mdeller.com)."
        )

        # The UI. ChatInterface owns history internally and does NOT expose it as
        # an API parameter, so its endpoint is
        # (message, temperature, top_p, repetition_penalty, max_new_tokens).
        # The Flask app needs history, so it calls /chat below instead.
        gr.ChatInterface(
            fn=chat_fn,
            type="messages",
            additional_inputs=[
                gr.Slider(0.0, 1.5, value=0.7, step=0.05, label="temperature"),
                gr.Slider(0.1, 1.0, value=0.9, step=0.05, label="top_p"),
                gr.Slider(1.0, 1.5, value=1.05, step=0.01, label="repetition_penalty"),
                gr.Slider(64, 1024, value=512, step=64, label="max_new_tokens"),
            ],
            examples=[
                ["Give me Marc's quick résumé."],
                ["What is Elora Therapeutics?"],
                ["Why is BoltzMaker called BoltzMaker?"],
            ],
            api_name="chat_ui",
        )

        # ---------------------------------------------------------------- /chat
        # The real API. Explicit components, not gr.api(): components state the
        # contract instead of inferring it from type hints, and this is the path
        # Gradio actually streams a generator over.
        with gr.Row(visible=False):
            api_message = gr.Textbox(label="message")
            api_history = gr.JSON(label="history")
            api_temperature = gr.Number(value=0.7, label="temperature")
            api_top_p = gr.Number(value=0.9, label="top_p")
            api_repetition_penalty = gr.Number(value=1.05, label="repetition_penalty")
            api_max_new_tokens = gr.Number(value=512, precision=0, label="max_new_tokens")
            api_answer = gr.Textbox(label="answer")
            api_submit = gr.Button("submit")

        api_submit.click(
            fn=chat_fn,
            inputs=[api_message, api_history, api_temperature, api_top_p,
                    api_repetition_penalty, api_max_new_tokens],
            outputs=api_answer,
            api_name="chat",
            show_progress="hidden",
        )

    return demo
