#!/usr/bin/env python3
"""Regenerate chatmcd/pdb_entries.json from the corpus's RCSB-derived entry list.

    python3 scripts/build_pdb_links.py

Refuses to write unless RCSB, asked for those IDs, returns every one of them:
a link called "all of Marc's PDB entries" must not silently show fewer.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
REF = ROOT / "training/corpus/reference/pdb-structures.md"
OUT = ROOT / "chatmcd/pdb_entries.json"


def main() -> int:
    text = REF.read_text()
    ids = re.findall(r"^- \*\*([0-9][A-Z0-9]{3})\*\*", text, re.M)
    if not ids or len(set(ids)) != len(ids):
        sys.exit(f"bad ID list in {REF}: {len(ids)} ids, {len(set(ids))} unique")
    when = re.search(r"retrieved live from the RCSB PDB on ([0-9]{1,2} \w+ [0-9]{4})", text)
    body = {"query": {"type": "terminal", "service": "text",
                      "parameters": {"attribute": "rcsb_entry_container_identifiers.entry_id",
                                     "operator": "in", "value": ids}},
            "return_type": "entry", "request_options": {"paginate": {"start": 0, "rows": 200}}}
    req = urllib.request.Request("https://search.rcsb.org/rcsbsearch/v2/query",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "chatMCD/1.0"})
    got = {x["identifier"] for x in json.loads(urllib.request.urlopen(req, timeout=60).read())["result_set"]}
    if set(ids) != got:
        sys.exit(f"RCSB returned {len(got)} of {len(ids)}; missing {sorted(set(ids) - got)}")
    OUT.write_text(json.dumps({
        "source": "RCSB PDB entries with Marc C. Deller as a deposition or primary-citation author",
        "retrieved": when.group(1) if when else "",
        "count": len(ids),
        "ids": ids,
    }, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(ids)} entries, all confirmed at RCSB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
