#!/usr/bin/env python3
"""Build the retrieval index for the Space, locally.

Embeds every corpus chunk with sentence-transformers/all-MiniLM-L6-v2 and writes
an L2-normalised float32 matrix plus the chunk texts. The Space does a brute-force
cosine over it: at ~800 vectors that is exact and instant, so there is no index
structure to build and nothing to go approximately wrong.

TWO SOURCES, and the Q&A pairs matter more than the prose.

The first version of this indexed only training/mlx/text — the corpus chunks.
Measured against the questions the fine-tune gets wrong, that index retrieved the
answer for 4 of 11 at top-3. Indexing the hand-written Q&A pairs instead gets
10 of 11 at top-3 and 11 of 11 at top-5.

The reason is embedding geometry, not content: the facts are in both, but a
1,590-token document chunk is a diffuse target for a short question, while
"Q: What is Elora Therapeutics? A: A pre-seed biotech..." is a near exact match
for the query. The Q&A pairs are already question-shaped, which is
what a question embedding wants.

Both are indexed, with the Q&A first. The chunks still earn their place for the
long tail of 215 blog posts that ~2.4 Q&A pairs each cannot cover.

    python3 scripts/build_rag_index.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Each chunk opens with "# <title>\nAuthors: ...\nSource: ..." from chunk_document().
TITLE_RE = re.compile(r"^#\s*(.+?)\s*$", re.M)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "space" / "index"))
    ap.add_argument("--batch-size", type=int, default=64)
    args = ap.parse_args()

    import numpy as np
    from sentence_transformers import SentenceTransformer

    texts, titles, kinds, seen = [], [], [], set()

    # 1. The hand-written Q&A pairs. Short, question-shaped, densely factual.
    for split in ("train", "valid", "test"):
        path = ROOT / "training" / "mlx" / "chat" / f"{split}.jsonl"
        if not path.exists():
            sys.exit(f"missing {path} — run training/build_jsonl.py first")
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            m = json.loads(line)["messages"]
            q, a = m[1]["content"].strip(), m[2]["content"].strip()
            # The corpus chunks are also present in mlx/chat, wrapped as
            # "Share the full text of ..." turns. Skip them here; they are
            # indexed properly in step 2 below.
            if q.startswith("Share the full text of"):
                continue
            text = f"Q: {q}\nA: {a}"
            if text in seen:
                continue
            seen.add(text)
            texts.append(text)
            titles.append(q[:90])
            kinds.append("qa")
    n_qa = len(texts)

    # 2. The corpus chunks, for the long tail the Q&A cannot cover: 215 blog
    #    posts against about 2.4 Q&A pairs each.
    for split in ("train", "valid", "test"):
        path = ROOT / "training" / "mlx" / "text" / f"{split}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            text = json.loads(line)["text"]
            if text in seen:
                continue
            seen.add(text)
            m = TITLE_RE.search(text)
            titles.append(m.group(1) if m else "chatMCD corpus")
            texts.append(text)
            kinds.append("chunk")

    print(f"{n_qa} Q&A pairs + {len(texts) - n_qa} corpus chunks = {len(texts)} units, "
          f"{sum(len(t) for t in texts) / 1e6:.2f} M chars")
    encoder = SentenceTransformer(MODEL)
    vectors = encoder.encode(texts, batch_size=args.batch_size,
                             normalize_embeddings=True, show_progress_bar=True)
    vectors = np.asarray(vectors, dtype="float32")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "embeddings.npy", vectors)
    (out / "chunks.json").write_text(json.dumps(
        {"model": MODEL, "titles": titles, "texts": texts, "kinds": kinds}))

    size = sum(f.stat().st_size for f in out.iterdir()) / 1e6
    print(f"wrote {out}  ({vectors.shape[0]} x {vectors.shape[1]}, {size:.1f} MB)")

    # Sanity: a query with an obvious answer must retrieve the obvious chunk.
    q = encoder.encode(["What is the PDB ID of the oncostatin M structure?"],
                       normalize_embeddings=True)[0]
    top = np.argsort(-(vectors @ q))[:3]
    print("\nspot check — 'PDB ID of the oncostatin M structure':")
    for i in top:
        print(f"  {float(vectors[i] @ q):.3f}  {titles[i][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
