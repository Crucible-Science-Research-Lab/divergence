# experiments/002-terminal-bench/intervals.py
"""
Note 002: intervals and the two extra verdict lines. Reads the database, changes nothing.

Run from the repository root, after the importer has run on both axes:
    python experiments/002-terminal-bench/intervals.py

Reads   experiments/002-terminal-bench/out/harbor.duckdb
            tables trials_models_confirmation, trials_confirmation
Writes  experiments/002-terminal-bench/out/intervals.txt   (also printed)

What it does, per axis and per scope (primary = the 61 unrevised tasks; all 89 alongside):
  1. Recomputes every scored point value with the importer's definitions and compares
     it with the two confirmation reports (EXPECT below). Any difference stops the script.
  2. 90% intervals as pre-registered: tasks resampled with replacement, 2,000 resamples,
     seed 20261002, 5th to 95th percentile. All arms share each resample (paired by task).
     Verdicts stay decided on the point values; the intervals are descriptive.
  3. Verdict lines without GLM-5 (model axis), and without trials that ended in an
     environment error.

Environment error = the sandbox or the test set-up failed (ENV_ERRORS below). A verifier
timeout or a missing reward file stays a failure, because the agent's own work can cause
it. Every ending is counted in the output so the split can be checked.

The scoring is the importer's and is not changed here; the thresholds and the minimum
number of logged trials per cell are read from the importer.
"""
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from divergence.importers.harbor import MIN_LOGGED, THRESHOLDS as TH   # noqa: E402

OUT = HERE / "out"
DB = OUT / "harbor.duckdb"
SEED, RESAMPLES = 20261002, 2000
ENV_ERRORS = {"DaytonaError", "AddTestsDirError", "DownloadVerifierDirError",
              "EnvironmentStartTimeoutError"}
KEPT_ERRORS = {"AgentTimeoutError", "VerifierTimeoutError", "RewardFileNotFoundError"}
FLAGGED = "GLM-5"
AXES = {
    "model": {"table": "trials_models_confirmation", "ref": "Claude-Opus-4.6",
              "arms": ["Claude-Opus-4.6", "DeepSeek-V3.2", "GLM-4.7", "GLM-5", "Kimi-k2.5"]},
    "harness": {"table": "trials_confirmation", "ref": None,
                "arms": ["Judy", "Meta-Harness", "Terminus-KIRA", "Terminus2", "WozCode"]},
}
# Point values from the two confirmation reports (sections 7, 7b and 9), in arm order.
# The reports are in reports/: harbor_import_models_confirmation.txt, harbor_import_confirmation.txt.
EXPECT = {
    ("model", "primary"): {
        "pass": ["230/305", "143/305", "120/305", "186/305", "147/305"],
        "flip": ["24/61", "22/61", "25/61", "23/61", "28/61"],
        "stopfail": ["37/260", "119/259", "77/195", "64/243", "95/236"],
        "M1": ["12/33", "11/33", "20/33", "14/33"],
        "M2": ["13/21", "16/22", "12/13", "16/19"],
        "M4": ["7.30x", "7.65x", "6.29x", "10.19x"],
        "M5": ["22/37", "33/39", "29/36", "29/38", "26/33"],
        "verdicts": "M1 3/4, M2 4/4, M3 4/4, M4 4/4, M5 5/5"},
    ("model", "all"): {
        "pass": ["280/445", "176/445", "147/445", "231/445", "189/445"],
        "flip": ["30/89", "33/89", "30/89", "32/89", "35/89"],
        "stopfail": ["92/364", "183/354", "139/284", "113/334", "161/344"],
        "M1": ["12/39", "11/39", "24/39", "16/39"],
        "M2": ["17/27", "20/28", "14/15", "19/23"],
        "M4": ["4.65x", "4.94x", "5.18x", "5.83x"],
        "M5": ["35/59", "46/56", "49/59", "41/57", "44/54"],
        "verdicts": "M1 3/4, M2 4/4, M3 3/4, M4 4/4, M5 5/5"},
    ("harness", "primary"): {
        "pass": ["250/305", "264/305", "258/305", "230/305", "241/305"],
        "flip": ["19/61", "16/61", "16/60", "24/61", "19/61"],
        "stopfail": ["26/262", "33/294", "21/264", "37/260", "43/279"],
        "H3flip": ["18/60", "16/60", "16/60", "24/60", "18/60"],
        "H3stop": ["26/261", "33/289", "21/264", "37/260", "43/278"],
        "H3": ["1.50x", "1.94x"],
        "verdicts": "H1 5/5, H2 4/5, H3 yes"},
    ("harness", "all"): {
        "pass": ["320/445", "340/445", "331/445", "280/445", "303/445"],
        "flip": ["32/89", "25/89", "23/88", "30/89", "24/89"],
        "stopfail": ["65/359", "75/410", "54/364", "92/364", "96/393"],
        "H3flip": ["31/88", "25/88", "23/88", "30/88", "23/88"],
        "H3stop": ["65/358", "75/405", "54/364", "92/364", "96/392"],
        "H3": ["1.35x", "1.70x"],
        "verdicts": "H1 5/5, H2 5/5, H3 no"},
}
SUM_KEYS = ("n", "p", "stop", "stopfail", "est", "est_pass", "cell", "flip", "same", "wide")
ROWS = {
    "model": [("pass", "passed (all trials)", "all"), ("flip", "tasks that flip", "all"),
              ("stopfail", "stopped by itself, but failed", "all"),
              ("M1", "M1 held, of the reference's every-time tasks", "others"),
              ("M2", "M2 'some' among the tasks not held", "others"),
              ("M3r", "M3 stopped-but-failed, ratio to reference", "others"),
              ("M3d", "M3 stopped-but-failed, difference", "others"),
              ("M4", "M4 log-est. input per pass, ratio", "others"),
              ("M5", "M5 same-outcome tasks at 2x+ input", "all")],
    "harness": [("pass", "passed (all trials)", "all"), ("flip", "H1 tasks that flip", "all"),
                ("stopfail", "H2 stopped by itself, but failed", "all"),
                ("H3flip", "H3 flips, tasks in every harness", "all"),
                ("H3stop", "H3 stopped-but-failed, same tasks", "all"),
                ("H3flipr", "H3 flips, highest / lowest", "one"),
                ("H3stopr", "H3 stopped-but-failed, highest / lowest", "one")],
}
RATIOS = {"M3r", "M4", "H3flipr", "H3stopr"}

