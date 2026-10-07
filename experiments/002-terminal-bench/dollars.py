# experiments/002-terminal-bench/dollars.py
"""
Note 002: dollars. Recorded dollars where the log has them, labelled estimates where it
does not. Reads the database, changes nothing.

Run from the repository root, after the importer has run on both axes:
    python experiments/002-terminal-bench/dollars.py

Reads   experiments/002-terminal-bench/out/harbor.duckdb
            tables trials_models_confirmation, trials_confirmation
Writes  experiments/002-terminal-bench/out/dollars.txt   (also printed)

What is recorded and what is estimated
  Opus 4.6, DeepSeek V3.2   dollars recorded for every trial: used as recorded.
  GLM-4.7, Kimi K2.5        tokens recorded, no dollars, no cached tokens: ESTIMATED at the
                            maker's list price, as a range.
                              upper  every input token at the full input price (what the
                                     log records; the convention of Princeton's HAL)
                              lower  the cache price on 95% of input, the highest cached
                                     share recorded on this harness (DeepSeek's)
  GLM-5                     no token counts: left out.
An estimate is what these tokens would cost at the maker's list price. It is not what the
submitter paid (GLM-4.7 and Kimi ran through Ollama cloud).

Order of work
  1. Recompute the report's recorded dollars per pass and provider tokens per pass and
     compare; any difference stops the script.
  2. Method check: do tokens x list price reproduce the recorded dollars for the two models
     that have both? Printed, not enforced.
  3. Per run and per pass, with 90% intervals (tasks resampled with replacement, 2,000
     resamples, seed 20261002, all arms sharing each resample).
  4. The same on the tasks both models pass every time, where per run and per pass are
     the same thing and the two models are compared on the same tasks.

per run  = dollars over every trial that has them / those trials
per pass = dollars over every trial that has them, passed or not / the passes among them
"""
import math
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "out"
DB = OUT / "harbor.duckdb"
SEED, RESAMPLES = 20261002, 2000
ASSUMED_CACHED = 0.95
REF = "Claude-Opus-4.6"
# Dollars per million tokens. "write" is Anthropic's 5-minute cache-write price.
PRICES = {
    "Claude-Opus-4.6": {"in": 5.00, "cache": 0.50, "write": 6.25, "out": 25.00,
                        "src": "Anthropic price page, read 2 Oct 2026; still listed"},
    "DeepSeek-V3.2": {"in": 0.28, "cache": 0.028, "out": 0.42,
                      "src": "secondary source (BenchLM); no longer on DeepSeek's price page"},
    "GLM-4.7": {"in": 0.60, "cache": 0.11, "out": 2.20,
                "src": "Z.ai price page, read 2 Oct 2026; still listed"},
    "Kimi-k2.5": {"in": 0.60, "cache": 0.10, "out": 3.00,
                  "src": "secondary source (eesel, citing Moonshot); no longer on Kimi's price page"},
}
ESTIMATED = ["GLM-4.7", "Kimi-k2.5"]
AXES = {
    "model": {"table": "trials_models_confirmation",
              "arms": ["Claude-Opus-4.6", "DeepSeek-V3.2", "GLM-4.7", "GLM-5", "Kimi-k2.5"]},
    "harness": {"table": "trials_confirmation",
                "arms": ["Judy", "Meta-Harness", "Terminus-KIRA", "Terminus2", "WozCode"]},
}
# From the two confirmation reports, sections 7 and 7b, in arm order ("-" = not recorded).
# The reports are in reports/: harbor_import_models_confirmation.txt, harbor_import_confirmation.txt.
EXPECT = {
    ("model", "primary"): {"dollars per pass": ["0.80", "0.07", "-", "-", "-"],
                           "input per pass (M)": ["0.29", "1.56", "1.82", "-", "2.42"],
                           "output per pass (k)": ["20", "36", "59", "-", "42"]},
    ("model", "all"): {"dollars per pass": ["1.05", "0.08", "-", "-", "-"],
                       "input per pass (M)": ["0.47", "1.69", "1.96", "-", "2.32"],
                       "output per pass (k)": ["23", "40", "60", "-", "42"]},
    ("harness", "primary"): {"dollars per pass": ["-", "1.89", "1.69", "0.80", "-"]},
    ("harness", "all"): {"dollars per pass": ["-", "2.69", "2.16", "1.05", "-"]},
}
SUM_KEYS = ("n", "p", "c_sum", "c_n", "c_pass", "t_in", "t_cache", "t_out", "t_n", "t_pass")

