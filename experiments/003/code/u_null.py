# experiments/003/code/u_null.py   (read-only; writes nothing)
# The same-model baseline for U1, U2, U4, U5 and U7, extending u7_null.py. In each framing, every
# task's counted runs from both arms are pooled and shuffled into two groups of the arms' sizes,
# 1,000 times (seed 20261027), which simulates running the same model twice. Each question is
# scored on the shuffles as if the groups were the arms, and the real value is read against them.
# Two-sided, three outcomes (decided 29 Oct): worse than the baseline, or better, when no more than
# 2.5% of shuffles reach the real value on that side; within otherwise. Which side is worse:
#   U1 held             lower is worse (fewer always-passing tasks still always pass)
#   U2 drops            higher is worse (more tasks drop a grade)
#   U4 cost, tokens     higher is worse (ratio after/before)
#   U5 stopped, refused higher is worse (after minus before)
#   U7 path changed     neither; reads above or below the baseline. Steps differ 1.5x or shared
#                       prefix differs by 2+, on the tasks with the same grade in both real arms
#                       (as u7_null.py)
# Each question draws from its own generator seeded 20261027, so U7 repeats u7_null.py's
# "steps or prefix 2+" line exactly. Observed values are checked against u_values.json.
# v2 (29 Oct, for the freeze): the two arms and the equal cap are arguments; their defaults are
# the Fable run's, so with them the output is unchanged. The checks against the 29 Oct counts run
# only when the arms are the two Fable models.
#   python experiments/003/code/u_null.py experiments/003-fable ~/crucible/harbor/out/switch_sort_003.jsonl
#   python experiments/003/code/u_null.py EXPERIMENT SWITCH_SORT --before claude-opus-5 --after claude-opus-5-5 --equal-cap CAP
import argparse
import json
import random
import sys
from collections import defaultdict
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
SEED, P, LEVEL = 20261027, 1000, 0.025
exp = Path(args.experiment)
an = json.loads((exp / "analysis.json").read_text())
labels = json.loads((exp / "run_labels.json").read_text())["by_run"]
uval = json.loads((exp / "u_values.json").read_text())
switched = {(r["job"], r["trial"]) for r in map(json.loads, Path(args.switch_sort).expanduser().read_text().splitlines())
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
        if lab:
            trials.append({"task": c["task"], "arm": c["model"].split("/")[-1], "passed": r.get("verdict") == "pass",
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


def flat(recs, a):
    return [t for r in recs for t in r[a]]


# the measures; each takes per-task records {BEFORE: [...], AFTER: [...]}, as in u_values.py
def u1(recs):
    held = [r for r in recs if grade(r[BEFORE]) == "every"]
    return sum(grade(r[AFTER]) == "every" for r in held) / len(held) if held else None


def u2(recs):
    return sum((grade(r[BEFORE]), grade(r[AFTER])) in {("every", "some"), ("every", "never"), ("some", "never")} for r in recs)


def cost_per_pass(ts):
    p = sum(t["passed"] for t in ts)
    return sum(t["cost"] or 0 for t in ts) / p if p else None


def u4_cost(recs):
    b, a = cost_per_pass(flat(recs, BEFORE)), cost_per_pass(flat(recs, AFTER))
    return a / b if a and b else None


def u4_tokens(recs):
    b, a = (median(t["in"] + t["out"] for t in flat(recs, x)) for x in (BEFORE, AFTER))
    return a / b if a and b else None


def stopped(ts):
    return sum(t["ending"] == "completed" and not t["passed"] for t in ts) / len(ts)


def u5_stopped(recs):
    return stopped(flat(recs, AFTER)) - stopped(flat(recs, BEFORE))


def u5_refused(recs):
    return sum(sum("refus" in t["ending"].lower() for t in flat(recs, x)) * s for x, s in ((AFTER, 1), (BEFORE, -1)))


def u7(recs):
    hits = 0
    for r in recs:
        mb, ma = median(t["turns"] for t in r[BEFORE]), median(t["turns"] for t in r[AFTER])
        steps = max(mb, ma) >= 1.5 * max(min(mb, ma), 1)
        d = abs(prefix([t["traj"] for t in r[BEFORE]]) - prefix([t["traj"] for t in r[AFTER]]))
        hits += steps or d >= 2
    return hits / len(recs) if recs else None


pct = lambda v: f"{100 * v:.0f}%"
cnt = lambda v: f"{v:.0f}"
ratio = lambda v: f"x{v:.2f}"
pts = lambda v: f"{100 * v:+.1f} pts"
diff = lambda v: f"{v:+.0f}"

# name, measure, task set ("all" in scope or "same" grade in both real arms), side that is worse, format
QUESTIONS = [
    ("U1 held", u1, "all", "low", pct),
    ("U2 drops", u2, "all", "high", cnt),
    ("U4 cost per pass", u4_cost, "all", "high", ratio),
    ("U4 tokens per trial", u4_tokens, "all", "high", ratio),
    ("U5 stopped but failed", u5_stopped, "all", "high", pts),
    ("U5 refusals", u5_refused, "all", "high", diff),
    ("U7 path changed", u7, "same", None, pct),
]


def close(a, b):
    return a is None and b is None or a is not None and b is not None and abs(a - b) < 1e-9


for name, keep in FRAMINGS.items():
    by = defaultdict(lambda: {BEFORE: [], AFTER: []})
    for t in trials:
        if keep(t) and t["arm"] in (BEFORE, AFTER):
            by[t["task"]][t["arm"]].append(t)
    recs = [v for k, v in sorted(by.items()) if len(v[BEFORE]) >= 3 and len(v[AFTER]) >= 3]
    same = [v for v in recs if grade(v[BEFORE]) == grade(v[AFTER])]
    print(f"== {name}: {len(recs)} tasks in scope, {len(same)} with the same grade; {P} shuffles")
    if not recs:
        print()
        continue
    u = uval.get(name, {})
    fb, fa = flat(recs, BEFORE), flat(recs, AFTER)
    check(close(u1(recs), u.get("U1")) and u2(recs) == u.get("U2"), "U1 and U2 match u_values.json")
    cpp, tok = u.get("U4", {}).get("cost_per_pass", {}), u.get("U4", {}).get("tokens_per_trial_median", {})
    check(close(cost_per_pass(fb), cpp.get(BEFORE)) and close(cost_per_pass(fa), cpp.get(AFTER))
          and close(median(t["in"] + t["out"] for t in fb), tok.get(BEFORE))
          and close(median(t["in"] + t["out"] for t in fa), tok.get(AFTER)), "U4 matches u_values.json")
    st, rf = u.get("U5", {}).get("stopped_failed", {}), u.get("U5", {}).get("refusals", {})
    check(close(stopped(fb), st.get(BEFORE)) and close(stopped(fa), st.get(AFTER))
          and close(u5_refused(recs), (rf.get(AFTER) or 0) - (rf.get(BEFORE) or 0)), "U5 matches u_values.json")
    for q, fn, scope, worse, fmt in QUESTIONS:
        base = recs if scope == "all" else same
        obs = fn(base)
        if obs is None:
            print(f"   {q:<23} not defined in this framing")
            continue
        rnd = random.Random(SEED)
        null = []
        for _ in range(P):
            sh = []
            for v in base:
                pool = v[BEFORE] + v[AFTER]
                rnd.shuffle(pool)
                nb = len(v[BEFORE])
                sh.append({BEFORE: pool[:nb], AFTER: pool[nb:]})
            x = fn(sh)
            if x is not None:
                null.append(x)
        null.sort()
        n = len(null)
        p_lo = sum(x <= obs + 1e-12 for x in null) / n
        p_hi = sum(x >= obs - 1e-12 for x in null) / n
        side = "high" if p_hi <= LEVEL else "low" if p_lo <= LEVEL else None
        if null[0] == null[-1] == obs:
            verdict = "no events in either arm"
        elif side is None:
            verdict = "within baseline"
        elif worse is None:
            verdict = f"{'above' if side == 'high' else 'below'} baseline"
        else:
            verdict = f"{'worse' if side == worse else 'better'} than baseline"
        print(f"   {q:<23} observed {fmt(obs):>9}   same model: median {fmt(median(null))}, "
              f"middle 95% {fmt(null[int(.025 * n)])} to {fmt(null[int(.975 * n) - 1])}   "
              f"shuffles at or below {p_lo:.3f}, at or above {p_hi:.3f}{'' if n == P else f' (of {n})'}   {verdict}")
    print()

print("PASS: same-model baseline computed for U1, U2, U4, U5 and U7" if not fails
      else f"FAIL: {fails} check(s) failed; read the lines above")
sys.exit(1 if fails else 0)