lines_out = []


def say(s=""):
    print(s)
    lines_out.append(s)


def truthy(v):
    return v is not None and str(v).strip().lower() in ("true", "1", "1.0")


def need(n, share):
    return max(1, math.ceil(share * n - 1e-9))


def val(x):
    """A measure as one number: a share for (count, of), the number itself otherwise."""
    if isinstance(x, tuple):
        return x[0] / x[1] if x[1] else None
    return x


def pctl(xs, q):
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def load(con, table):
    out = []
    for arm, task, revised, verdict, term, has_log, est in con.execute(
            f"SELECT arm, task, revised, verdict, termination, has_log, est_in_tok "
            f"FROM {table}").fetchall():
        est = None if est is None or (isinstance(est, float) and math.isnan(est)) else float(est)
        out.append({"arm": arm, "task": task, "revised": truthy(revised),
                    "passed": verdict == "pass", "term": str(term),
                    "logged": truthy(has_log), "est": est})
    return out


def aggregate(trials, arms):
    """Per arm and task, the counts every measure is a sum of (importer definitions)."""
    by = {a: defaultdict(list) for a in arms}
    for t in trials:
        if t["arm"] in by:
            by[t["arm"]][t["task"]].append(t)
    agg = {a: {} for a in arms}
    for a in arms:
        for task, ts in by[a].items():
            lg = [t for t in ts if t["logged"]]
            le = [t for t in lg if t["est"]]
            p = sum(t["passed"] for t in ts)
            cell = len(lg) >= MIN_LOGGED
            outcomes = {t["passed"] for t in lg}
            same = cell and len(outcomes) == 1 and all((t["est"] or 0) > 0 for t in lg)
            ests = [t["est"] for t in lg] if same else []
            stopped = [t for t in ts if t["term"] == "completed"]
            agg[a][task] = {
                "n": len(ts), "p": p,
                "grade": "every" if p == len(ts) else "never" if p == 0 else "some",
                "stop": len(stopped), "stopfail": sum(not t["passed"] for t in stopped),
                "est": sum(t["est"] for t in le), "est_pass": sum(t["passed"] for t in le),
                "cell": int(cell), "flip": int(cell and len(outcomes) == 2),
                "same": int(same),
                "wide": int(bool(same) and max(ests) / min(ests) >= TH["M5_spread"])}
    return agg