lines_out = []


def say(s=""):
    print(s)
    lines_out.append(s)


def truthy(v):
    return v is not None and str(v).strip().lower() in ("true", "1", "1.0")


def num(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v)


def pctl(xs, q):
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def div(a, b):
    return a / b if a is not None and b else None


def priced(arm, t_in, t_cache, t_out, how):
    """Dollars for token sums under one convention."""
    p = PRICES[arm]
    if how == "none":              # every input token at the full price
        inp = t_in * p["in"]
    elif how == "recorded":        # recorded cached tokens at the cache price
        inp = (t_in - t_cache) * p["in"] + t_cache * p["cache"]
    elif how == "write":           # as "recorded", the rest at the cache-write price
        inp = (t_in - t_cache) * p["write"] + t_cache * p["cache"]
    else:                          # "assumed": the cache price on ASSUMED_CACHED of input
        inp = t_in * ((1 - ASSUMED_CACHED) * p["in"] + ASSUMED_CACHED * p["cache"])
    return (inp + t_out * p["out"]) / 1e6


def load(con, table):
    out = []
    for arm, task, revised, verdict, in_tok, out_tok, cache_tok, cost, started in con.execute(
            f"SELECT arm, task, revised, verdict, in_tok, out_tok, cache_tok, cost_usd, "
            f"started_at FROM {table}").fetchall():
        out.append({"arm": arm, "task": task, "revised": truthy(revised),
                    "passed": verdict == "pass", "in": num(in_tok), "out": num(out_tok),
                    "cache": num(cache_tok), "cost": num(cost), "started": str(started or "")})
    return out


def aggregate(trials, arms):
    agg = {a: defaultdict(Counter) for a in arms}
    for t in trials:
        if t["arm"] not in agg:
            continue
        r = agg[t["arm"]][t["task"]]
        r["n"] += 1
        r["p"] += t["passed"]
        if t["cost"] is not None:           # importer: dollar cost recorded
            r["c_sum"] += t["cost"]
            r["c_n"] += 1
            r["c_pass"] += t["passed"]
        if t["in"]:                         # importer: tokens recorded
            r["t_in"] += t["in"]
            r["t_cache"] += t["cache"] or 0
            r["t_out"] += t["out"] or 0
            r["t_n"] += 1
            r["t_pass"] += t["passed"]
    return agg


def totals(agg, arms, draw, keep=None):
    tot = {a: Counter() for a in arms}
    for a in arms:
        for task in draw:
            if keep is not None and task not in keep:
                continue
            r = agg[a].get(task)
            if r:
                for k in SUM_KEYS:
                    tot[a][k] += r[k]
    return tot


def every(agg, arm, task):
    r = agg[arm].get(task)
    return bool(r) and r["n"] > 0 and r["p"] == r["n"]


