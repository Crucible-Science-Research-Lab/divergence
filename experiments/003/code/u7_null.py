# ~/crucible/divergence/scratch/u7_null.py   (read-only; writes nothing)
# Is U7's "path changed" a signal or does it fire on almost anything? For the same-grade tasks of
# each framing, the runs of both arms are pooled and shuffled into two groups of the arms' sizes,
# 1,000 times (seed 20261027), and each criterion is scored as if the groups were the arms. The
# observed share is then read against that null: its median, its 95th percentile, and how often a
# shuffle matches or beats the observed share.
#   python scratch/u7_null.py experiments/003-fable ~/crucible/harbor/out/switch_sort_003.jsonl
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

BEFORE, AFTER = "claude-fable-5", "claude-fable-5-1"
SEED, P = 20261027, 1000
exp = Path(sys.argv[1])
an = json.loads((exp / "analysis.json").read_text())
labels = json.loads((exp / "run_labels.json").read_text())["by_run"]
switched = {(r["job"], r["trial"]) for r in map(json.loads, Path(sys.argv[2]).expanduser().read_text().splitlines())
            if r.get("kind") in ("fallback", "silent")}

trials = []
for c in an["cells"]:
    for r in c["runs"]:
        lab = labels.get(r["id"])
        if lab:
            trials.append({"task": c["task"], "arm": c["model"].split("/")[-1], "passed": r.get("verdict") == "pass",
                           "api": lab["api_error"], "cap": lab["cap"], "switched": (lab["job"], lab["trial"]) in switched,
                           "turns": r.get("turns") or 0, "traj": r.get("trajectory") or []})

FRAMINGS = {
    "as received": lambda t: not t["api"],
    "equal cap": lambda t: not t["api"] and t["cap"] == 64000,
    "own model": lambda t: not t["api"] and not t["switched"],
}


def grade(ts):
    p = sum(t["passed"] for t in ts)
    return "every" if p == len(ts) else "never" if p == 0 else "some"


def prefix(trajs):
    n = 0
    while all(len(x) > n for x in trajs) and len({x[n] for x in trajs}) == 1:
        n += 1
    return n


def split_kind(trajs):
    n = prefix(trajs)
    return Counter(x[n] if len(x) > n else "END" for x in trajs).most_common(1)[0][0]


def flags(b, a):
    mb, ma = median(t["turns"] for t in b), median(t["turns"] for t in a)
    steps = max(mb, ma) >= 1.5 * max(min(mb, ma), 1)
    kind = split_kind([t["traj"] for t in b]) != split_kind([t["traj"] for t in a])
    d = abs(prefix([t["traj"] for t in b]) - prefix([t["traj"] for t in a]))
    return {"steps 1.5x": steps, "first-split category": kind, "prefix 1+": d >= 1, "prefix 2+": d >= 2,
            "prefix 3+": d >= 3, "steps or category": steps or kind, "steps or prefix 2+": steps or d >= 2,
            "any of the three (prefix 2+)": steps or kind or d >= 2}


for name, keep in FRAMINGS.items():
    by = defaultdict(lambda: {BEFORE: [], AFTER: []})
    for t in trials:
        if keep(t):
            by[t["task"]][t["arm"]].append(t)
    same = [v for k, v in sorted(by.items()) if len(v[BEFORE]) >= 3 and len(v[AFTER]) >= 3
            and grade(v[BEFORE]) == grade(v[AFTER])]
    if not same:
        continue
    obs = Counter()
    for v in same:
        for k, f in flags(v[BEFORE], v[AFTER]).items():
            obs[k] += f
    rnd = random.Random(SEED)
    null = defaultdict(list)
    for _ in range(P):
        cnt = Counter()
        for v in same:
            pool = v[BEFORE] + v[AFTER]
            rnd.shuffle(pool)
            nb = len(v[BEFORE])
            for k, f in flags(pool[:nb], pool[nb:]).items():
                cnt[k] += f
        for k in obs:
            null[k].append(cnt[k] / len(same))
    print(f"== {name}: {len(same)} same-grade tasks; observed share against shuffled arms ({P} shuffles)")
    for k in obs:
        o = obs[k] / len(same)
        ns = sorted(null[k])
        p = sum(x >= o for x in ns) / P
        print(f"   {k:<30} observed {100 * o:3.0f}%   null median {100 * median(ns):3.0f}%, "
              f"95th {100 * ns[int(.95 * P) - 1]:3.0f}%   shuffles at or above observed {p:.3f}")
    print()
print("done")