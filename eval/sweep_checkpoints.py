#!/usr/bin/env python3
"""Score every checkpoint in a training run and promote the best one.

The last checkpoint is not the best checkpoint. Validation loss and fidelity to
the training set keep improving after downstream accuracy has peaked and started
to fall, so a run that looks monotonically better in Weights & Biases can still
be past its best on the questions a visitor will actually ask. The only way to
know is to score them.

Checkpoint finely, search coarsely. `save_every: 50` on a 2,000-iteration run is
40 checkpoints; screening all of them on the gate buckets is about two hours, and
most of that is spent far from the peak. So the search is coarse-to-fine:

  coarse   screen a strided subset (default ~8 checkpoints)
  refine   halve the stride and screen around the current best *region*, repeat
           until the stride is 1
  confirm  the full 40 questions on the top few, so the promoted checkpoint has
           a complete report and not just a screen score

The screen uses 35 of the 40 questions, not the 16 gate ones. A screen's
resolution is 1/n: with 16 questions the score moves in 6.25% steps, adjacent
checkpoints tie, and the search resolves the tie by scoring order. Widening the
screen buys more than adding checkpoints does. See eval/test_sweep_search.py.

That is O(log n) passes instead of O(n), and it resolves the peak to a single
checkpoint. It assumes the accuracy-vs-iteration curve is roughly unimodal, which
is the normal shape; `--exhaustive` scores every checkpoint when that assumption
is not safe, or when the whole curve is wanted for a write-up.

    python3 eval/sweep_checkpoints.py adapters/chatmcd-qwen3-8b-round01
    python3 eval/sweep_checkpoints.py adapters/<run> --confirm-top 3
    python3 eval/sweep_checkpoints.py adapters/<run> --report   # re-read results
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CKPT = re.compile(r"^(\d+)_adapters\.safetensors$")


def checkpoints(run: Path) -> list[tuple[int, Path]]:
    """Every numbered checkpoint, plus the final adapter as the last entry."""
    out = []
    for f in run.iterdir():
        m = CKPT.match(f.name)
        if m:
            out.append((int(m.group(1)), f))
    out.sort()
    final = run / "adapters.safetensors"
    if final.exists() and (not out or final.stat().st_size != out[-1][1].stat().st_size
                           or final.read_bytes() != out[-1][1].read_bytes()):
        # The final save is only a distinct checkpoint if it differs from the
        # last numbered one; mlx-lm often writes both at the same iteration.
        out.append((10 ** 9, final))
    return out


def stage(run: Path, ckpt: Path, tmp: Path) -> Path:
    """mlx-lm loads `adapters.safetensors` from the adapter directory, so each
    checkpoint has to be staged into a directory of its own."""
    d = tmp / ckpt.stem
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(run / "adapter_config.json", d / "adapter_config.json")
    shutil.copy(ckpt, d / "adapters.safetensors")
    return d


def run_eval(model: str, adapter: Path, label: str, buckets: list[str] | None,
             max_tokens: int) -> dict:
    cmd = [sys.executable, str(ROOT / "eval" / "run_eval.py"),
           "--backend", "mlx", "--model", model, "--adapter", str(adapter),
           "--label", label, "--max-tokens", str(max_tokens)]
    for b in buckets or []:
        cmd += ["--bucket", b]
    subprocess.run(cmd, cwd=ROOT, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    summary = ROOT / "eval" / "reports" / f"{label}.json"
    return json.loads(summary.read_text()) if summary.exists() else {}


def table(rows: list[dict], buckets: list[str]) -> str:
    head = "| Checkpoint | " + " | ".join(buckets) + " | overall |"
    sep = "|---|" + "---:|" * (len(buckets) + 1)
    lines = [head, sep]
    for r in rows:
        cells = []
        for b in buckets:
            s = r["summary"].get(b)
            cells.append(f"{s['rate']:.0%}" if s else "—")
        o = r["summary"].get("overall")
        lines.append(f"| `{r['name']}` | " + " | ".join(cells) + " | "
                     + (f"**{o['rate']:.0%}**" if o else "—") + " |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="adapters/<run-name>")
    ap.add_argument("--model", help="base model (default: read from the mlx config)")
    ap.add_argument("--screen-buckets", default="facts,depth,personality,web,honesty",
                    help="buckets used for the screening pass. Default is 35 of "
                         "the 40 questions: the screen's resolution is 1/n, and "
                         "with only the 16 facts questions adjacent checkpoints "
                         "tie at 6.25%% steps and the search picks between them "
                         "arbitrarily. Measured on synthetic curves, mean "
                         "accuracy given up is 6.0%% at 8 questions, 2.9%% at 16, "
                         "0.8%% at 40. Manners is excluded: it measures refusal "
                         "behaviour, which barely varies between checkpoints, and "
                         "its answers are the slowest to generate.")
    ap.add_argument("--confirm-top", type=int, default=3,
                    help="how many leaders get the full 40-question eval")
    ap.add_argument("--coarse", type=int, default=8,
                    help="how many checkpoints the first coarse pass screens")
    ap.add_argument("--window", type=int, default=2,
                    help="how many strides either side of the best to refine over")
    ap.add_argument("--exhaustive", action="store_true",
                    help="screen every checkpoint instead of coarse-to-fine")
    ap.add_argument("--max-tokens", type=int, default=320)
    ap.add_argument("--report", action="store_true",
                    help="rebuild the table from results already on disk")
    args = ap.parse_args()

    run = Path(args.run)
    if not run.is_dir():
        sys.exit(f"no such run directory: {run}")

    model = args.model
    if not model:
        cfg = run / "adapter_config.json"
        # mlx-lm's adapter_config.json does not record the base model, so fall
        # back to the training config that names this adapter path.
        for y in (ROOT / "train").glob("*.yaml"):
            text = y.read_text()
            if run.name in text or run.name.rsplit("-round", 1)[0] in text:
                m = re.search(r"^model:\s*(\S+)", text, re.M)
                if m:
                    model = m.group(1)
                    break
    if not model:
        sys.exit("could not infer the base model; pass --model")

    ckpts = checkpoints(run)
    if not ckpts:
        sys.exit(f"no checkpoints in {run}")
    screen = [b.strip() for b in args.screen_buckets.split(",") if b.strip()]

    print(f"run    {run.name}")
    print(f"base   {model}")
    print(f"found  {len(ckpts)} checkpoints")
    print(f"screen {', '.join(screen)}\n")

    tmp = Path(tempfile.mkdtemp(prefix="chatmcd-sweep-"))
    scored: dict[int, dict] = {}          # index into ckpts -> result row
    budget = {"n": 0}

    def name_for(it: int) -> str:
        return "final" if it == 10 ** 9 else f"iter{it:05d}"

    def score(idx: int) -> dict | None:
        """Screen one checkpoint, or return the cached result."""
        if idx in scored:
            return scored[idx]
        it, path = ckpts[idx]
        name = name_for(it)
        label = f"{run.name}-{name}"
        if args.report:
            f = ROOT / "eval" / "reports" / f"{label}.json"
            summary = json.loads(f.read_text()) if f.exists() else {}
        else:
            budget["n"] += 1
            print(f"  [{budget['n']}] {name} …", flush=True)
            summary = run_eval(model, stage(run, path, tmp), label, screen,
                               args.max_tokens)
        if not summary:
            return None
        row = {"idx": idx, "name": name, "label": label, "summary": summary,
               "facts": summary.get("facts", {}).get("rate", 0.0),
               "overall": summary.get("overall", {}).get("rate", 0.0)}
        scored[idx] = row
        if not args.report:
            print(f"        facts {row['facts']:.0%}   "
                  f"gate-buckets {row['overall']:.0%}")
        return row

    try:
        n = len(ckpts)
        if args.exhaustive or args.report or n <= args.coarse:
            for i in range(n):
                score(i)
        else:
            # Coarse pass: an evenly spaced subset spanning the whole run.
            stride = max(1, n // args.coarse)
            idxs = list(range(0, n, stride))
            if idxs[-1] != n - 1:
                idxs.append(n - 1)      # always include the final checkpoint
            print(f"coarse pass: {len(idxs)} of {n} checkpoints "
                  f"(stride {stride})")
            for i in idxs:
                score(i)

            # Refine: halve the stride and look around the current best. The
            # best is a *set*, not a point: with 16 facts questions the score is
            # quantised to 6.25% steps, so several checkpoints near a broad peak
            # tie exactly. Refining around one arbitrary member of that tie (which
            # is what max() returns, by insertion order) walked 350 iterations
            # away from the true peak on a noise-free synthetic curve. Refine
            # across the whole tied span instead.
            while stride > 1:
                stride = max(1, stride // 2)
                top = max(r["facts"] for r in scored.values())
                tied = sorted(r["idx"] for r in scored.values()
                              if r["facts"] >= top - 1e-9)
                lo = max(0, tied[0] - args.window * stride)
                hi = min(n - 1, tied[-1] + args.window * stride)
                nxt = [i for i in range(lo, hi + 1, stride) if i not in scored]
                if not nxt:
                    continue
                span = (f"{name_for(ckpts[tied[0]][0])}"
                        if len(tied) == 1 else
                        f"{len(tied)} tied at {top:.0%}, "
                        f"{name_for(ckpts[tied[0]][0])}..{name_for(ckpts[tied[-1]][0])}")
                print(f"refine (stride {stride}) around {span}: {len(nxt)} more")
                for i in nxt:
                    score(i)

        results = [scored[i] for i in sorted(scored)]
        if not results:
            sys.exit("no results")

        print("\n" + "=" * 62)
        print(f"SCREEN — gate buckets only "
              f"({len(results)} of {len(ckpts)} checkpoints scored)")
        print("=" * 62)
        print(table(results, screen))

        # Rank on facts first (that is the gate), then on the screen overall.
        ranked = sorted(results, key=lambda r: (-r["facts"], -r["overall"]))

        if not args.report and args.confirm_top > 0:
            print("\n" + "=" * 62)
            print(f"CONFIRM — full 40 questions on the top {args.confirm_top}")
            print("=" * 62)
            full = []
            for r in ranked[:args.confirm_top]:
                label = f"{r['label']}-full"
                print(f"  {r['name']} …", flush=True)
                path = ckpts[r["idx"]][1]
                summary = run_eval(model, stage(run, path, tmp), label, None,
                                   args.max_tokens)
                if summary:
                    full.append({"name": r["name"], "label": label,
                                 "summary": summary})
            if full:
                buckets = ["facts", "depth", "personality", "web", "manners", "honesty"]
                print()
                print(table(full, buckets))
                best = max(full, key=lambda r: (
                    r["summary"]["gate"]["passed"],
                    r["summary"]["facts"]["rate"],
                    r["summary"]["overall"]["rate"]))
                gate = best["summary"]["gate"]
                print(f"\n  promote: {best['name']}")
                print(f"  facts {gate['facts_accuracy']:.0%} "
                      f"(need {gate['facts_required']:.0%}), "
                      f"honesty {gate['honesty_pass']}/{gate['honesty_required']}")
                print(f"  gate: {'PASSED' if gate['passed'] else 'NOT PASSED'}")
                print(f"\n  report: eval/reports/{best['label']}.md")
                out = run / "PROMOTED"
                out.write_text(f"{best['name']}\n{best['label']}\n")
                print(f"  wrote {out}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