def measure(agg, axis, draw):
    arms = AXES[axis]["arms"]
    tot = totals(agg, arms, draw)
    m = {}
    for a in arms:
        s = tot[a]
        if s["c_n"]:
            m["rec_run", a] = s["c_sum"] / s["c_n"]
            m["rec_pass", a] = div(s["c_sum"], s["c_pass"])
        if s["t_n"]:
            m["in_pass", a] = div(s["t_in"] / 1e6, s["t_pass"])
            m["out_pass", a] = div(s["t_out"] / 1e3, s["t_pass"])
    if axis == "harness":
        for a in arms:
            if ("rec_pass", a) in m and a != "Terminus2":
                m["rec_pass_x", a] = div(m["rec_pass", a], m.get(("rec_pass", "Terminus2")))
        return m
    for a in arms:
        s = tot[a]
        if a not in PRICES or not s["t_n"]:
            continue
        none = priced(a, s["t_in"], s["t_cache"], s["t_out"], "none")
        m["nc_run", a] = none / s["t_n"]
        m["nc_pass", a] = div(none, s["t_pass"])
        if a in ESTIMATED:
            low = priced(a, s["t_in"], s["t_cache"], s["t_out"], "assumed")
            m["lo_run", a] = low / s["t_n"]
            m["lo_pass", a] = div(low, s["t_pass"])
    for a in arms:
        if a == REF:
            continue
        if ("in_pass", a) in m:
            m["in_pass_x", a] = div(m["in_pass", a], m.get(("in_pass", REF)))
        if ("rec_pass", a) in m:
            m["rec_run_x", a] = div(m["rec_run", REF], m["rec_run", a])
            m["rec_pass_x", a] = div(m["rec_pass", REF], m["rec_pass", a])
        if ("nc_pass", a) in m:
            m["nc_pass_x", a] = div(m["nc_pass", REF], m["nc_pass", a])
        if ("lo_pass", a) in m:
            m["lo_pass_x", a] = div(m["rec_pass", REF], m["lo_pass", a])
        if a not in PRICES:
            continue
        # tasks both models pass every time: per run and per pass are the same thing there
        both = {t for t in set(draw) if every(agg, REF, t) and every(agg, a, t)}
        bt = totals(agg, [REF, a], draw, keep=both)
        m["bp_tasks", a] = float(sum(t in both for t in draw))
        r0, r1 = bt[REF], bt[a]
        if r0["t_n"] and r1["t_n"]:
            n0 = priced(REF, r0["t_in"], r0["t_cache"], r0["t_out"], "none") / r0["t_n"]
            n1 = priced(a, r1["t_in"], r1["t_cache"], r1["t_out"], "none") / r1["t_n"]
            m["bp_nc_ref", a], m["bp_nc", a], m["bp_nc_x", a] = n0, n1, div(n0, n1)
        if r0["c_n"] and r1["c_n"]:
            c0, c1 = r0["c_sum"] / r0["c_n"], r1["c_sum"] / r1["c_n"]
            m["bp_rec_ref", a], m["bp_rec", a], m["bp_rec_x", a] = c0, c1, div(c0, c1)
    return m


def intervals(agg, axis, tasks):
    rng = random.Random(SEED)
    n = len(tasks)
    samples, undefined = defaultdict(list), Counter()
    for _ in range(RESAMPLES):
        draw = [tasks[rng.randrange(n)] for _ in range(n)]
        got = measure(agg, axis, draw)
        for key in got:
            if got[key] is None:
                undefined[key] += 1
            else:
                samples[key].append(got[key])
    return ({k: (pctl(xs, 0.05), pctl(xs, 0.95)) for k, xs in samples.items()}, undefined)


def fmt(key, x):
    if x is None:
        return "-"
    kind = key[0]
    if kind.endswith("_x"):
        return f"{x:.1f}x"
    if kind == "in_pass":
        return f"{x:.2f}M"
    if kind == "bp_tasks":
        return f"{x:.0f}"
    return f"${x:.3f}"


def table(m, ci, undefined, rows):
    say(f"{'measure':<52} {'arm':<16} {'point':<10} 90% interval")
    for label, kind, arms in rows:
        for a in arms:
            key = (kind, a)
            if key not in m:
                continue
            rng_txt = f"{fmt(key, ci[key][0])} to {fmt(key, ci[key][1])}" if key in ci else "-"
            note = f"   (undefined in {undefined[key]} resamples)" if undefined[key] else ""
            say(f"{label:<52} {a:<16} {fmt(key, m[key]):<10} {rng_txt}{note}")


