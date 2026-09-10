#!/usr/bin/env python3
"""Put N concurrent conversations through the live endpoint and time them.

The number that matters for a recruiter is not throughput, it is **time to first
token**: how long the page sits blank. Total time only decides how long they wait
for the rest, and they are already reading by then. So both are reported, and the
percentiles are reported rather than the mean, because the mean of a queue hides
exactly the tail the queue creates.

Every request is a different question, so nothing is served from a cache anywhere
in the chain and each one costs a real ZeroGPU allocation.

    python3 scripts/load_test.py --n 20
    python3 scripts/load_test.py --n 20 --url http://127.0.0.1:8011/api/chat

Counts as a failure, and each is reported separately rather than as one number:
  http      a non-200, or the connection died
  error     the server sent an SSE `error` event
  empty     200, stream ended, no tokens -- the ZeroGPU quota signature
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field

QUESTIONS = [
    "What is Elora Therapeutics?", "What did Marc do at Pfizer?",
    "How many protein structures has Marc solved?", "Where did Marc do his doctorate?",
    "What is BoltzMaker?", "What are the 6 C's?", "What is Marc's most-cited paper?",
    "What does Marc do at DeepCovalent?", "Which companies has Marc worked for?",
    "What is Marc's h-index?", "What is AlphaFraud?", "Where is Marc based?",
    "What is the PDB ID of the oncostatin M structure?", "What is CODSWALLOP?",
    "What is Marc's favourite band?", "How many patents does Marc hold?",
    "What is PANTS?", "What did Marc do at Stanford?",
    "What is Marc's view on mentoring?", "What is ALPHABETTI?",
]


@dataclass
class Run:
    idx: int
    question: str
    ttft: float | None = None
    total: float | None = None
    tokens: int = 0
    status: str = ""
    failure: str = ""


def one(url: str, run: Run, timeout: float) -> None:
    body = json.dumps({"message": run.question, "stream": True,
                       "source": "api"}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                run.failure = f"http {r.status}"
                return
            event = ""
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    if event == "token":
                        if run.ttft is None:
                            run.ttft = time.time() - t0
                        run.tokens += 1
                    elif event == "status":
                        run.status = json.loads(line[6:]).get("state", "")
                    elif event == "error":
                        run.failure = "error: " + str(
                            json.loads(line[6:]).get("detail", ""))[:60]
    except Exception as e:  # a dead connection is a failure like any other
        run.failure = f"{type(e).__name__}: {str(e)[:60]}"
    run.total = time.time() - t0
    # 200 with no tokens is the ZeroGPU quota signature, and it is NOT a success.
    if not run.failure and run.tokens == 0:
        run.failure = "empty (no tokens)"


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))
    return s[k]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="https://chatmcd.mdeller.com/api/chat")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--ttft-budget", type=float, default=10.0,
                    help="p95 time to first token that counts as a pass")
    args = ap.parse_args()

    runs = [Run(i, QUESTIONS[i % len(QUESTIONS)]) for i in range(args.n)]
    print(f"{args.n} concurrent conversations -> {args.url}\n")

    threads = [threading.Thread(target=one, args=(args.url, r, args.timeout))
               for r in runs]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - t0

    ok = [r for r in runs if not r.failure]
    bad = [r for r in runs if r.failure]
    ttfts = [r.ttft for r in ok if r.ttft is not None]
    totals = [r.total for r in ok if r.total is not None]

    for r in sorted(runs, key=lambda r: (bool(r.failure), r.ttft or 1e9)):
        mark = "ok  " if not r.failure else "FAIL"
        ttft = f"{r.ttft:6.1f}" if r.ttft is not None else "     -"
        total = f"{r.total:6.1f}" if r.total is not None else "     -"
        note = r.failure or (f"{r.tokens} chunks"
                             + (f", {r.status}" if r.status else ""))
        print(f"  {mark} [{r.idx:2}] ttft {ttft}s  total {total}s  {note}")

    print(f"\n  wall clock      {wall:.1f}s for {args.n} concurrent")
    print(f"  succeeded       {len(ok)}/{args.n}")
    if bad:
        kinds: dict[str, int] = {}
        for r in bad:
            kinds[r.failure.split(":")[0]] = kinds.get(r.failure.split(":")[0], 0) + 1
        print(f"  failures        {kinds}")
    if ttfts:
        print(f"  time to first token   p50 {pct(ttfts,50):5.1f}s   "
              f"p95 {pct(ttfts,95):5.1f}s   max {max(ttfts):5.1f}s")
        print(f"  total per answer      p50 {pct(totals,50):5.1f}s   "
              f"p95 {pct(totals,95):5.1f}s   max {max(totals):5.1f}s")

    passed = bool(ttfts) and not bad and pct(ttfts, 95) <= args.ttft_budget
    print(f"\n  {'PASS' if passed else 'FAIL'}: "
          f"{args.n} concurrent, no failures, p95 first token "
          f"<= {args.ttft_budget:.0f}s")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