def measure(agg, axis, draw, common):
    """Every measure over a list of tasks (a task may appear more than once)."""
    arms, ref = AXES[axis]["arms"], AXES[axis]["ref"]
    m, tot = {}, {}
    for a in arms:
        s, g = Counter(), agg[a]
        for task in draw:
            r = g.get(task)
            if r is None:
                continue
            for k in SUM_KEYS:
                s[k] += r[k]
            if task in common:
                s["c_n"] += 1
                s["c_flip"] += r["flip"]
                s["c_stop"] += r["stop"]
                s["c_stopfail"] += r["stopfail"]
        tot[a] = s
        m["pass", a] = (s["p"], s["n"])
        m["flip", a] = (s["flip"], s["cell"])
        m["stopfail", a] = (s["stopfail"], s["stop"])
    if axis == "model":
        r0 = val(m["stopfail", ref])
        p0 = tot[ref]["est"] / tot[ref]["est_pass"] if tot[ref]["est_pass"] else None
        for a in arms:
            m["M5", a] = (tot[a]["wide"], tot[a]["same"])
            if a == ref:
                continue
            ref_every = held = some = 0
            for task in draw:
                x, y = agg[ref].get(task), agg[a].get(task)
                if x is None or y is None or x["grade"] != "every":
                    continue
                ref_every += 1
                held += y["grade"] == "every"
                some += y["grade"] == "some"
            m["M1", a] = (held, ref_every)
            m["M2", a] = (some, ref_every - held)
            r1 = val(m["stopfail", a])
            p1 = tot[a]["est"] / tot[a]["est_pass"] if tot[a]["est_pass"] else None
            m["M3r", a] = r1 / r0 if r0 and r1 is not None else None
            m["M3d", a] = r1 - r0 if r0 is not None and r1 is not None else None
            m["M4", a] = p1 / p0 if p0 and p1 else None
    else:
        for a in arms:
            m["H3flip", a] = (tot[a]["c_flip"], tot[a]["c_n"])
            m["H3stop", a] = (tot[a]["c_stopfail"], tot[a]["c_stop"])
        for key, name in (("H3flipr", "H3flip"), ("H3stopr", "H3stop")):
            rs = [val(m[name, a]) for a in arms if val(m[name, a]) is not None]
            m[key, ""] = max(rs) / min(rs) if len(rs) >= 2 and min(rs) > 0 else None
    return m


def verdicts(m, axis, skip=()):
    """Verdict lines from point values, by the pre-registered rules. -> (lines, short form)."""
    arms, ref = AXES[axis]["arms"], AXES[axis]["ref"]
    out, short = [], []

    def line(name, holds, k):
        n = sum(holds)
        out.append(f"{name} {'SUPPORTED' if n >= k else 'NOT SUPPORTED'}, holds for {n} of "
                   f"{len(holds)} (needs {k})")
        short.append(f"{name} {n}/{len(holds)}")

    if axis == "model":
        others = [a for a in arms if a != ref and a not in skip]
        k = need(len(others), TH["model_arms_share"])
        v = lambda key, a: val(m[key, a])
        line("M1", [v("M1", a) is not None and v("M1", a) < TH["M1_max_hold"] for a in others], k)
        line("M2", [v("M2", a) is not None and v("M2", a) >= TH["M2_min_some"] for a in others], k)
        line("M3", [v("M3r", a) is not None and v("M3r", a) >= TH["M3_ratio"]
                    and v("M3d", a) >= TH["M3_points"] for a in others], k)
        line("M4", [v("M4", a) is not None and v("M4", a) >= TH["M4_ratio"] for a in others], k)
        five = [a for a in arms if a not in skip]
        line("M5", [v("M5", a) is not None and v("M5", a) >= TH["M5_min_share"] for a in five],
             len(five))
    else:
        k = need(len(arms), TH["harness_arms_share"])
        line("H1", [val(m["flip", a]) is not None and val(m["flip", a]) >= TH["H1_min_flip"]
                    for a in arms], k)
        line("H2", [val(m["stopfail", a]) is not None
                    and val(m["stopfail", a]) >= TH["H2_min_stopfail"] for a in arms], k)
        ok = []
        for name in ("H3flip", "H3stop"):
            rs = [val(m[name, a]) for a in arms if val(m[name, a]) is not None]
            if len(rs) < 2:
                ok.append(False)
            elif min(rs) > 0:
                ok.append(max(rs) / min(rs) >= TH["H3_ratio"])
            else:
                ok.append(max(rs) >= TH["H3_zero_floor"])
        out.append(f"H3 {'SUPPORTED' if all(ok) else 'NOT SUPPORTED'} (flips "
                   f"{'holds' if ok[0] else 'does not hold'}, stopped-but-failed "
                   f"{'holds' if ok[1] else 'does not hold'}; needs both)")
        short.append(f"H3 {'yes' if all(ok) else 'no'}")
    return out, ", ".join(short)


