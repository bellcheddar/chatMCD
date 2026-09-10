"""chatMCD — Hugging Face ZeroGPU Space.

Answers about Marc C. Deller by retrieving from his own writing. Retrieval over
the hand-written Q&A pairs is the primary mechanism; a LoRA adapter may be loaded
on top as an optional voice layer, but it is not what supplies the facts.

Nothing here trains anything: ZeroGPU is inference only. The index was built on
Marc's M1 Max by scripts/build_rag_index.py.

The Gradio UI is a debug surface. The product is the `/chat` endpoint, which the
Flask app at chatmcd.mdeller.com calls; the Flask app is the only thing that
holds a Hugging Face token.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# `spaces` must be imported before torch touches CUDA.
import spaces  # noqa: F401  (import order matters on ZeroGPU)
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer

from ui import build_demo

HERE = Path(__file__).parent

BASE_MODEL = os.environ.get("BASE_MODEL", "Qwen/Qwen3-8B")
# No adapter by default. Measured on the 50-question set: base + retrieval
# scores 92%, and adding the fine-tuned adapter scored lower (86%) while
# dropping honesty from 4/4 to 3/4. Set ADAPTER to a repo id to load one anyway.
ADAPTER = os.environ.get("ADAPTER", "")
# Retrieval is the primary mechanism, not a fallback, so it is ON by default.
# Measured on the 50-question set: retrieval over the Q&A pairs takes facts from
# 25% to 88% and honesty from 2/4 to 4/4 with no fine-tuning at all.
RAG_ENABLED = os.environ.get("RAG_ENABLED", "1").lower() in {"1", "true", "on"}
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "8"))
# "qa" retrieves only the hand-written pairs: measured to match top-12 over
# everything for recall, with prompts roughly a fifth the length.
RAG_KINDS = os.environ.get("RAG_KINDS", "hybrid")
RAG_CHUNKS = int(os.environ.get("RAG_CHUNKS", "3"))
MAX_HISTORY_TURNS = 8

SYSTEM_PROMPT = (HERE / "system_prompt.md").read_text().strip()

# ---------------------------------------------------------------- model load

# Loaded once, at import. On ZeroGPU the process is snapshotted with the weights
# resident and a GPU is attached for the duration of each decorated call.
tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
model = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL, torch_dtype=torch.bfloat16, device_map="auto",
)
if ADAPTER:
    from peft import PeftModel

    model = PeftModel.from_pretrained(model, ADAPTER)
    # Merging removes the LoRA indirection from every forward pass. The adapter
    # is frozen at inference, so there is nothing to lose by folding it in.
    model = model.merge_and_unload()
model.eval()

# Qwen3's hybrid template emits a reasoning block by default. chatMCD was trained
# on answers with no reasoning block, and a recruiter waiting on first token does
# not want one, so it is switched off.
TEMPLATE_KW = {}
try:
    tokenizer.apply_chat_template([{"role": "user", "content": "x"}],
                                  add_generation_prompt=True, tokenize=False,
                                  enable_thinking=False)
    TEMPLATE_KW["enable_thinking"] = False
except TypeError:
    pass


# ------------------------------------------------------------------ retrieval

class Retriever:
    """Brute-force cosine over the 806 corpus chunks.

    The build plan specifies FAISS. At 806 vectors an exact numpy dot product is
    faster than building an index, has no extra dependency to pin on the Space,
    and cannot silently return approximate neighbours — so that is what this
    does. The embedding model is the one the plan names.
    """

    def __init__(self, index_dir: Path):
        import numpy as np
        from sentence_transformers import SentenceTransformer

        self.np = np
        meta = json.loads((index_dir / "chunks.json").read_text())
        self.texts = meta["texts"]
        self.titles = meta["titles"]
        self.vectors = np.load(index_dir / "embeddings.npy")   # already L2-normalised
        self.encoder = SentenceTransformer(meta["model"])
        # HYBRID, and both halves are load-bearing. Measured:
        #   Q&A only k=8   short-fact recall 10/10, blog long tail 1/23
        #   hybrid 8+3     short-fact recall 10/10, blog long tail 21/23
        # The Q&A pairs answer the short factual questions; the corpus chunks are
        # the only route to the 215 blog posts, which ~2.4 pairs each cannot
        # cover. Dropping the chunks scores perfectly on the evaluation and
        # leaves the model unable to discuss half of what Marc has written.
        kinds = meta.get("kinds") or ["qa"] * len(self.texts)
        self.qa_mask = np.array([k == "qa" for k in kinds])
        if not self.qa_mask.any():
            self.qa_mask = np.ones(len(self.texts), dtype=bool)

    def top(self, query: str, k: int) -> list[tuple[str, str]]:
        np_ = self.np
        q = self.encoder.encode([query], normalize_embeddings=True)[0]
        scores = self.vectors @ q
        if RAG_KINDS == "all":
            idx = np_.argsort(-scores)[:k]
        else:
            idx = list(np_.argsort(-np_.where(self.qa_mask, scores, -9.0))[:k])
            if RAG_KINDS == "hybrid" and RAG_CHUNKS > 0:
                idx += list(np_.argsort(-np_.where(~self.qa_mask, scores, -9.0))[:RAG_CHUNKS])
        return [(self.titles[i], self.texts[i]) for i in idx]


retriever: Retriever | None = None
if RAG_ENABLED:
    index_dir = HERE / "index"
    if (index_dir / "embeddings.npy").exists():
        retriever = Retriever(index_dir)
    else:
        print("RAG_ENABLED but space/index/ is empty; running fine-tune only")


# ----------------------------------------------------------------- generation

def build_messages(message: str, history: list | None) -> list[dict]:
    """Assemble the prompt. The system message is injected here and here only —
    a client-supplied one is never trusted."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for item in (history or [])[-MAX_HISTORY_TURNS * 2:]:
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}:
            messages.append({"role": item["role"], "content": str(item["content"])})
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            u, a = item
            if u:
                messages.append({"role": "user", "content": str(u)})
            if a:
                messages.append({"role": "assistant", "content": str(a)})

    user = message
    if retriever is not None:
        chunks = retriever.top(message, RAG_TOP_K)
        context = "\n\n".join(f"[{t}]\n{c}" for t, c in chunks)
        # The fine-tune owns voice; retrieval owns exact numbers. Context goes in
        # the user turn, above the question, which is how the training records
        # present quoted source material.
        user = (f"<context>\n{context}\n</context>\n\n"
                f"Using the context above only where it is relevant, answer: {message}")
    messages.append({"role": "user", "content": user})
    return messages