def as_report(m, axis):
    arms = AXES[axis]["arms"]
    col = lambda kind, spec: [format(m[kind, a], spec) if m.get((kind, a)) is not None else "-"
                              for a in arms]
    got = {"dollars per pass": col("rec_pass", ".2f")}
    if axis == "model":
        got["input per pass (M)"] = col("in_pass", ".2f")
        got["output per pass (k)"] = col("out_pass", ".0f")
    return got


def method_check(trials):
    say()
    say("-- method check: do tokens x list price reproduce the recorded dollars? (all 89 tasks)")
    say("   'reads at cache price' = recorded cached tokens at the cache price, the rest of the")
    say("   input at the full input price. For Opus the rest may include cache writes, which")
    say("   cost more, so a second figure prices the rest at the cache-write price.")
    for a in (REF, "DeepSeek-V3.2"):
        ts = [t for t in trials if t["arm"] == a and t["cost"] is not None and t["in"]]
        if not ts:
            say(f"   {a}: no trial has both dollars and tokens")
            continue
        rec = sum(t["cost"] for t in ts)
        calc = {how: sum(priced(a, t["in"], t["cache"] or 0, t["out"] or 0, how) for t in ts)
                for how in ("recorded", "none") + (("write",) if "write" in PRICES[a] else ())}
        per = [t["cost"] / priced(a, t["in"], t["cache"] or 0, t["out"] or 0, "recorded")
               for t in ts if t["cost"] > 0]
        cached = sum(t["cache"] or 0 for t in ts) / sum(t["in"] for t in ts)
        say(f"   {a}: {len(ts)} trials, cached share of input {100 * cached:.0f}%")
        say(f"      recorded ${rec:,.2f}; reads at cache price ${calc['recorded']:,.2f} "
            f"(recorded / this = {rec / calc['recorded']:.2f})"
            + (f"; rest at write price ${calc['write']:,.2f} "
               f"(recorded / this = {rec / calc['write']:.2f})" if "write" in calc else ""))
        say(f"      no caching at all ${calc['none']:,.2f} (recorded / this = "
            f"{rec / calc['none']:.2f})")
        if per:
            say(f"      per trial, recorded / 'reads at cache price': median "
                f"{statistics.median(per):.2f}, 5th to 95th percentile "
                f"{pctl(per, 0.05):.2f} to {pctl(per, 0.95):.2f}")


