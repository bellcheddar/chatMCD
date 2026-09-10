#!/usr/bin/env python3
"""chatMCD evaluation harness.

Runs eval/questions.yaml against a model and scores every assertion by regex.
Three backends, all usable from this Mac:

    mlx   local MLX model, optionally + a LoRA adapter directory   (iteration)
    hf    local transformers model, optionally + a PEFT adapter    (the deployable artefact)
    api   an HTTP endpoint that returns the answer                 (the live Space / Flask app)

Generation is separated from scoring: answers are cached to JSON so the
assertions can be tightened and re-scored without paying for generation again.

    python3 eval/run_eval.py --backend mlx --model Qwen/Qwen3-8B --label base
    python3 eval/run_eval.py --backend mlx --model Qwen/Qwen3-8B \
        --adapter adapters/chatmcd-qwen3-8b-round01 --label round01
    python3 eval/run_eval.py --score-only --answers eval/answers/round01.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT = (ROOT / "training" / "system_prompt.md").read_text().strip()


class Retriever:
    """Brute-force cosine over the corpus chunks, identical to the Space's.

    Retrieval has to be measurable here or the decision to enable it is a guess.
    Keeping the prompt construction byte-identical to space/app.py matters: an
    A/B that assembles context differently from production measures something
    production will never do.
    """

    def __init__(self, index_dir: Path, top_k: int, kinds: str = "hybrid",
                 n_chunks: int = 3):
        import numpy as np
        from sentence_transformers import SentenceTransformer

        self.np, self.k = np, top_k
        meta = json.loads((index_dir / "chunks.json").read_text())
        self.texts, self.titles = meta["texts"], meta["titles"]
        self.vectors = np.load(index_dir / "embeddings.npy")
        self.encoder = SentenceTransformer(meta["model"])
        # Which units may be retrieved. Measured on the questions retrieval got
        # wrong: top-8 over Q&A units alone recovers as many as top-12 over
        # everything, and does it with much shorter prompts, because a Q&A pair
        # is ~300 tokens where a corpus chunk is ~1,590.
        # HYBRID, and both halves are load-bearing. Measured:
        #   Q&A only k=8   short-fact recall 10/10, blog long tail 1/23
        #   hybrid 8+3     short-fact recall 10/10, blog long tail 21/23
        # The Q&A pairs answer the short factual questions; the corpus chunks are
        # the only route to the 215 blog posts, which ~2.4 pairs each cannot
        # cover. Dropping the chunks scores perfectly on the evaluation and
        # leaves the model unable to discuss half of what Marc has written.
        self.kinds = kinds
        self.n_chunks = n_chunks
        all_kinds = meta.get("kinds") or ["qa"] * len(self.texts)
        self.qa_mask = np.array([k == "qa" for k in all_kinds])
        if not self.qa_mask.any():
            self.qa_mask = np.ones(len(self.texts), dtype=bool)

    def _select(self, sims):
        np_ = self.np
        if self.kinds == "all":
            return np_.argsort(-sims)[: self.k]
        if self.kinds == "chunk":
            return np_.argsort(-np_.where(~self.qa_mask, sims, -9.0))[: self.k]
        qa = np_.argsort(-np_.where(self.qa_mask, sims, -9.0))[: self.k]
        if self.kinds == "qa" or self.n_chunks <= 0:
            return qa
        ch = np_.argsort(-np_.where(~self.qa_mask, sims, -9.0))[: self.n_chunks]
        return list(qa) + list(ch)

    def wrap(self, message: str) -> str:
        q = self.encoder.encode([message], normalize_embeddings=True)[0]
        idx = self._select(self.vectors @ q)
        context = "\n\n".join(f"[{self.titles[i]}]\n{self.texts[i]}" for i in idx)
        return (f"<context>\n{context}\n</context>\n\n"
                f"Using the context above only where it is relevant, answer: {message}")


BUCKETS = ["facts", "depth", "personality", "web", "manners", "honesty"]

# Qwen3's hybrid models emit a reasoning block the chat UI never shows. It is
# not part of the answer, so it is not part of what we score.
THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def strip_think(text: str) -> str:
    text = THINK_RE.sub("", text)
    # An unterminated block (hit the token cap mid-thought) leaves a dangling tag.
    if "<think>" in text:
        text = text.split("<think>")[0]
    return text.strip()


# --------------------------------------------------------------------- scoring


@dataclass
class Assertion:
    """One `expect` entry: a single regex, or an any-of group."""

    patterns: list[str]
    any_of: bool

    @classmethod
    def parse(cls, item) -> "Assertion":
        if isinstance(item, dict):
            return cls(patterns=list(item["any"]), any_of=True)
        return cls(patterns=[str(item)], any_of=False)

    def check(self, answer: str) -> bool:
        hits = [bool(re.search(p, answer, re.I)) for p in self.patterns]
        return any(hits) if self.any_of else all(hits)

    def label(self) -> str:
        return " | ".join(self.patterns) if self.any_of else self.patterns[0]


@dataclass
class Result:
    qid: str
    bucket: str
    question: str
    answer: str
    passed: bool
    missed: list[str] = field(default_factory=list)
    tripped: list[str] = field(default_factory=list)
    latency: float = 0.0


# chatMCD speaks ABOUT Marc, never as him. This is the one failure that would be
# screenshotted, and it appeared under an off-topic request while the direct
# "are you Marc?" question still passed — so it is checked on every question that
# opts in, not left to a single identity item.
FIRST_PERSON = re.compile(
    r"\bI(?:'m| am) Marc\b|\bI(?:'m| am) Dr\.? Deller\b|"
    r"\bmy (?:work|research|career|patents|structures|thesis|lab|company)\b",
    re.I)


# Typographic characters a language model emits and a hand-written regex does
# not contain. `don'?t` does not match `don\u2019t`, and the model writes the
# curly one about half the time -- so a question the model declined perfectly was
# scored a failure, while man-poem survived only by matching a different
# alternative in the same list. That is the fourth time the decline vocabulary
# has scored a correct answer wrong, and the first three were fixed by adding
# more phrasings, which would never have caught this one.
#
# Normalise the ANSWER, not the patterns: the patterns are hand-written and can
# be held to plain ASCII, and normalising one string is checkable in a way that
# rewriting sixty regexes is not.
SMART = {
    "\u2019": "'", "\u2018": "'", "\u201b": "'",      # single quotes
    "\u201c": '"', "\u201d": '"', "\u201e": '"',      # double quotes
    "\u2013": "-", "\u2014": "-", "\u2212": "-",      # dashes
    "\u2026": "...",                                    # ellipsis
    "\u00a0": " ", "\u202f": " ", "\u2009": " ",      # non-breaking spaces
}


def normalise(text: str) -> str:
    for bad, good in SMART.items():
        text = text.replace(bad, good)
    return text


def score_one(spec: dict, answer: str) -> Result:
    missed, tripped = [], []
    # Scored on the normalised text; the report shows what the model really said.
    scored = normalise(answer)
    if spec.get("persona_guard"):
        m = FIRST_PERSON.search(scored)
        if m:
            tripped.append(f"PERSONA BREAK: spoke as Marc ({m.group(0)!r})")
    for item in spec.get("expect", []):
        a = Assertion.parse(item)
        if not a.check(scored):
            missed.append(a.label())
    for pattern in spec.get("forbid", []):
        if re.search(pattern, scored, re.I):
            tripped.append(pattern)
    return Result(
        qid=spec["id"],
        bucket=spec["bucket"],
        question=spec["q"],
        answer=answer,
        passed=not missed and not tripped,
        missed=missed,
        tripped=tripped,
    )


# -------------------------------------------------------------------- backends


class MLXBackend:
    retriever = None
    name = "mlx"

    def __init__(self, model: str, adapter: str | None, max_tokens: int, temp: float,
                 thinking: bool):
        from mlx_lm import load

        self.max_tokens = max_tokens
        self.temp = temp
        self.thinking = thinking
        kwargs = {"adapter_path": adapter} if adapter else {}
        self.model, self.tokenizer = load(model, **kwargs)

    def _prompt(self, question: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        try:
            return self.tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, tokenize=False,
                enable_thinking=self.thinking,
            )
        except TypeError:
            # Templates without an enable_thinking switch.
            return self.tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, tokenize=False,
            )

    def ask(self, question: str) -> str:
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        if self.retriever:
            question = self.retriever.wrap(question)

        sampler = make_sampler(temp=self.temp, top_p=0.9)
        out = generate(
            self.model, self.tokenizer, prompt=self._prompt(question),
            max_tokens=self.max_tokens, sampler=sampler, verbose=False,
        )
        return strip_think(out)


class HFBackend:
    retriever = None
    name = "hf"

    def __init__(self, model: str, adapter: str | None, max_tokens: int, temp: float,
                 thinking: bool, device: str):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.max_tokens = max_tokens
        self.temp = temp
        self.thinking = thinking
        self.device = device
        dtype = torch.float32 if device == "cpu" else torch.bfloat16
        self.tokenizer = AutoTokenizer.from_pretrained(model)
        self.model = AutoModelForCausalLM.from_pretrained(model, dtype=dtype)
        if adapter:
            from peft import PeftModel

            self.model = PeftModel.from_pretrained(self.model, adapter)
        self.model.to(device).eval()

    def ask(self, question: str) -> str:
        if self.retriever:
            question = self.retriever.wrap(question)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        try:
            text = self.tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, tokenize=False,
                enable_thinking=self.thinking,
            )
        except TypeError:
            text = self.tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, tokenize=False,
            )
        ids = self.tokenizer([text], return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            out = self.model.generate(
                **ids, max_new_tokens=self.max_tokens, do_sample=self.temp > 0,
                temperature=self.temp or None, top_p=0.9,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        new = out[0][ids["input_ids"].shape[1]:]
        return strip_think(self.tokenizer.decode(new, skip_special_tokens=True))


class APIBackend:
    name = "api"

    def __init__(self, url: str, max_tokens: int, temp: float, **_):
        self.url = url
        self.max_tokens = max_tokens
        self.temp = temp

    def ask(self, question: str) -> str:
        import urllib.request

        payload = json.dumps({
            "message": question, "history": [],
            "max_tokens": self.max_tokens, "temperature": self.temp,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=180) as r:
            body = json.loads(r.read())
        # Accept the shapes the Flask app and the Space each return.
        for key in ("answer", "reply", "content", "text"):
            if isinstance(body, dict) and key in body:
                return strip_think(str(body[key]))
        return strip_think(json.dumps(body))


# ---------------------------------------------------------------------- report


def build_report(label: str, backend: str, model: str, adapter: str | None,
                 results: list[Result], thresholds: dict, wall: float) -> tuple[str, dict]:
    by_bucket: dict[str, list[Result]] = {b: [] for b in BUCKETS}
    for r in results:
        by_bucket.setdefault(r.bucket, []).append(r)

    lines = [
        f"# chatMCD eval — {label}",
        "",
        f"- **backend** `{backend}`",
        f"- **model** `{model}`",
        f"- **adapter** `{adapter or '—'}`",
        f"- **questions** {len(results)}",
        f"- **wall clock** {wall:.0f} s ({wall / max(len(results), 1):.1f} s/question)",
        "",
        "## Scores by bucket",
        "",
        "| Bucket | Passed | Total | Rate |",
        "|---|---:|---:|---:|",
    ]
    summary = {}
    for b in BUCKETS:
        rs = by_bucket.get(b, [])
        if not rs:
            continue
        p = sum(r.passed for r in rs)
        summary[b] = {"passed": p, "total": len(rs), "rate": p / len(rs)}
        lines.append(f"| {b} | {p} | {len(rs)} | {p / len(rs):.0%} |")
    total_p = sum(r.passed for r in results)
    summary["overall"] = {
        "passed": total_p, "total": len(results), "rate": total_p / max(len(results), 1)
    }
    lines.append(f"| **overall** | **{total_p}** | **{len(results)}** "
                 f"| **{total_p / max(len(results), 1):.0%}** |")

    facts_rate = summary.get("facts", {}).get("rate", 0.0)
    honesty_pass = summary.get("honesty", {}).get("passed", 0)
    need_facts = thresholds.get("facts_accuracy", 0.90)
    need_honesty = thresholds.get("honesty_pass", 3)
    gate_ok = facts_rate >= need_facts and honesty_pass >= need_honesty
    summary["gate"] = {
        "facts_accuracy": facts_rate, "facts_required": need_facts,
        "honesty_pass": honesty_pass, "honesty_required": need_honesty,
        "passed": gate_ok,
    }
    lines += [
        "",
        "## Phase 1 gate",
        "",
        f"- facts accuracy **{facts_rate:.0%}** (need ≥ {need_facts:.0%}) — "
        f"{'PASS' if facts_rate >= need_facts else 'FAIL'}",
        f"- honesty **{honesty_pass}/{need_honesty}** — "
        f"{'PASS' if honesty_pass >= need_honesty else 'FAIL'}",
        "",
        f"**{'GATE PASSED' if gate_ok else 'GATE NOT PASSED'}**",
        "",
        "## Per-question",
        "",
        "| | id | bucket | question | missing / tripped |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        note = ""
        if r.missed:
            note += "missing: " + "; ".join(f"`{m}`" for m in r.missed)
        if r.tripped:
            note += (" " if note else "") + "tripped: " + "; ".join(f"`{t}`" for t in r.tripped)
        q = r.question.replace("|", "\\|")
        lines.append(f"| {'✅' if r.passed else '❌'} | `{r.qid}` | {r.bucket} | {q} | "
                     f"{note.replace('|', '\\|')} |")

    fails = [r for r in results if not r.passed]
    if fails:
        lines += ["", "## Failing answers", ""]
        for r in fails:
            lines += [f"### `{r.qid}` — {r.question}", "", "> " +
                      r.answer.replace("\n", "\n> ")[:1500], ""]
    return "\n".join(lines) + "\n", summary


# ------------------------------------------------------------------------ main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["mlx", "hf", "api"], default="mlx")
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--adapter")
    ap.add_argument("--url", help="api backend endpoint")
    ap.add_argument("--device", default="mps", help="hf backend device")
    ap.add_argument("--label", default="run")
    ap.add_argument("--questions", default=str(ROOT / "eval" / "questions.yaml"))
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--temp", type=float, default=0.0,
                    help="0 = greedy. Eval is deterministic by default.")
    ap.add_argument("--thinking", action="store_true",
                    help="let a hybrid model emit <think> blocks (they are stripped anyway)")
    ap.add_argument("--bucket", action="append", help="restrict to one or more buckets")
    ap.add_argument("--rag", action="store_true",
                    help="prepend retrieved corpus chunks, exactly as the Space does")
    ap.add_argument("--rag-top-k", type=int, default=8)
    ap.add_argument("--rag-kinds", default="hybrid",
                    choices=["hybrid", "qa", "chunk", "all"],
                    help="hybrid = top-k Q&A pairs plus --rag-chunks corpus chunks")
    ap.add_argument("--rag-chunks", type=int, default=3,
                    help="corpus chunks added in hybrid mode; these are the only "
                         "route to the 215 blog posts")
    ap.add_argument("--index", default=str(ROOT / "space" / "index"))
    ap.add_argument("--answers", help="answers JSON to write (or read with --score-only)")
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--out", help="report markdown path")
    args = ap.parse_args()

    spec = yaml.safe_load(Path(args.questions).read_text())
    questions = spec["questions"]
    if args.bucket:
        questions = [q for q in questions if q["bucket"] in args.bucket]
    thresholds = spec.get("meta", {}).get("thresholds", {})

    answers_path = Path(args.answers) if args.answers else \
        ROOT / "eval" / "answers" / f"{args.label}.json"
    answers_path.parent.mkdir(parents=True, exist_ok=True)

    if args.score_only:
        cached = json.loads(answers_path.read_text())
        answers = cached["answers"]
        wall = cached.get("wall", 0.0)
        meta = cached.get("meta", {})
        backend, model, adapter = meta.get("backend", "?"), meta.get("model", "?"), meta.get("adapter")
    else:
        if args.backend == "mlx":
            be = MLXBackend(args.model, args.adapter, args.max_tokens, args.temp, args.thinking)
        elif args.backend == "hf":
            be = HFBackend(args.model, args.adapter, args.max_tokens, args.temp,
                           args.thinking, args.device)
        else:
            if not args.url:
                ap.error("--backend api needs --url")
            be = APIBackend(args.url, args.max_tokens, args.temp)
        if args.rag:
            index = Path(args.index)
            if not (index / "embeddings.npy").exists():
                ap.error(f"no index at {index}; run scripts/build_rag_index.py")
            be.retriever = Retriever(index, args.rag_top_k, args.rag_kinds,
                                     args.rag_chunks)
            extra = f" + {args.rag_chunks} chunks" if args.rag_kinds == "hybrid" else ""
            print(f"retrieval ON: top-{args.rag_top_k} {args.rag_kinds}{extra} "
                  f"from {index}")
        backend, model, adapter = be.name, args.model, args.adapter

        answers, t0 = {}, time.time()
        for i, q in enumerate(questions, 1):
            qt = time.time()
            try:
                a = be.ask(q["q"])
            except Exception as e:  # a broken generation is a failed question, not a crash
                a = f"<<GENERATION ERROR: {type(e).__name__}: {e}>>"
            answers[q["id"]] = a
            print(f"[{i:2d}/{len(questions)}] {q['id']:<26} {time.time() - qt:5.1f}s  "
                  f"{a[:70].replace(chr(10), ' ')}", flush=True)
        wall = time.time() - t0
        answers_path.write_text(json.dumps(
            {"meta": {"backend": backend, "model": model, "adapter": adapter,
                      "label": args.label, "temp": args.temp,
                      "rag": args.rag,
                      "rag_top_k": args.rag_top_k if args.rag else None,
                      "rag_kinds": args.rag_kinds if args.rag else None},
             "wall": wall, "answers": answers}, indent=2))

    results = [score_one(q, answers.get(q["id"], "")) for q in questions]
    report, summary = build_report(args.label, backend, model, adapter, results,
                                   thresholds, wall)

    out = Path(args.out) if args.out else ROOT / "eval" / "reports" / f"{args.label}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    out.with_suffix(".json").write_text(json.dumps(summary, indent=2))

    print()
    for b in BUCKETS:
        if b in summary:
            s = summary[b]
            print(f"  {b:<12} {s['passed']:>2}/{s['total']:<2}  {s['rate']:>4.0%}")
    o = summary["overall"]
    print(f"  {'OVERALL':<12} {o['passed']:>2}/{o['total']:<2}  {o['rate']:>4.0%}")
    print(f"\n  gate: {'PASSED' if summary['gate']['passed'] else 'NOT PASSED'}")
    print(f"  report: {out}")
    return 0 if summary["gate"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
