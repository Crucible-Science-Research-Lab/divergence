# ~/crucible/divergence/scratch/make_share.py   (reads the viewer and one experiment; writes one HTML file)
# One shareable file for a pair: the viewer with just the two cells and their run labels inside,
# so it opens from disk, with no server and nothing fetched. It opens on the replay of the pair;
# "explore the runs" switches to the interactive view of the same two cells.
# Leaves out what describes the whole experiment (hypotheses, per-model tables, exhibits), since
# the file holds two cells. Safe to run twice: the file is rebuilt each time.
#   python scratch/make_share.py experiments/003-fable \
#       embedding-drift-monitor__claude-fable-5 embedding-drift-monitor__claude-fable-5-1 \
#       --out experiments/003-fable/share/embedding-drift-monitor.html
import argparse
import hashlib
import json
import sys
from pathlib import Path

KEY = "inspect,edit,run,verify,install,fetch,delete,delegate,done"

ap = argparse.ArgumentParser()
ap.add_argument("exp_dir")
ap.add_argument("cell")
ap.add_argument("with_cell")
ap.add_argument("--out", required=True)
ap.add_argument("--viewer", default="viewer/index.html")
ap.add_argument("--key", default=KEY)
a = ap.parse_args()

fails = 0
def check(ok, what):
    global fails
    fails += not ok
    print(f"   {'ok  ' if ok else 'FAIL'} {what}")

exp = Path(a.exp_dir)
viewer = Path(a.viewer).read_text(encoding="utf-8")
check("const EMBD" in viewer and viewer.count("\n<script>\n") == 1, "viewer reads embedded data (v0.8 or later)")
an = json.loads((exp / "analysis.json").read_text(encoding="utf-8"))
cells = [c for c in an["cells"] if c["cell_id"] in (a.cell, a.with_cell)]
cells.sort(key=lambda c: c["cell_id"] != a.cell)
check(len(cells) == 2, f"both cells found ({', '.join(c['cell_id'] for c in cells)})")

labels = None
lp = exp / "run_labels.json"
if lp.exists():
    l = json.loads(lp.read_text(encoding="utf-8"))
    ids = {r["id"] for c in cells for r in c["runs"]}
    labels = {"version": l.get("version"), "default_cap": l.get("default_cap"),
              "by_run": {k: v for k, v in (l.get("by_run") or {}).items() if k in ids},
              "by_cell": {c["cell_id"]: l["by_cell"][c["cell_id"]] for c in cells if c["cell_id"] in (l.get("by_cell") or {})}}
    check(len(labels["by_run"]) == len(ids), f"run labels for every run ({len(labels['by_run'])} of {len(ids)})")
else:
    print("   (no run_labels.json; the file is made without labels)")

meta = dict(an.get("meta") or {})
meta["tasks"] = sorted({c["task"] for c in cells})
meta["models"] = [m for m in meta.get("models", []) if any(c["model"] == m for c in cells)] or sorted({c["model"] for c in cells})
meta["n_cells"] = len(cells)
meta["n_runs"] = sum(len(c["runs"]) for c in cells)
data = {
    "analysis": {"contract_version": an.get("contract_version"), "meta": meta, "cells": cells, "exhibits": {}},
    "labels": labels,
    "view": f"cell={a.cell}&with={a.with_cell}&export=1&play=1&key={a.key}",
    "explore": f"cell={a.cell}&key={a.key}",
}
blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
check("</script" not in blob.lower(), "embedded data cannot close its own script tag")
page = viewer.replace("\n<script>\n", f'\n<script type="application/json" id="embedded">{blob}</script>\n<script>\n', 1)
tag = '<script type="application/json" id="embedded">'
check(page.count(tag) == viewer.count(tag) + 1, "data embedded once")

if fails:
    print(f"FAIL: {fails} check(s); nothing written")
    sys.exit(1)
out = Path(a.out)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(page, encoding="utf-8")
b = out.read_bytes()
print(f"   wrote {out} ({len(b) / 1024:.0f} KB; {meta['n_runs']} runs in {len(cells)} cells)")
cv = hashlib.sha256(b.rstrip(b"\n")).hexdigest()
print(f"   check value {cv}")
print("PASS: shareable file written")