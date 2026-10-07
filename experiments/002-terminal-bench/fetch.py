# experiments/002-terminal-bench/fetch.py
"""
Get the inputs of note 002 and check them against manifest.tsv.

    python experiments/002-terminal-bench/fetch.py                  # download (0.91 GB), then verify
    python experiments/002-terminal-bench/fetch.py --verify         # verify an existing copy, no download
    python experiments/002-terminal-bench/fetch.py --only Terminus2__GLM-5     # one submission only
    python experiments/002-terminal-bench/fetch.py --raw /some/dir  # keep the files somewhere else

The inputs are published trials from Harbor's Terminal-Bench 2.0 leaderboard repository on
Hugging Face (Apache 2.0): for each of nine submissions, every trial's result.json,
config.json and agent/trajectory.json. The repository, the pinned revision and the sha256
of each of the 12,025 files are in manifest.tsv, next to this script. Nothing else is read.

Downloading needs `pip install huggingface_hub`; verifying needs nothing beyond Python.
Each file is requested by the path in the manifest, so the (very large) repository is never
listed; files already present with the recorded size are skipped, so a stopped download
can simply be run again. The files land in experiments/002-terminal-bench/raw/ (not
committed), in the same layout as the dataset. The exit code is 0 only if every file is
present with the recorded hash.
"""
import argparse
import hashlib
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.tsv"
BASE = "submissions/terminal-bench/2.0"
WORKERS = 8
HEADER = re.compile(r"^# inputs of note \S+: (\S+) @ ([0-9a-f]{40})\s*$")


def read_manifest():
    """-> (repo, revision, [(submission, path, bytes, sha256)])."""
    if not MANIFEST.exists():
        raise SystemExit(f"not found: {MANIFEST}")
    rows, repo, rev = [], None, None
    with open(MANIFEST) as f:
        for ln in f:
            if ln.startswith("#"):
                m = HEADER.match(ln)
                if m:
                    repo, rev = m.groups()
                continue
            if not ln.strip():
                continue
            sha, size, path = ln.rstrip("\n").split("\t")
            if not path.startswith(BASE + "/"):
                raise SystemExit(f"unexpected path in the manifest: {path}")
            rows.append((path[len(BASE) + 1:].split("/", 1)[0], path, int(size), sha))
    if not repo:
        raise SystemExit("the manifest's first line does not name the repository and revision")
    return repo, rev, rows


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(repo, rev, rows, raw):
    """Fetch each manifest path directly (no listing of the repository)."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise SystemExit("downloading needs huggingface_hub: pip install huggingface_hub")
    raw.mkdir(parents=True, exist_ok=True)
    todo = [path for _, path, size, _ in rows
            if not ((raw / path).is_file() and (raw / path).stat().st_size == size)]
    print(f"downloading {len(todo):,} of {len(rows):,} files from {repo} @ {rev[:12]} "
          f"into {raw} ({len(rows) - len(todo):,} already there)", flush=True)

    def get(path):
        err = None
        for attempt in range(4):
            try:
                hf_hub_download(repo, path, repo_type="dataset", revision=rev, local_dir=str(raw))
                return None
            except Exception as e:                       # rate limit or network: wait, retry
                err = f"{type(e).__name__}: {str(e)[:160]}"
                time.sleep(5 * (attempt + 1))
        return path, err

    failed = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, res in enumerate(pool.map(get, todo), 1):
            if res:
                failed.append(res)
            if i % 200 == 0 or i == len(todo):
                print(f"   {i:,} / {len(todo):,}", flush=True)
    if failed:
        print(f"{len(failed)} files could not be downloaded (run again to retry), e.g.")
        for path, err in failed[:3]:
            print(f"   {path}: {err}")


def verify(rows, raw):
    """Check every manifest row. -> number of problems."""
    per = defaultdict(lambda: {"files": 0, "ok": 0, "bytes": 0})
    problems = []
    for sub, path, size, sha in rows:
        s = per[sub]
        s["files"] += 1
        p = raw / path
        if not p.is_file():
            problems.append(("missing", path))
        elif p.stat().st_size != size:
            problems.append(("size differs", path))
        elif sha256(p) != sha:
            problems.append(("hash differs", path))
        else:
            s["ok"] += 1
            s["bytes"] += size
    print(f"{'submission':<32} {'files':>6} {'match':>6} {'MB':>7}")
    for sub in sorted(per):
        s = per[sub]
        print(f"{sub:<32} {s['files']:>6} {s['ok']:>6} {s['bytes'] / 1e6:>7.0f}"
              + ("" if s["ok"] == s["files"] else "   <-- problem"))
    total = sum(s["files"] for s in per.values())
    if problems:
        kinds = defaultdict(int)
        for kind, _ in problems:
            kinds[kind] += 1
        print(f"{len(problems)} of {total} files do not match the manifest: "
              + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))
        for kind, path in problems[:10]:
            print(f"   {kind}: {path}")
    else:
        print(f"all {total:,} files match the manifest "
              f"({sum(s['bytes'] for s in per.values()) / 1e9:.2f} GB)")
    return len(problems)


def main():
    ap = argparse.ArgumentParser(description="Download or verify the inputs of note 002.")
    ap.add_argument("--verify", action="store_true", help="check an existing copy; no download")
    ap.add_argument("--only", metavar="SUBMISSION", help="one submission, e.g. Terminus2__GLM-5")
    ap.add_argument("--raw", type=Path, default=HERE / "raw", help="where the files are kept")
    args = ap.parse_args()

    repo, rev, rows = read_manifest()
    subs = sorted({r[0] for r in rows})
    if args.only:
        if args.only not in subs:
            raise SystemExit(f"--only must be one of: {', '.join(subs)}")
        subs = [args.only]
        rows = [r for r in rows if r[0] == args.only]
    print(f"note 002 inputs: {repo} @ {rev}")
    if not args.verify:
        download(repo, rev, rows, args.raw)
    elif not args.raw.exists():
        raise SystemExit(f"nothing to verify: {args.raw} does not exist (run without --verify to download)")
    sys.exit(1 if verify(rows, args.raw) else 0)


if __name__ == "__main__":
    main()