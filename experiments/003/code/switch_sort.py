# experiments/003/code/switch_sort.py   (reads the job folders and model_audit's output; writes one JSONL)
# Sorts the runs model_audit.py flagged as switched into three kinds:
#   harness   only "<synthetic>" replies besides the asked-for model: messages Claude Code writes
#             itself (error notices), not another model
#   fallback  another model took over and a fallback event is recorded
#   silent    another model took over with no fallback event recorded
# Shows examples of each, the order of models in runs that reach claude-opus-4-8, and recomputes
# the comparisons with only real switches (fallback and silent) set apart.
# v2 (29 Oct, for the freeze): the audit file, the output file and the equal cap are arguments;
# their defaults are the Fable run's, so with them the output is unchanged. The check against the
# 28 Oct numbers runs only on the two Fable jobs.
#   python experiments/003/code/switch_sort.py ~/crucible/harbor/jobs/tb4-fable-5 ~/crucible/harbor/jobs/tb4-fable-5-1
#   python experiments/003/code/switch_sort.py JOB_BEFORE JOB_AFTER --audit ~/crucible/harbor/out/model_audit_003_opus.jsonl \
#          --out ~/crucible/harbor/out/switch_sort_003_opus.jsonl --equal-cap CAP
# Standard library only. Safe to run twice: the output file is rebuilt each time.
import argparse
import json
import sys
from collections import Counter, defaultdict
from itertools import groupby
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("jobs", nargs="+")
ap.add_argument("--audit", default="~/crucible/harbor/out/model_audit_003.jsonl")
ap.add_argument("--out", default="~/crucible/harbor/out/switch_sort_003.jsonl")
ap.add_argument("--equal-cap", type=int, default=64000)
args = ap.parse_args()
AUDIT = Path(args.audit).expanduser()
OUT = Path(args.out).expanduser()
CAP = args.equal_cap
FABLE = ["tb4-fable-5", "tb4-fable-5-1"]
SYN = "<synthetic>"
short = lambda m: str(m or "?").split("/")[-1]


def load(p):
    return json.loads(Path(p).read_text(errors="replace"))


def text_of(msg):
    if isinstance(msg, str):
        return msg
    if isinstance(msg, list):
        return " ".join(str(x.get("text") or "") if isinstance(x, dict) else str(x) for x in msg)
    return str(msg or "")


def runs_of(seq):
    return " > ".join(f"{k} x{len(list(g))}" for k, g in groupby(seq))


fails = 0
def check(ok, what):
    global fails
    fails += not ok
    print(f"   {'ok  ' if ok else 'FAIL'} {what}")


audit = [json.loads(l) for l in AUDIT.read_text().splitlines() if l.strip()]
by_key = {(x["job"], x["trial"]): x for x in audit}
rows = []
for job in args.jobs:
    job = Path(job).expanduser()
    for x in [x for x in audit if x["job"] == job.name and x["switched"]]:
        tr = load(job / x["trial"] / "agent" / "trajectory.json")
        seq, syn_texts, fb_models = [], [], []
        for s in tr.get("steps") or []:
            if s.get("source") != "agent" or (s.get("extra") or {}).get("is_sidechain"):
                continue
            mo = short(s.get("model_name"))
            seq.append(mo)
            if mo == SYN:
                syn_texts.append(text_of(s.get("message")).strip().replace("\n", " ")[:140])
            for it in ((s.get("metrics") or {}).get("extra") or {}).get("iterations") or []:
                if it.get("type") == "fallback_message":
                    fb_models.append(short(it.get("model")))
        others = {m for m in seq if m not in (x["want"], SYN)}
        kind = "harness" if not others else ("fallback" if x["fallback_event"] else "silent")
        rows.append({**x, "kind": kind, "sequence": runs_of(seq), "synthetic_texts": syn_texts[:3],
                     "synthetic_last": bool(seq) and seq[-1] == SYN and seq.count(SYN) == 1,
                     "fallback_models": fb_models})

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w") as f:
    for r in rows:
        f.write(json.dumps(r, sort_keys=True) + "\n")