@spaces.GPU(duration=60)
def chat(message: str, history: list | None = None, temperature: float = 0.7,
         top_p: float = 0.9, repetition_penalty: float = 1.05,
         max_new_tokens: int = 512):
    """Stream an answer. Yields the answer *so far* on each step, which is what
    Gradio's streaming contract expects and what hf_client.py diffs into deltas."""
    message = (message or "").strip()
    if not message:
        yield ""
        return

    text = tokenizer.apply_chat_template(
        build_messages(message, history), add_generation_prompt=True,
        tokenize=False, **TEMPLATE_KW,
    )
    inputs = tokenizer([text], return_tensors="pt").to(model.device)

    streamer = TextIteratorStreamer(tokenizer, skip_prompt=True,
                                    skip_special_tokens=True)
    kwargs = dict(
        **inputs,
        streamer=streamer,
        max_new_tokens=int(max_new_tokens),
        do_sample=temperature > 0,
        temperature=float(temperature) if temperature > 0 else None,
        top_p=float(top_p),
        repetition_penalty=float(repetition_penalty),
        pad_token_id=tokenizer.eos_token_id,
    )
    thread = threading.Thread(target=model.generate, kwargs=kwargs)
    thread.start()

    out = ""
    for piece in streamer:
        out += piece
        # A hybrid model that ignores enable_thinking would leak a reasoning
        # block into the transcript; hold it back until the block closes.
        if "<think>" in out and "</think>" not in out:
            continue
        yield out.split("</think>")[-1].lstrip()
    thread.join()


# ------------------------------------------------------------------------- UI

# Built in ui.py so the API contract can be tested on a laptop, without loading
# an 8B model. Two production-only contract bugs is two too many; see ui.py.
demo = build_demo(chat, base_model=BASE_MODEL, adapter=ADAPTER,
                  retrieval_on=retriever is not None)

if __name__ == "__main__":
    demo.queue(max_size=32).launch(ssr_mode=False)
