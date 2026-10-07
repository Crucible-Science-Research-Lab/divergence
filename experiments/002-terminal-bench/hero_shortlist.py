# experiments/002-terminal-bench/hero_shortlist.py
"""
Note 002: shortlist the candidate cells for the note's worked example (model axis).
Reads the database, changes nothing.

Run from the repository root, after the importer has run on the model axis:
    python experiments/002-terminal-bench/hero_shortlist.py

Reads   experiments/002-terminal-bench/out/harbor.duckdb
            tables trials_models_confirmation, calls_models_confirmation
Writes  experiments/002-terminal-bench/out/hero_shortlist.txt   (also printed)

This picks an ILLUSTRATION, after the outcomes were seen. It scores no hypothesis and
does not touch the importer or the step key. The ranking only narrows the pool: the cell
shown in the note (sparql-university, GLM-4.7) was then chosen by reading the runs of the
top candidates, and it is rank 8 of the 30 kept, not rank 1. The note says how it was
chosen and which candidates were set aside.

Pool   primary scope (61 tasks Terminal-Bench 2.1 did not revise); a (task, cheaper
       model) cell where Opus 4.6 passes 5 of 5 and the cheaper model passes 1 to 4.
       GLM-5 is left out of the hero pool (the flagged arm: other parser, no tokens).
Cuts   in this order:
       1. all five runs have a step log
       2. no run ended in a sandbox or verifier error
       3. at least one failing run stopped by itself with a done call
       4. no passing run and failing run share an identical path (listed separately)
Rank   on the collapsed step key (`path`, what analyse.py builds the tree from):
       1. clean fork: the passing runs stay together longer than any of them stays
          with a failing run ("pass"), or the same for the failing runs ("fail"),
          or both ("both")
       2. 2-3 or 3-2 cells ahead of 4-1 and 1-4
       3. no timeout among the failing runs
       4. gap: mean steps shared by passing pairs minus mean shared by mixed pairs
          (failing pairs minus mixed pairs when only one run passed)
       5. shorter longest path
"""
import itertools
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "out"
DB = OUT / "harbor.duckdb"
TRIALS, CALLS = "trials_models_confirmation", "calls_models_confirmation"
REF = "Claude-Opus-4.6"
CHEAPER = ["DeepSeek-V3.2", "GLM-4.7", "GLM-5", "Kimi-k2.5"]
HERO_ARMS = ["DeepSeek-V3.2", "GLM-4.7", "Kimi-k2.5"]
# From reports/harbor_import_models_confirmation.txt, section 8, primary scope.
EXPECT = {"tasks": 61, "ref_every": 33,
          "some": {"DeepSeek-V3.2": 13, "GLM-4.7": 16, "GLM-5": 12, "Kimi-k2.5": 16}}
CLEAN_ENDINGS = {"completed", "AgentTimeoutError"}
ENDING = {"completed": "stopped", "AgentTimeoutError": "timeout"}
LETTER = {"inspect": "i", "edit": "e", "run": "r", "install": "n", "fetch": "f",
          "delete": "x", "done": "D", "other": "o"}
TOP = 12
NEED = {"arm", "task", "revised", "run_index", "run_id", "verdict", "termination",
        "has_log", "declared_done", "path"}

lines_out = []


def say(s=""):
    print(s)
    lines_out.append(s)


def isnull(v):
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return str(v).strip() in ("", "None", "nan", "NaN", "<NA>")


def truthy(v):
    return (not isnull(v)) and str(v).strip().lower() in ("true", "1", "1.0")


def steps(v):
    return [] if isnull(v) else str(v).split("|")


def letters(path):
    return "".join(LETTER.get(s, "?") for s in path)


def shared(a, b):
    """Steps two paths have in common from the start."""
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def grade(ts):
    p = sum(t["verdict"] == "pass" for t in ts)
    return "every" if p == len(ts) else "never" if p == 0 else "some"


