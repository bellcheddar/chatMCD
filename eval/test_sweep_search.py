#!/usr/bin/env python3
"""Test the checkpoint search on synthetic curves, with no model in sight.

The search is a hill-climb over a metric quantised to 1/16 (there are 16 facts
questions). That is coarse enough that several checkpoints near a broad peak tie
exactly, and an argmax over a tie returns whichever was scored first. On a
noise-free curve that walked the search 350 iterations away from the true peak.
So the search gets tested like any other algorithm.

    python3 eval/test_sweep_search.py
"""
import contextlib, importlib.util, io, random, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sw", ROOT / "eval/sweep_checkpoints.py")
sw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sw)


def true_curve(i, peak):
    """A realistic accuracy-vs-checkpoint curve.

    Rises steeply from the untuned baseline (25% facts, measured), peaks, then
    decays as the model overfits. The first version of this test used a very
    shallow parabola whose whole top quantised to one value of 16 questions, so
    a 19-checkpoint-wide region tied and *any* answer was defensible. The
    exhaustive control failing was what exposed it: if scoring every checkpoint
    still "misses" the peak, the curve is the problem, not the search.
    """
    if i <= peak:
        return 0.25 + 0.63 * (i / peak) ** 0.55
    return 0.88 - 0.30 * ((i - peak) / (40 - peak)) ** 1.5


def harness(n, peak, noise, seed, n_questions=35, exhaustive=False):
    """Run the search over a synthetic curve; return (found, evals)."""
    rng, calls = random.Random(seed), {"n": 0}
    true = lambda i: true_curve(i, peak)

    def fake(model, adapter, label, buckets, max_tokens):
        calls["n"] += 1
        i = int(re.search(r"iter(\d+)$", label).group(1)) // 50 - 1
        a = max(0.0, min(1.0, true(i) + rng.gauss(0, noise)))
        a = round(a * n_questions) / n_questions        # the real quantisation
        return {"facts": {"rate": a, "passed": 0, "total": n_questions},
                "overall": {"rate": a, "passed": 0, "total": n_questions + 3}}

    tmp = Path(tempfile.mkdtemp())
    run = tmp / "adapters" / "fake-round"
    run.mkdir(parents=True)
    (run / "adapter_config.json").write_text("{}")
    for i in range(n):
        (run / f"{(i + 1) * 50:07d}_adapters.safetensors").write_bytes(b"x")

    sw.run_eval, sw.stage = fake, (lambda r, c, t: c)
    sys.argv = ["x", str(run), "--confirm-top", "0", "--model", "m"]
    if exhaustive:
        sys.argv.append("--exhaustive")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sw.main()
    # Match a table row by its checkpoint name and its final (overall) cell,
    # without assuming how many bucket columns sit between them: the number of
    # screen buckets is configurable, and hard-coding two of them silently broke
    # this the moment the default screen was widened.
    rows = re.findall(r"\| `iter(\d+)` \|.*?\*\*(\d+)%\*\* \|", buf.getvalue())
    if not rows:
        raise AssertionError("no result rows parsed from:\n" + buf.getvalue()[-1500:])
    found = int(max(rows, key=lambda r: int(r[1]))[0]) // 50 - 1
    return found, calls["n"]


def main():
    N, PEAK = 40, 17
    best = true_curve(PEAK, PEAK)
    # What matters is promoting a GOOD checkpoint, not hitting the argmax index.
    # Score the search on the accuracy it gives up against the true peak.
    TOL = 0.03
    print(f"{N} checkpoints, true peak {best:.0%} at index {PEAK}")
    print(f"pass = promoted checkpoint is within {TOL:.0%} of the peak\n")
    print(f"{'noise':>6} {'seed':>5} {'evals':>6} {'saved':>7} {'found':>6} "
          f"{'true acc':>9} {'gap':>6}")

    fails, gaps, evals = [], [], []
    for noise in (0.00, 0.03, 0.06):
        for seed in (1, 2, 3, 4, 5):
            found, n = harness(N, PEAK, noise, seed)
            gap = best - true_curve(found, PEAK)
            gaps.append(gap); evals.append(n)
            ok = gap <= TOL
            if not ok:
                fails.append((noise, seed, found, round(gap, 3)))
            print(f"{noise:>6.2f} {seed:>5} {n:>6} {100*(1-n/N):>6.0f}% "
                  f"{found:>6} {true_curve(found, PEAK):>8.0%} {gap:>6.1%}"
                  f"{'' if ok else '   <-- FAIL'}")

    print(f"\nmean {sum(evals)/len(evals):.1f} evaluations of {N} "
          f"({100*(1-sum(evals)/len(evals)/N):.0f}% saved), "
          f"mean accuracy given up {sum(gaps)/len(gaps):.1%}")

    # Control: scoring everything must do at least as well, or the harness is
    # wrong and every number above is meaningless. This is what caught the first
    # version of this test, whose curve was too flat for 16 questions to resolve.
    found, n = harness(N, PEAK, 0.0, 1, exhaustive=True)
    gap = best - true_curve(found, PEAK)
    ex_ok = n == N and gap <= TOL
    print(f"\nexhaustive control: {n} evaluations, found {found} "
          f"({true_curve(found, PEAK):.0%}, gap {gap:.1%}) "
          f"{'ok' if ex_ok else 'FAIL'}")
    if not ex_ok:
        fails.append(("exhaustive", "-", found, round(gap, 3)))

    # Resolution: with a coarser metric the search cannot do better than the
    # metric allows. Reported, not asserted -- it is the reason finer
    # checkpointing has a ceiling.
    print("\nmetric resolution vs achievable accuracy (noise 0.03, 5 seeds):")
    for q in (8, 16, 35, 40, 100):
        g = [best - true_curve(harness(N, PEAK, 0.03, s, n_questions=q)[0], PEAK)
             for s in (1, 2, 3, 4, 5)]
        print(f"  {q:>3} questions ({1/q:.1%} steps): mean gap {sum(g)/len(g):.1%}")

    print(f"\n{len(fails)} failures")
    for f in fails:
        print(f"  {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
