# experiments/003/code/u_values.py   (reads the tree's analysis, the run labels and the switch
# sort; writes one JSON file beside the analysis)
# The exploration arm's values for the pre-registration's questions U1 to U7, so thresholds can
# be set by looking at what each measure does. Fable 5 stands in for "before" and Fable 5.1 for
# "after". Three framings, as in draft v2:
#   as received     every trial, API errors left out
#   equal cap       the same, only trials run at the equal cap (64k for Fable)
#   own model       the same, without trials another model took over (fallback or silent switch)
# Scope: tasks with at least 3 counted trials in both arms. Shares carry a 90% interval from
# resampling tasks (2,000 resamples, seed 20261027).
# v2 (29 Oct, for the freeze): the two arms and the equal cap are arguments; their defaults are
# the Fable run's, so with them the output is unchanged. The checks against the 29 Oct counts run
# only when the arms are the two Fable models.
#   python experiments/003/code/u_values.py experiments/003-fable ~/crucible/harbor/out/switch_sort_003.jsonl
#   python experiments/003/code/u_values.py EXPERIMENT SWITCH_SORT --before claude-opus-5 --after claude-opus-5-5 --equal-cap CAP
import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

ap = argparse.ArgumentParser()
ap.add_argument("experiment")
ap.add_argument("switch_sort")
ap.add_argument("--before", default="claude-fable-5")
ap.add_argument("--after", default="claude-fable-5-1")
ap.add_argument("--equal-cap", type=int, default=64000)
args = ap.parse_args()
BEFORE, AFTER, CAP = args.before, args.after, args.equal_cap
FABLE = (BEFORE, AFTER) == ("claude-fable-5", "claude-fable-5-1")
SEED, B = 20261027, 2000
exp = Path(args.experiment)
switch_file = Path(args.switch_sort).expanduser()

an = json.loads((exp / "analysis.json").read_text())
labels = json.loads((exp / "run_labels.json").read_text())["by_run"]
switched = {(r["job"], r["trial"]) for r in map(json.loads, switch_file.read_text().splitlines())
            if r.get("kind") in ("fallback", "silent")}

fails = 0
def check(ok, what):
    global fails
    fails += not ok
    print(f"   {'ok  ' if ok else 'FAIL'} {what}")

trials = []
for c in an["cells"]:
    for r in c["runs"]:
        lab = labels.get(r["id"])
        if lab is None:
            continue
        trials.append({
            "task": c["task"], "arm": c["model"].split("/")[-1], "passed": r.get("verdict") == "pass",
            "api": lab["api_error"], "cap": lab["cap"], "switched": (lab["job"], lab["trial"]) in switched,
            "ending": lab["ending"], "turns": r.get("turns") or 0, "in": r.get("in_tok") or 0,
            "out": r.get("out_tok") or 0, "cost": r.get("cost_usd"), "traj": r.get("trajectory") or []})
if FABLE:
    check(len(trials) == 660, f"every run has its label ({len(trials)} of 660)")
    check(sum(t["switched"] for t in trials) == 117, f"switched runs as sorted on 29 Oct ({sum(t['switched'] for t in trials)} of 117)")

