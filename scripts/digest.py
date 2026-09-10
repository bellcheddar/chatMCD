#!/usr/bin/env python3
"""Weekly digest of what visitors actually asked chatMCD.

This is the input to docs/RETRAIN.md. It reads the SQLite the Flask app writes
and reports the three things that decide what to train next:

  * the most-asked questions, which say what the corpus should cover best
  * questions that got a thumbs down, which say where it is wrong
  * questions that look like near-duplicates of each other, which say where one
    good Q&A pair would fix a whole cluster

It reports questions, never answers and never anything about who asked. The app
scrubs email addresses and phone numbers before a row is written; this only ever
reads what survived that.

    python3 scripts/digest.py --db var/chatmcd.sqlite --days 7
    ssh root@droplet 'sqlite3 -json /opt/chatmcd/var/chatmcd.sqlite \\
        "select * from questions"' > /tmp/q.json   # or pull it and run locally
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Phrases the model uses when it declines. A question that reliably produces one
# of these is a gap in the corpus, which is the whole point of the log.
STOPWORDS = set("""a an the is are was were do does did of to in on for with and or
what who when where why how can could would should i you he she it they me my his her
about tell give show me please marc marcs deller""".split())


def normalise(q: str) -> str:
    """Collapse a question to its content words, so near-duplicates group."""
    words = re.findall(r"[a-z0-9']+", q.lower())
    return " ".join(w for w in words if w not in STOPWORDS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(ROOT / "var" / "chatmcd.sqlite"))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    path = Path(args.db)
    if not path.exists():
        sys.exit(f"no database at {path} — nothing has been asked yet, or pull "
                 f"it from the droplet first")

    since = time.time() - args.days * 86400
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    rows = conn.execute(
        "SELECT ts, source, question, turn FROM questions WHERE ts >= ? ORDER BY ts",
        (since,)).fetchall()
    votes = conn.execute(
        "SELECT ts, source, question, vote, note FROM feedback WHERE ts >= ?",
        (since,)).fetchall()

    print(f"# chatMCD digest — {args.days} days to "
          f"{time.strftime('%Y-%m-%d')}\n")
    if not rows:
        print("No questions in the window.")
        return 0

    by_source = Counter(r[1] for r in rows)
    first_turns = sum(1 for r in rows if r[3] == 0)
    print(f"- **{len(rows)} questions** across {len(set(r[0] // 86400 for r in rows))} days")
    print(f"- **{first_turns} conversations** started "
          f"({len(rows) / max(first_turns, 1):.1f} questions each)")
    print("- by surface: " + ", ".join(f"{k} {v}" for k, v in by_source.most_common()))
    ups = sum(1 for v in votes if v[3] > 0)
    downs = sum(1 for v in votes if v[3] < 0)
    print(f"- feedback: {ups} up, {downs} down\n")

    # -- clusters -------------------------------------------------------------
    clusters: dict[str, list[str]] = {}
    for _, _, q, _ in rows:
        clusters.setdefault(normalise(q), []).append(q)
    ranked = sorted(clusters.items(), key=lambda kv: -len(kv[1]))

    print("## Most-asked\n")
    print("| Asked | Question (one example of the cluster) |")
    print("|---:|---|")
    for _, examples in ranked[:args.top]:
        if len(examples) < 2:
            continue
        print(f"| {len(examples)} | {examples[0][:110].replace('|', '\\|')} |")

    # -- thumbs down ----------------------------------------------------------
    if downs:
        print("\n## Thumbs down — write a Q&A pair for each of these\n")
        for ts, source, q, vote, note in votes:
            if vote < 0:
                stamp = time.strftime('%Y-%m-%d', time.localtime(ts))
                print(f"- `{stamp}` ({source}) {q[:160]}"
                      + (f"\n  - note: {note}" if note else ""))

    # -- singletons -----------------------------------------------------------
    singles = [ex[0] for _, ex in ranked if len(ex) == 1]
    if singles:
        print(f"\n## Asked once ({len(singles)})\n")
        print("The long tail. Worth skimming for anything the corpus does not "
              "cover at all.\n")
        for q in singles[:40]:
            print(f"- {q[:160]}")

    print("\n---\n")
    print("Next: write new pairs into `training/qa/<area>/*.qa.md`, rerun "
          "`python3 training/build_jsonl.py`, then follow `docs/RETRAIN.md`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