def show(key, x):
    """Point value as text."""
    if x is None or (isinstance(x, tuple) and not x[1]):
        return "-"
    if isinstance(x, tuple):
        return f"{x[0]}/{x[1]} {100 * x[0] / x[1]:.1f}%"
    return f"{x:.2f}x" if key in RATIOS else f"{100 * x:+.1f} pts"


def show_range(key, lo, hi):
    if key in RATIOS:
        return f"{lo:.2f}x to {hi:.2f}x"
    if key == "M3d":
        return f"{100 * lo:+.1f} to {100 * hi:+.1f} pts"
    return f"{100 * lo:.1f}% to {100 * hi:.1f}%"


def as_report(m, axis):
    """Point values in the same text form as EXPECT."""
    arms, ref = AXES[axis]["arms"], AXES[axis]["ref"]
    frac = lambda key, names: [f"{m[key, a][0]}/{m[key, a][1]}" for a in names]
    got = {"pass": frac("pass", arms), "flip": frac("flip", arms),
           "stopfail": frac("stopfail", arms)}
    if axis == "model":
        others = [a for a in arms if a != ref]
        got.update({"M1": frac("M1", others), "M2": frac("M2", others), "M5": frac("M5", arms),
                    "M4": [f"{m['M4', a]:.2f}x" if m["M4", a] else "-" for a in others]})
    else:
        got.update({"H3flip": frac("H3flip", arms), "H3stop": frac("H3stop", arms),
                    "H3": [f"{m[k, '']:.2f}x" if m[k, ""] else "-"
                           for k in ("H3flipr", "H3stopr")]})
    got["verdicts"] = verdicts(m, axis)[1]
    return got


def rows_for(axis, key_kind):
    arms, ref = AXES[axis]["arms"], AXES[axis]["ref"]
    return arms if key_kind == "all" else [""] if key_kind == "one" else \
        [a for a in arms if a != ref]


def common_tasks(agg, axis, tasks):
    if axis != "harness":
        return set()
    return {t for t in tasks
            if all(agg[a].get(t, {}).get("cell") for a in AXES[axis]["arms"])}


def intervals(agg, axis, tasks, common):
    rng = random.Random(SEED)
    n = len(tasks)
    samples, undefined = defaultdict(list), Counter()
    for _ in range(RESAMPLES):
        draw = [tasks[rng.randrange(n)] for _ in range(n)]
        for key, x in measure(agg, axis, draw, common).items():
            y = val(x)
            if y is None:
                undefined[key] += 1
            else:
                samples[key].append(y)
    return ({k: (pctl(xs, 0.05), pctl(xs, 0.95)) for k, xs in samples.items()}, undefined)


def variant_lines(m, axis):
    """Compact point values for a variant (no intervals)."""
    arms, ref = AXES[axis]["arms"], AXES[axis]["ref"]
    out = []
    if axis == "model":
        for a in arms:
            if a == ref:
                continue
            out.append(f"{a} against {ref}: passed {show('pass', m['pass', a])}; "
                       f"M1 held {show('M1', m['M1', a])}; M2 'some' {show('M2', m['M2', a])}; "
                       f"M3 {show('stopfail', m['stopfail', a])} against "
                       f"{show('stopfail', m['stopfail', ref])}; M4 {show('M4', m['M4', a])}")
        out.append("M5: " + ", ".join(f"{a} {show('M5', m['M5', a])}" for a in arms))
    else:
        out.append("passed: " + ", ".join(f"{a} {show('pass', m['pass', a])}" for a in arms))
        out.append("H1 flips: " + ", ".join(f"{a} {show('flip', m['flip', a])}" for a in arms))
        out.append("H2 stopped-but-failed: " + ", ".join(
            f"{a} {show('stopfail', m['stopfail', a])}" for a in arms))
        out.append(f"H3 highest / lowest: flips {show('H3flipr', m['H3flipr', ''])}, "
                   f"stopped-but-failed {show('H3stopr', m['H3stopr', ''])}")
    return out