def evaluate(ts):
    """The five trials of one (task, cheaper model) cell -> (cut reason or None, measures)."""
    ts = sorted(ts, key=lambda t: t["run_index"])
    paths = [steps(t["path"]) for t in ts]
    base = {"ts": ts, "paths": paths}
    if len(ts) != 5 or not all(truthy(t["has_log"]) for t in ts):
        return "1 not five runs with a step log", base
    if any(t["termination"] not in CLEAN_ENDINGS for t in ts):
        return "2 a run ended in a sandbox or verifier error", base
    P = [i for i, t in enumerate(ts) if t["verdict"] == "pass"]
    F = [i for i, t in enumerate(ts) if t["verdict"] != "pass"]
    done_fail = [i for i in F if ts[i]["termination"] == "completed"
                 and truthy(ts[i]["declared_done"])]
    if not done_fail:
        return "3 no failing run stopped by itself with a done call", base
    if {tuple(paths[i]) for i in P} & {tuple(paths[i]) for i in F}:
        return "4 same path, different outcome", base

    pp = [shared(paths[a], paths[b]) for a, b in itertools.combinations(P, 2)]
    ff = [shared(paths[a], paths[b]) for a, b in itertools.combinations(F, 2)]
    pf = [shared(paths[a], paths[b]) for a in P for b in F]
    clean_pass = bool(pp) and min(pp) > max(pf)
    clean_fail = bool(ff) and min(ff) > max(pf)
    mean = statistics.mean
    base.update({
        "n_pass": len(P), "n_fail": len(F),
        "fork": "both" if clean_pass and clean_fail else "pass" if clean_pass
                else "fail" if clean_fail else "-",
        "clean": int(clean_pass) + int(clean_fail),
        "timeouts": sum(ts[i]["termination"] == "AgentTimeoutError" for i in F),
        "done_fail": len(done_fail),
        "pp": mean(pp) if pp else None, "ff": mean(ff) if ff else None, "pf": mean(pf),
        "gap": (mean(pp) if pp else mean(ff)) - mean(pf),
        "trunk": min(pp + ff + pf),          # steps all five runs share
        "part_by": max(pf) + 1,              # step by which every pass has left every fail
        "longest": max(len(p) for p in paths),
    })
    return None, base


def rank_key(c):
    return (-c["clean"], 0 if min(c["n_pass"], c["n_fail"]) == 2 else 1,
            1 if c["timeouts"] else 0, -c["gap"], c["longest"], c["task"], c["arm"])


def f1(x):
    return f"{x:.1f}" if x is not None else "-"


def run_line(t, path, trunk=0):
    done = "done" if truthy(t["declared_done"]) else "-"
    ls = letters(path)
    ls = (ls[:trunk] + " " + ls[trunk:]) if trunk else ls
    return (f"      run {t['run_index']}  {t['verdict']:<4}  "
            f"{ENDING.get(t['termination'], t['termination']):<8} {done:<4} "
            f"{len(path):>3}  {ls}")


