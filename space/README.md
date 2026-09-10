---
title: chatMCD API
emoji: 🔬
colorFrom: indigo
colorTo: gray
sdk: gradio
sdk_version: 5.49.1
app_file: app.py
pinned: false
license: apache-2.0
short_description: Inference endpoint for chatMCD, Marc C. Deller's assistant
---

# chatMCD — inference endpoint

Answers questions about [Marc C. Deller, D.Phil.](https://marcdeller.com) in the
third person, by retrieving from his own writing.

This Space is a **debug surface**. The product is
[chatmcd.mdeller.com](https://chatmcd.mdeller.com), a Flask app that holds the
Hugging Face token and streams from the `/chat` endpoint here.

## Provenance

Everything was produced on a 64 GB M1 Max:

| Stage | Where | Tool |
|---|---|---|
| Corpus and Q&A | local | `training/build_jsonl.py` |
| Retrieval index | local | `scripts/build_rag_index.py` |
| LoRA voice layer (optional) | local | `mlx_lm.lora` |
| Parity verification | local | `convert/verify_parity.py` |
| Inference | here | `transformers` + `peft` on ZeroGPU |

No rented GPU was used at any point. ZeroGPU is inference only and cannot load
an MLX adapter, so the adapter is converted rather than retrained: the two
produce byte-identical delta weights, which `verify_parity.py` checks against
the trained tensors and against the model's own logits.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `BASE_MODEL` | `Qwen/Qwen3-8B` | base checkpoint |
| `ADAPTER` | `Dellboy/chatmcd-8b` | optional voice layer, merged at load; set empty to serve the base model |
| `RAG_ENABLED` | `1` | retrieval is the primary mechanism, not a fallback |
| `RAG_TOP_K` | `8` | Q&A pairs retrieved per query |
| `RAG_KINDS` | `hybrid` | `hybrid` = Q&A pairs plus corpus chunks |
| `RAG_CHUNKS` | `3` | corpus chunks added in hybrid mode; the only route to the 215 blog posts |

## Scope

chatMCD answers about Marc only, and says so rather than guessing when a
question falls outside what it was trained on.