for job in [Path(j).expanduser().name for j in args.jobs]:
    rs = [r for r in rows if r["job"] == job]
    k = Counter(r["kind"] for r in rs)
    print(f"== {job}: {len(rs)} flagged as switched: harness {k['harness']}, fallback {k['fallback']}, silent {k['silent']}")
    h = [r for r in rs if r["kind"] == "harness"]
    if h:
        print(f"   harness: endings {dict(Counter(r['ending'] for r in h))}; "
              f"the harness message is the last reply in {sum(r['synthetic_last'] for r in h)} of {len(h)}")
        for t, n in Counter(r["synthetic_texts"][0][:90] for r in h if r["synthetic_texts"]).most_common(4):
            print(f"      x{n}  {t}")
    fb = [r for r in rs if r["kind"] == "fallback"]
    if fb:
        print(f"   fallback: event model {dict(Counter(m for r in fb for m in r['fallback_models']))}; "
              f"models after the switch {dict(Counter(m for r in fb for m in r['reply_models'] if m != r['want']))}")
    for r in [r for r in rs if r["kind"] == "silent"][:4]:
        print(f"   silent  {r['trial']}: {r['sequence']}")
    for r in [r for r in rs if "claude-opus-4-8" in r["reply_models"]][:4]:
        print(f"   4-8     {r['trial']} ({r['kind']}): {r['sequence']}; fallback events {r['fallback_models']}")
    check(k["fallback"] + k["silent"] + k["harness"] == len(rs), "every flagged run sorted")
    real = k["fallback"] + k["silent"]
    print(f"   real switches: {real} of {sum(1 for x in audit if x['job'] == job)} runs; "
          f"passed {sum(r['passed'] for r in rs if r['kind'] != 'harness' and not r['api_error'])} of "
          f"{sum(1 for r in rs if r['kind'] != 'harness' and not r['api_error'])}")
    print()

# comparisons with only real switches set apart (harness-message runs stay in, by their endings)
real_switch = {(r["job"], r["trial"]) for r in rows if r["kind"] != "harness"}
names = [Path(j).expanduser().name for j in args.jobs]


def groups(job, cap_only, drop_switched):
    g = defaultdict(list)
    for x in audit:
        if x["job"] != job or x["api_error"] or (cap_only and x["cap"] != CAP):
            continue
        if drop_switched and (job, x["trial"]) in real_switch:
            continue
        g[x["task"]].append(x["passed"])
    return g


def tw(g, tasks):
    rates = [sum(g[t]) / len(g[t]) for t in tasks]
    return 100 * sum(rates) / max(len(rates), 1)


if len(names) == 2:
    for label, cap_only in ((f"equal cap ({CAP // 1000}k)", True), ("as run (any cap)", False)):
        for drop in (False, True):
            ga, gb = groups(names[0], cap_only, drop), groups(names[1], cap_only, drop)
            c = sorted(set(ga) & set(gb))
            print(f"{label}, task-weighted, {'real switches set apart' if drop else 'all trials'}: "
                  f"{len(c)} tasks: {names[0]} {tw(ga, c):.0f}%, {names[1]} {tw(gb, c):.0f}%")
    ga, gb = groups(names[0], True, False), groups(names[1], True, False)
    c = sorted(set(ga) & set(gb))
    if names == FABLE:
        check(len(c) == 55 and round(tw(ga, c)) == 44 and round(tw(gb, c)) == 57, "reproduces 28 Oct (55 tasks, 44% and 57%)")

print()
print(f"   wrote {OUT} ({len(rows)} runs)")
print("PASS: switches sorted" if not fails else f"FAIL: {fails} check(s) failed; read the lines above")
sys.exit(1 if fails else 0)