def main():
    import duckdb
    if not DB.exists():
        raise SystemExit(f"not found: {DB.relative_to(REPO)}; run the importer on the model axis first")
    con = duckdb.connect(str(DB), read_only=True)
    cur = con.execute(f"SELECT * FROM {TRIALS}")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if NEED - set(cols):
        raise SystemExit(f"columns missing from {TRIALS}: {sorted(NEED - set(cols))}")
    fall = {(task, arm): (d, n) for task, arm, n, d in con.execute(
        f"SELECT task, arm, count(*), "
        f"sum(CASE WHEN defaulted IS NOT NULL AND defaulted <> '' THEN 1 ELSE 0 END) "
        f"FROM {CALLS} GROUP BY 1, 2").fetchall()}
    con.close()

    per = defaultdict(lambda: defaultdict(list))
    for t in rows:
        if not truthy(t["revised"]):
            per[t["arm"]][t["task"]].append(t)
    grades = {arm: {task: grade(ts) for task, ts in per[arm].items()} for arm in per}
    ref_every = sorted(k for k, g in grades.get(REF, {}).items() if g == "every")
    pool = {arm: [k for k in ref_every if grades.get(arm, {}).get(k) == "some"]
            for arm in CHEAPER}

    say("Hero shortlist, note two, model axis (an illustration; no hypothesis is scored here)")
    say(f"source {DB.name}: {TRIALS}, {CALLS}; primary scope only")
    found = {"tasks": len(per.get(REF, {})), "ref_every": len(ref_every),
             "some": {arm: len(pool[arm]) for arm in CHEAPER}}
    say(f"tasks {found['tasks']}; {REF} passes every time on {found['ref_every']}; "
        "of those, 'some' on: " + ", ".join(f"{a} {n}" for a, n in found["some"].items()))
    if found != EXPECT:
        say(f"!! does not match the confirmation report: expected {EXPECT}")
        raise SystemExit("stopped: counts differ from the report; nothing was ranked")
    say("counts match the confirmation report (section 8, primary scope)")
    say(f"hero pool: {sum(len(pool[a]) for a in HERO_ARMS)} cells "
        f"({', '.join(HERO_ARMS)}); GLM-5's {len(pool['GLM-5'])} left out (flagged arm)")

    kept, cut, same_path = [], Counter(), []
    for arm in HERO_ARMS:
        for task in pool[arm]:
            reason, c = evaluate(per[arm][task])
            c.update(task=task, arm=arm)
            if reason:
                cut[reason] += 1
                if reason.startswith("4"):
                    same_path.append(c)
                continue
            d, n = fall.get((task, arm), (0, 0))
            c["fall"] = f"{100 * d / n:.0f}%" if n else "-"
            c["others"] = " ".join(grades.get(a, {}).get(task, "?")[0] for a in CHEAPER)
            kept.append(c)
    kept.sort(key=rank_key)

    say()
    say("cuts, in order:")
    for reason in sorted(cut):
        say(f"  {cut[reason]:>3}  {reason[2:]}")
    say(f"  {len(kept):>3}  kept; clean fork: both "
        f"{sum(c['fork'] == 'both' for c in kept)}, pass side "
        f"{sum(c['fork'] == 'pass' for c in kept)}, fail side "
        f"{sum(c['fork'] == 'fail' for c in kept)}, none "
        f"{sum(c['fork'] == '-' for c in kept)}")

    say()
    say("ranked. pass = passes of 5; fork = which side stays together; t/o = failing runs")
    say("that timed out; dnf = failing runs that stopped with a done call; pp, ff, pf = mean")
    say("steps shared by passing pairs, failing pairs, mixed pairs; trunk = steps all five")
    say("share; part = step by which every passing run has left every failing run; long =")
    say("longest path; fall = calls whose program fell through to 'run'; others = the task's")
    say("grade on DeepSeek, GLM-4.7, GLM-5, Kimi (e every, s some, n never)")
    say(f"{'#':>2} {'task':<34} {'arm':<13} {'pass':>4} {'fork':>4} {'t/o':>3} {'dnf':>3} "
        f"{'pp':>5} {'ff':>5} {'pf':>5} {'gap':>5} {'trunk':>5} {'part':>4} {'long':>4} "
        f"{'fall':>4}  others")
    for i, c in enumerate(kept, 1):
        say(f"{i:>2} {c['task'][:34]:<34} {c['arm']:<13} {c['n_pass']:>4} {c['fork']:>4} "
            f"{c['timeouts']:>3} {c['done_fail']:>3} {f1(c['pp']):>5} {f1(c['ff']):>5} "
            f"{f1(c['pf']):>5} {c['gap']:>5.1f} {c['trunk']:>5} {c['part_by']:>4} "
            f"{c['longest']:>4} {c['fall']:>4}  {c['others']}")

    say()
    say(f"top {min(TOP, len(kept))}: the five paths (a space marks the end of the shared trunk)")
    say("  " + ", ".join(f"{v} {k}" for k, v in LETTER.items())
        + "; columns: run, outcome, ending, done call, steps, path")
    for i, c in enumerate(kept[:TOP], 1):
        say(f"-- {i}. {c['task']}  {c['arm']}  {c['n_pass']}/5  fork {c['fork']}")
        for t, p in zip(c["ts"], c["paths"]):
            say(run_line(t, p, c["trunk"]))
        say(f"   {REF} on the same task:")
        for t in sorted(per[REF][c["task"]], key=lambda t: t["run_index"]):
            say(run_line(t, steps(t["path"])))

    say()
    say(f"same path, different outcome ({len(same_path)} cells; not hero candidates, "
        "possible second exhibit):")
    for c in same_path:
        n_pass = sum(t["verdict"] == "pass" for t in c["ts"])
        say(f"-- {c['task']}  {c['arm']}  {n_pass}/5")
        for t, p in zip(c["ts"], c["paths"]):
            say(run_line(t, p))

    OUT.mkdir(exist_ok=True)
    report = OUT / "hero_shortlist.txt"
    report.write_text("\n".join(lines_out) + "\n")
    print(f"\nreport: {report.relative_to(REPO)}")


if __name__ == "__main__":
    main()