def run_axis(con, axis):
    arms = AXES[axis]["arms"]
    trials = [t for t in load(con, AXES[axis]["table"]) if t["arm"] in arms]
    say()
    say(f"== {axis} axis " + "=" * 60)
    endings = defaultdict(Counter)
    for t in trials:
        if t["term"] != "completed":
            endings[t["term"]][t["arm"]] += 1
    say("endings other than 'completed' (all 89 tasks):")
    for term in sorted(endings):
        kind = ("dropped in the environment-error line" if term in ENV_ERRORS else
                "kept as a failure" if term in KEPT_ERRORS else
                "!! unclassified, kept as a failure")
        say(f"  {term:<30} " + ", ".join(f"{a} {n}" for a, n in sorted(endings[term].items()))
            + f"   [{kind}]")

    scopes = [("primary", sorted({t["task"] for t in trials if not t["revised"]})),
              ("all", sorted({t["task"] for t in trials}))]
    agg = aggregate(trials, arms)
    points = {}
    for scope, tasks in scopes:
        common = common_tasks(agg, axis, tasks)
        points[scope] = (measure(agg, axis, tasks, common), common)
        got = as_report(points[scope][0], axis)
        if got != EXPECT[axis, scope]:
            say(f"!! {scope} scope does not match the confirmation report")
            for k, want in EXPECT[axis, scope].items():
                if got.get(k) != want:
                    say(f"   {k}: report {want}")
                    say(f"   {k}: here   {got.get(k)}")
            raise SystemExit("stopped: point values differ from the report; no intervals made")
    say("point values and verdicts match the confirmation report on both scopes")

    kept = [t for t in trials if t["term"] not in ENV_ERRORS]
    agg_env = aggregate(kept, arms)
    for scope, tasks in scopes:
        m, common = points[scope]
        ci, undefined = intervals(agg, axis, tasks, common)
        say()
        say(f"-- {axis} axis, {'PRIMARY scope' if scope == 'primary' else 'all tasks (alongside)'}"
            f": {len(tasks)} tasks; {RESAMPLES:,} resamples of tasks, seed {SEED}")
        say(f"{'measure':<46} {'arm':<16} {'point':<18} 90% interval")
        for key, label, kind in ROWS[axis]:
            for a in rows_for(axis, kind):
                rng_txt = show_range(key, *ci[key, a]) if (key, a) in ci else "-"
                note = (f"   (undefined in {undefined[key, a]} resamples)"
                        if undefined[key, a] else "")
                say(f"{label:<46} {a:<16} {show(key, m[key, a]):<18} {rng_txt}{note}")
        say("verdicts as scored (point values):")
        for ln in verdicts(m, axis)[0]:
            say(f"   {ln}")
        if axis == "model":
            say(f"verdicts without {FLAGGED}:")
            for ln in verdicts(m, axis, skip=(FLAGGED,))[0]:
                say(f"   {ln}")
        in_scope = set(tasks)
        n_drop = sum(t["term"] in ENV_ERRORS and t["task"] in in_scope for t in trials)
        m_env = measure(agg_env, axis, tasks, common_tasks(agg_env, axis, tasks))
        say(f"without the {n_drop} trials that ended in an environment error:")
        for ln in variant_lines(m_env, axis):
            say(f"   {ln}")
        for ln in verdicts(m_env, axis)[0]:
            say(f"   {ln}")


def main():
    import duckdb
    if not DB.exists():
        raise SystemExit(f"not found: {DB.relative_to(REPO)}; run the importer on both axes first")
    con = duckdb.connect(str(DB), read_only=True)
    say(f"Note two: intervals and extra verdict lines, {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    say(f"source {DB.name}; 90% interval = 5th to 95th percentile over {RESAMPLES:,} resamples of "
        f"tasks (with replacement, seed {SEED})")
    say("verdicts are decided on the point values; the intervals are descriptive")
    for axis in ("model", "harness"):
        run_axis(con, axis)
    con.close()
    OUT.mkdir(exist_ok=True)
    report = OUT / "intervals.txt"
    report.write_text("\n".join(lines_out) + "\n")
    print(f"\nreport: {report.relative_to(REPO)}")


if __name__ == "__main__":
    main()