def run_axis(con, axis):
    arms = AXES[axis]["arms"]
    trials = [t for t in load(con, AXES[axis]["table"]) if t["arm"] in arms]
    agg = aggregate(trials, arms)
    scopes = [("primary", sorted({t["task"] for t in trials if not t["revised"]})),
              ("all", sorted({t["task"] for t in trials}))]
    say()
    say(f"== {axis} axis " + "=" * 60)
    say("recorded per arm (all 89 tasks): trials, with dollars, with tokens, run dates")
    for a in arms:
        ts = [t for t in trials if t["arm"] == a]
        days = sorted(t["started"][:10] for t in ts if t["started"])
        say(f"   {a:<16} {len(ts):>4} {sum(t['cost'] is not None for t in ts):>5} "
            f"{sum(bool(t['in']) for t in ts):>5}   "
            + (f"{days[0]} to {days[-1]}" if days else "no dates"))
    points = {}
    for scope, tasks in scopes:
        points[scope] = measure(agg, axis, tasks)
        got = as_report(points[scope], axis)
        if got != EXPECT[axis, scope]:
            say(f"!! {scope} scope does not match the confirmation report")
            for k, want in EXPECT[axis, scope].items():
                if got.get(k) != want:
                    say(f"   {k}: report {want}")
                    say(f"   {k}: here   {got.get(k)}")
            raise SystemExit("stopped: recorded values differ from the report")
    say("recorded dollars per pass" + (" and provider tokens per pass" if axis == "model" else "")
        + " match the confirmation report on both scopes")
    if axis == "model":
        say("list prices used, dollars per million tokens:")
        for a, p in PRICES.items():
            say(f"   {a:<16} input {p['in']}, cached input {p['cache']}, output {p['out']}"
                + (f", cache write {p['write']}" if "write" in p else "") + f"   [{p['src']}]")
        method_check(trials)

    priced_arms = [a for a in arms if a in PRICES]
    others = [a for a in priced_arms if a != REF]
    for scope, tasks in scopes:
        m = points[scope]
        ci, undefined = intervals(agg, axis, tasks)
        say()
        say(f"-- {axis} axis, {'PRIMARY scope' if scope == 'primary' else 'all tasks (alongside)'}"
            f": {len(tasks)} tasks; {RESAMPLES:,} resamples of tasks, seed {SEED}")
        if axis == "harness":
            table(m, ci, undefined, [
                ("RECORDED dollars per run", "rec_run", arms),
                ("RECORDED dollars per pass", "rec_pass", arms),
                ("  per pass, times Terminus2's", "rec_pass_x", arms)])
            continue
        table(m, ci, undefined, [
            ("RECORDED dollars per run", "rec_run", arms),
            ("RECORDED dollars per pass", "rec_pass", arms),
            ("  Opus per run / this model per run", "rec_run_x", others),
            ("  Opus per pass / this model per pass", "rec_pass_x", others),
            ("provider input per pass (tokens)", "in_pass", arms),
            ("  this model / Opus", "in_pass_x", others),
            ("ESTIMATE, no caching: dollars per run", "nc_run", priced_arms),
            ("ESTIMATE, no caching: dollars per pass", "nc_pass", priced_arms),
            ("  Opus per pass / this model per pass", "nc_pass_x", others),
            (f"ESTIMATE, {ASSUMED_CACHED:.0%} cached: dollars per run", "lo_run", ESTIMATED),
            (f"ESTIMATE, {ASSUMED_CACHED:.0%} cached: dollars per pass", "lo_pass", ESTIMATED),
            ("  Opus RECORDED per pass / this estimate", "lo_pass_x", ESTIMATED)])
        say("on the tasks both Opus and the model pass every time (same tasks, every run a pass):")
        table(m, ci, undefined, [
            ("tasks", "bp_tasks", others),
            ("RECORDED dollars per run, Opus", "bp_rec_ref", others),
            ("RECORDED dollars per run, this model", "bp_rec", others),
            ("  Opus / this model", "bp_rec_x", others),
            ("ESTIMATE, no caching: per run, Opus", "bp_nc_ref", others),
            ("ESTIMATE, no caching: per run, this model", "bp_nc", others),
            ("  Opus / this model", "bp_nc_x", others)])


def main():
    import duckdb
    if not DB.exists():
        raise SystemExit(f"not found: {DB.relative_to(REPO)}; run the importer on both axes first")
    con = duckdb.connect(str(DB), read_only=True)
    say(f"Note two: dollars, recorded and estimated, {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    say(f"source {DB.name}; 90% interval = 5th to 95th percentile over {RESAMPLES:,} resamples of "
        f"tasks (with replacement, seed {SEED})")
    say("RECORDED = dollars in the leaderboard logs. ESTIMATE = recorded tokens x the maker's list")
    say("price; not what the submitter paid. GLM-5 has no token counts and is left out.")
    for axis in ("model", "harness"):
        run_axis(con, axis)
    con.close()
    OUT.mkdir(exist_ok=True)
    report = OUT / "dollars.txt"
    report.write_text("\n".join(lines_out) + "\n")
    print(f"\nreport: {report.relative_to(REPO)}")


if __name__ == "__main__":
    main()