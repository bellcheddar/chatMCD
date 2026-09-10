#!/usr/bin/env python3
"""Audit the evaluation set against the corpus itself.

Every number this project reports depends on these assertions being right, and
they have been wrong twice: a schooling question sat in the honesty bucket while
the corpus named the school, and the decline vocabulary rejected three perfectly
correct declines. Both inflated or deflated scores silently.

Four checks, none of which need a model:

  answerable   For facts / depth / personality / web, every expected string must
               appear somewhere in corpus/ or qa/. If it does not, the question
               cannot be answered from the material and is scoring the model for
               something it was never given.

  unanswerable For honesty, the expected answer must NOT appear anywhere. An
               "unanswerable" question the corpus actually answers penalises a
               correct answer and caps the honesty score.

  forbid       No forbid pattern may match the corpus's own wording. A forbid
               that matches a true statement rejects a correct answer.

  reachable    Every expected string must be retrievable under the configured
               retrieval settings, or no amount of prompting will produce it.

    python3 eval/audit_questions.py
    python3 eval/audit_questions.py --no-retrieval    # skip the slow check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BUCKETS_ANSWERABLE = {"facts", "depth", "personality", "web"}

PROBLEMS: list[tuple[str, str, str]] = []


def flag(severity: str, qid: str, detail: str) -> None:
    PROBLEMS.append((severity, qid, detail))


def load_corpus() -> str:
    """Everything the model could possibly have learned or retrieved."""
    parts = []
    for sub in ("corpus", "qa"):
        for f in (ROOT / "training" / sub).rglob("*.md"):
            parts.append(f.read_text(errors="ignore"))
    return "\n".join(parts)


def patterns_of(entry) -> list[str]:
    return list(entry["any"]) if isinstance(entry, dict) else [str(entry)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-retrieval", action="store_true")
    ap.add_argument("--rag-top-k", type=int, default=8)
    ap.add_argument("--rag-chunks", type=int, default=3)
    args = ap.parse_args()

    spec = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text())
    questions = spec["questions"]
    corpus = load_corpus()
    print(f"corpus + qa: {len(corpus) / 1e6:.2f} M chars")
    print(f"questions:   {len(questions)}\n")

    # ---------------------------------------------------------- answerable
    print("== answerable: every expected string must exist in the material ==")
    for q in questions:
        if q["bucket"] not in BUCKETS_ANSWERABLE:
            continue
        for entry in q.get("expect", []):
            pats = patterns_of(entry)
            if not any(re.search(p, corpus, re.I) for p in pats):
                flag("BLOCKER", q["id"],
                     f"expects {pats[0]!r} which appears nowhere in corpus/ or qa/ "
                     f"— unanswerable, so this question can never pass")
    print(f"   {sum(1 for s, _, _ in PROBLEMS if s == 'BLOCKER')} unanswerable assertions")

    # -------------------------------------------------------- unanswerable
    print("\n== honesty: the expected answer must NOT exist in the material ==")
    n_before = len(PROBLEMS)
    for q in questions:
        if q["bucket"] != "honesty":
            continue
        # Pull the distinctive nouns out of the question and look for them
        # alongside Marc. A hit means the corpus may well answer it.
        words = [w for w in re.findall(r"[A-Za-z][a-z]{4,}", q["q"])
                 if w.lower() not in {"marc", "deller", "which", "where", "what",
                                      "many", "times", "given", "about", "does",
                                      "name", "year"}]
        for w in words:
            hits = len(re.findall(rf"\b{re.escape(w)}\b", corpus, re.I))
            if hits >= 3:
                flag("REVIEW", q["id"],
                     f"'{w}' occurs {hits}x in the material — verify this question "
                     f"is genuinely unanswerable before trusting the honesty score")
    print(f"   {len(PROBLEMS) - n_before} honesty topics to re-verify")

    # --------------------------------------------------------------- forbid
    print("\n== forbid: no pattern may match the corpus's own wording ==")
    n_before = len(PROBLEMS)
    for q in questions:
        for pat in q.get("forbid", []):
            try:
                m = re.search(pat, corpus, re.I)
            except re.error as e:
                flag("BLOCKER", q["id"], f"forbid {pat!r} does not compile: {e}")
                continue
            if m and q["bucket"] in BUCKETS_ANSWERABLE:
                flag("REVIEW", q["id"],
                     f"forbid {pat!r} matches the material at {m.group(0)[:40]!r} "
                     f"— it may reject a correct answer")
    print(f"   {len(PROBLEMS) - n_before} suspect forbid patterns")

    # ------------------------------------------------------------ reachable
    if not args.no_retrieval:
        print("\n== reachable: expected strings must survive retrieval ==")
        n_before = len(PROBLEMS)
        index = ROOT / "space" / "index"
        if not (index / "embeddings.npy").exists():
            flag("REVIEW", "-", "no retrieval index; run scripts/build_rag_index.py")
        else:
            import numpy as np
            from sentence_transformers import SentenceTransformer

            meta = json.loads((index / "chunks.json").read_text())
            V = np.load(index / "embeddings.npy")
            texts = meta["texts"]
            kinds = meta.get("kinds") or ["qa"] * len(texts)
            qa_mask = np.array([k == "qa" for k in kinds])
            enc = SentenceTransformer(meta["model"])
            for q in questions:
                if q["bucket"] not in BUCKETS_ANSWERABLE:
                    continue
                v = enc.encode([q["q"]], normalize_embeddings=True)[0]
                s = V @ v
                idx = list(np.argsort(-np.where(qa_mask, s, -9))[: args.rag_top_k])
                idx += list(np.argsort(-np.where(~qa_mask, s, -9))[: args.rag_chunks])
                blob = " ".join(texts[i] for i in idx)
                for entry in q.get("expect", []):
                    pats = patterns_of(entry)
                    if not any(re.search(p, blob, re.I) for p in pats):
                        flag("INFO", q["id"],
                             f"{pats[0]!r} is in the material but not retrieved at "
                             f"top-{args.rag_top_k}+{args.rag_chunks}")
        print(f"   {len(PROBLEMS) - n_before} retrieval gaps")

    # ---------------------------------------------------------------- report
    print("\n" + "=" * 70)
    for sev in ("BLOCKER", "REVIEW", "INFO"):
        rows = [p for p in PROBLEMS if p[0] == sev]
        if not rows:
            continue
        print(f"\n{sev} ({len(rows)})")
        for _, qid, detail in rows:
            print(f"  {qid:<24} {detail}")

    blockers = sum(1 for s, _, _ in PROBLEMS if s == "BLOCKER")
    print(f"\n{blockers} blockers, "
          f"{sum(1 for s, _, _ in PROBLEMS if s == 'REVIEW')} to review, "
          f"{sum(1 for s, _, _ in PROBLEMS if s == 'INFO')} informational")
    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