FRAMINGS = {
    "as received": lambda t: not t["api"],
    "equal cap": lambda t: not t["api"] and t["cap"] == CAP,
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
    return Counter(x[n] if len(x) > n else "END" for x in trajs).most_common(1)[0][0] if len(trajs) > 1 else "END"


def tasks_for(keep):
    by = defaultdict(lambda: {BEFORE: [], AFTER: []})
    for t in trials:
        if keep(t) and t["arm"] in (BEFORE, AFTER):
            by[t["task"]][t["arm"]].append(t)
    return {k: v for k, v in by.items() if len(v[BEFORE]) >= 3 and len(v[AFTER]) >= 3}


def rate(ts):
    return sum(t["passed"] for t in ts) / len(ts)


# the measures, each from a list of per-task records (a resample may repeat tasks)
def u1(recs):
    held = [r for r in recs if grade(r[BEFORE]) == "every"]
    return sum(grade(r[AFTER]) == "every" for r in held) / len(held) if held else None


def u2(recs):
    return sum((grade(r[BEFORE]), grade(r[AFTER])) in {("every", "some"), ("every", "never"), ("some", "never")} for r in recs)


def u3(recs):
    vals = []
    for r in recs:
        d = rate(r[AFTER]) - rate(r[BEFORE])
        sign = (d > 0) - (d < 0)
        pairs = [(a["passed"] - b["passed"]) for a in r[AFTER] for b in r[BEFORE]]
        vals.append(sum(((p > 0) - (p < 0)) != sign for p in pairs) / len(pairs))
    return sum(vals) / len(vals) if vals else None


def path_changed(r, k):
    mb, ma = median(t["turns"] for t in r[BEFORE]), median(t["turns"] for t in r[AFTER])
    steps = max(mb, ma) >= 1.5 * max(min(mb, ma), 1)
    kind = split_kind([t["traj"] for t in r[BEFORE]]) != split_kind([t["traj"] for t in r[AFTER]])
    pre = k is not None and abs(prefix([t["traj"] for t in r[BEFORE]]) - prefix([t["traj"] for t in r[AFTER]])) >= k
    return steps, kind, pre


def u7(recs, k=None, which=(0, 1, 2)):
    same = [r for r in recs if grade(r[BEFORE]) == grade(r[AFTER])]
    if not same:
        return None
    return sum(any(path_changed(r, k)[i] for i in which) for r in same) / len(same)


def interval(fn, recs):
    rnd = random.Random(SEED)
    vals = []
    for _ in range(B):
        v = fn([recs[rnd.randrange(len(recs))] for _ in recs])
        if v is not None:
            vals.append(v)
    vals.sort()
    return (vals[int(0.05 * len(vals))], vals[int(0.95 * len(vals)) - 1]) if vals else (None, None)


def pct(v):
    return "-" if v is None else f"{100 * v:.0f}%"


def show(fn, recs):
    lo, hi = interval(fn, recs)
    return f"{pct(fn(recs))} ({pct(lo)} to {pct(hi)})"


out = {}
for name, keep in FRAMINGS.items():
    tk = tasks_for(keep)
    recs = [tk[t] for t in sorted(tk)]
    flat = {a: [t for r in recs for t in r[a]] for a in (BEFORE, AFTER)}
    same = [r for r in recs if grade(r[BEFORE]) == grade(r[AFTER])]
    o = {"tasks": len(recs), "trials": {a: len(v) for a, v in flat.items()}}
    print(f"== {name}: {len(recs)} tasks in scope; counted trials {BEFORE} {len(flat[BEFORE])}, {AFTER} {len(flat[AFTER])}")
    if not recs:
        print()
        out[name] = o
        continue
    tw = {a: sum(rate(r[a]) for r in recs) / len(recs) for a in (BEFORE, AFTER)}
    print(f"   pass rate, task-weighted: {pct(tw[BEFORE])} before, {pct(tw[AFTER])} after")
    print(f"   U1 held: of {sum(grade(r[BEFORE]) == 'every' for r in recs)} tasks passed every time before, "
          f"still every time after: {show(u1, recs)}")
    lo, hi = interval(u2, recs)
    print(f"   U2 drops: {u2(recs)} tasks drop a grade ({lo} to {hi}) while the pass rate moves "
          f"{'up' if tw[AFTER] > tw[BEFORE] else 'down or flat'}; grade moves "
          + ", ".join(f"{b}>{a} {n}" for (b, a), n in sorted(Counter((grade(r[BEFORE]), grade(r[AFTER])) for r in recs).items()) if b != a))
    print(f"   U3 one run misleads: expected share of trial pairs pointing the wrong way {show(u3, recs)}")
    cost = {a: sum(t["cost"] or 0 for t in flat[a]) for a in flat}
    passes = {a: sum(t["passed"] for t in flat[a]) for a in flat}
    cpp = {a: cost[a] / passes[a] if passes[a] else None for a in flat}
    tok = {a: median(t["in"] + t["out"] for t in flat[a]) for a in flat}
    print(f"   U4 cost per pass: ${cpp[BEFORE]:.2f} before, ${cpp[AFTER]:.2f} after (x{cpp[AFTER] / cpp[BEFORE]:.2f}); "
          f"tokens per trial, median: {tok[BEFORE]:,.0f} before, {tok[AFTER]:,.0f} after (x{tok[AFTER] / tok[BEFORE]:.2f})")
    stopped = {a: sum(t["ending"] == "completed" and not t["passed"] for t in flat[a]) / len(flat[a]) for a in flat}
    refused = {a: sum("refus" in t["ending"].lower() for t in flat[a]) for a in flat}
    print(f"   U5 endings: stopped by itself but failed {pct(stopped[BEFORE])} before, {pct(stopped[AFTER])} after; "
          f"refusals {refused[BEFORE]} and {refused[AFTER]}; endings after "
          + ", ".join(f"{k} {v}" for k, v in Counter(t["ending"] for t in flat[AFTER]).most_common()))
    steps = {a: median(t["turns"] for t in flat[a]) for a in flat}
    per_in = {a: median(t["in"] / max(t["turns"], 1) for t in flat[a]) for a in flat}
    per_out = {a: median(t["out"] / max(t["turns"], 1) for t in flat[a]) for a in flat}
    print(f"   U6 shape, medians: steps per trial {steps[BEFORE]:.0f} before, {steps[AFTER]:.0f} after; "
          f"tokens per step in {per_in[BEFORE]:,.0f} and {per_in[AFTER]:,.0f}, out {per_out[BEFORE]:,.0f} and {per_out[AFTER]:,.0f}")
    print(f"   U7 same grade, path changed, of {len(same)} same-grade tasks:")
    print(f"      steps differ 1.5x {show(lambda rs: u7(rs, None, (0,)), recs)}; "
          f"first-split category differs {show(lambda rs: u7(rs, None, (1,)), recs)}; "
          f"either {show(lambda rs: u7(rs, None, (0, 1)), recs)}")
    for k in (1, 2, 3, 5):
        print(f"      shared prefix differs by {k}+ steps {show(lambda rs, k=k: u7(rs, k, (2,)), recs)}; "
              f"any of the three {show(lambda rs, k=k: u7(rs, k), recs)}")
    o.update({"pass_rate_task_weighted": tw, "U1": u1(recs), "U2": u2(recs), "U3": u3(recs),
              "U4": {"cost_per_pass": cpp, "tokens_per_trial_median": tok},
              "U5": {"stopped_failed": stopped, "refusals": refused},
              "U6": {"steps_median": steps, "in_per_step": per_in, "out_per_step": per_out},
              "U7": {"steps_or_kind": u7(recs, None, (0, 1)),
                     "prefix": {k: u7(recs, k, (2,)) for k in (1, 2, 3, 5)}}})
    out[name] = o
    print()

dest = exp / "u_values.json"
dest.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
print(f"   wrote {dest}")
print("PASS: exploration values computed" if not fails else f"FAIL: {fails} check(s) failed; read the lines above")
sys.exit(1 if fails else 0)