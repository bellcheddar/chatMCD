"""The /pdb link must show every one of Marc's PDB entries, and only those.

An author-name search at RCSB was measured to find 9 of his 78 entries, because
structural-genomics depositions name the consortium rather than the scientists.
The link is therefore an ID list, and these tests hold it to that.

    .venv/bin/python3 -m pytest chatmcd/test_pdb.py -q
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chatmcd.pdb import load, rcsb_search_url  # noqa: E402


def test_the_entry_list_is_well_formed():
    d = load()
    ids = d["ids"]
    assert ids and d["count"] == len(ids)
    assert len(set(ids)) == len(ids), "duplicate PDB IDs"
    assert all(re.fullmatch(r"[0-9][A-Z0-9]{3}", i) for i in ids), ids


def test_the_link_asks_rcsb_for_exactly_those_ids():
    ids = load()["ids"]
    url = rcsb_search_url(ids)
    parts = urllib.parse.urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "www.rcsb.org" and parts.path == "/search"
    req = json.loads(urllib.parse.unquote(parts.query.split("request=", 1)[1]))
    params = req["query"]["parameters"]
    assert params["attribute"] == "rcsb_entry_container_identifiers.entry_id"
    assert params["operator"] == "in"
    assert params["value"] == ids
    assert req["return_type"] == "entry"


def test_it_is_not_an_author_name_search():
    """The measured failure: a name search found 9 of 78."""
    assert "audit_author" not in urllib.parse.unquote(rcsb_search_url(["1EVS"]))


def test_the_route_is_registered():
    source = (Path(__file__).parent / "app.py").read_text()
    assert '@app.get("/pdb")' in source and "rcsb_search_url(" in source
