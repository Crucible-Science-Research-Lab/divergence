# experiments/003/code/model_audit.py   (reads the job folders; writes one JSONL file)
# Which model actually wrote each run's replies. For every trial: the model the job asked for,
# the model named on every main-agent reply and every sub-agent reply (trajectory.json's
# model_name, with extra.is_sidechain), where the first switch to another model happened, how
# much of the run came after it, and the sub-agents' own transcripts. Then pass rates with
# switched runs set apart, including the 28 Oct equal-cap comparison.
# v2 (29 Oct, for the freeze): the output file and the equal cap are arguments; their defaults are
# the Fable run's, so with them the output is unchanged. The checks against the 28 Oct numbers and
# the lead exhibit run only on the two Fable jobs.
#   python experiments/003/code/model_audit.py ~/crucible/harbor/jobs/tb4-fable-5 ~/crucible/harbor/jobs/tb4-fable-5-1
#   python experiments/003/code/model_audit.py JOB_BEFORE JOB_AFTER --out ~/crucible/harbor/out/model_audit_003_opus.jsonl --equal-cap CAP
# Standard library only. Safe to run twice: the output file is rebuilt each time.
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

ap = argparse.ArgumentParser()
ap.add_argument("jobs", nargs="+")
ap.add_argument("--out", default="~/crucible/harbor/out/model_audit_003.jsonl")
ap.add_argument("--equal-cap", type=int, default=64000)
args = ap.parse_args()
OUT = Path(args.out).expanduser()
CAP = args.equal_cap
FABLE = ["tb4-fable-5", "tb4-fable-5-1"]
LEAD = "embedding-drift-monitor"
short = lambda m: str(m or "?").split("/")[-1]


def load(p):
    return json.loads(Path(p).read_text(errors="replace"))


def audit(tdir):
    r, cfg = load(tdir / "result.json"), load(tdir / "config.json")
    agent = cfg.get("agent") or {}
    want = short(agent.get("model_name"))
    env = (agent.get("env") or {}).get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
    exc = r.get("exception_info")
    etype = (exc.get("exception_type") if isinstance(exc, dict) else str(exc)) if exc else ""
    reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    main, side, fb_event = [], [], False
    tp = tdir / "agent" / "trajectory.json"
    if tp.exists():
        for s in load(tp).get("steps") or []:
            if s.get("source") != "agent":
                continue
            m = s.get("metrics") or {}
            its = (m.get("extra") or {}).get("iterations") or []
            fb_event |= any(it.get("type") == "fallback_message" for it in its)
            row = (short(s.get("model_name")), int(m.get("completion_tokens") or 0))
            (side if (s.get("extra") or {}).get("is_sidechain") else main).append(row)
    # sub-agent transcripts: one entry per assistant message id
    sub_models, sub_launch_types = Counter(), Counter()
    for f in tdir.glob("agent/sessions/projects/**/subagents/*.jsonl"):
        seen = set()
        for line in f.read_text(errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg = d.get("message") or {}
            if d.get("type") == "assistant" and msg.get("id") and msg["id"] not in seen:
                seen.add(msg["id"])
                sub_models[short(msg.get("model"))] += 1
        meta = f.with_suffix(".meta.json")
        if meta.exists():
            sub_launch_types[load(meta).get("agentType") or "?"] += 1
    first_other = next((i for i, (mo, _) in enumerate(main) if mo != want), None)
    after = main[first_other:] if first_other is not None else []
    tok = sum(t for _, t in main) or 1
    return {
        "trial": tdir.name, "task": r["task_name"].split("/")[-1], "want": want,
        "cap": int(env) if env else 64000, "api_error": "api" in etype.lower(), "ending": etype or "completed",
        "passed": reward is not None and float(reward) >= 1.0,
        "replies": len(main), "reply_models": dict(Counter(mo for mo, _ in main)),
        "first_switch_reply": None if first_other is None else first_other + 1,
        "switched": first_other is not None,
        "back_to_want_after_switch": any(mo == want for mo, _ in after),
        "share_replies_other": round(sum(mo != want for mo, _ in main) / max(len(main), 1), 4),
        "share_output_other": round(sum(t for mo, t in main if mo != want) / tok, 4),
        "fallback_event": fb_event,
        "side_models": dict(Counter(mo for mo, _ in side)),
        "subagents": sum(sub_launch_types.values()), "subagent_types": dict(sub_launch_types),
        "subagent_reply_models": dict(sub_models),
    }


def pct(x):
    return f"{100 * x:.0f}%"


def task_weighted(groups, tasks):
    """mean over tasks of each task's pass rate; groups: {task: [passed, ...]}"""
    rates = [sum(groups[t]) / len(groups[t]) for t in tasks if groups.get(t)]
    return sum(rates) / max(len(rates), 1)


fails = 0
def check(ok, what):
    global fails
    fails += not ok
    print(f"   {'ok  ' if ok else 'FAIL'} {what}")

jobs = {}
OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w") as out:
    for job in args.jobs:
        job = Path(job).expanduser()
        rows = []
        for rp in sorted(job.glob("*/result.json")):
            if not load(rp).get("trial_name"):
                continue
            x = audit(rp.parent)
            x["job"] = job.name
            rows.append(x)
            out.write(json.dumps(x, sort_keys=True) + "\n")
        jobs[job.name] = rows

for name, rows in jobs.items():
    sw = [x for x in rows if x["switched"]]
    print(f"== {name}: {len(rows)} trials, asked for {Counter(x['want'] for x in rows).most_common()}")
    print(f"   switched to another model on the main agent: {len(sw)} trials"
          + (f"; to {dict(Counter(m for x in sw for m in x['reply_models'] if m != x['want']))}" if sw else ""))
    if sw:
        print(f"   first switch at reply: median {median(x['first_switch_reply'] for x in sw)}, "
              f"first reply in {sum(x['first_switch_reply'] == 1 for x in sw)}; "
              f"share of replies by the other model: median {pct(median(x['share_replies_other'] for x in sw))}, "
              f"share of output tokens: median {pct(median(x['share_output_other'] for x in sw))}")
        print(f"   back to the asked-for model after a switch: {sum(x['back_to_want_after_switch'] for x in sw)} trials")
        print(f"   passed: switched {sum(x['passed'] for x in sw)} of {len(sw)}, "
              f"not switched {sum(x['passed'] for x in rows if not x['switched'] and not x['api_error'])} of "
              f"{sum(not x['switched'] and not x['api_error'] for x in rows)} (API errors left out)")
        top = Counter(x["task"] for x in sw).most_common(12)
        print("   tasks with switched trials: " + ", ".join(f"{t} {n}" for t, n in top)
              + (f" (+{len(set(x['task'] for x in sw)) - 12} more)" if len(set(x['task'] for x in sw)) > 12 else ""))
    fe = [x for x in rows if x["fallback_event"]]
    check(set(x["trial"] for x in fe) == set(x["trial"] for x in sw),
          f"switches and fallback events are the same trials ({len(sw)} and {len(fe)})")
    subs = [x for x in rows if x["subagents"]]
    print(f"   sub-agents: {len(subs)} trials, {sum(x['subagents'] for x in subs)} launches, types "
          f"{dict(sum((Counter(x['subagent_types']) for x in subs), Counter()))}; their replies by model "
          f"{dict(sum((Counter(x['subagent_reply_models']) for x in subs), Counter()))}; "
          f"in switched trials {sum(x['switched'] for x in subs)}")
    lead = [x for x in rows if x["task"] == LEAD]
    if lead and name in FABLE:
        check(all(not x["switched"] and set(x["side_models"]) <= {x["want"]} and set(x["subagent_reply_models"]) <= {x["want"]} for x in lead),
              f"{LEAD}: every reply by {lead[0]['want']} ({len(lead)} trials)")
    elif not lead:
        print(f"   ({LEAD} not in this job)")
    print()

# the 28 Oct equal-cap comparison, then with switched trials set apart
names = list(jobs)
if len(names) == 2:
    a, b = jobs[names[0]], jobs[names[1]]
    def groups(rows, keep):
        g = defaultdict(list)
        for x in rows:
            if x["cap"] == CAP and not x["api_error"] and keep(x):
                g[x["task"]].append(x["passed"])
        return g
    ga_all, gb_all = groups(a, lambda x: True), groups(b, lambda x: True)
    common = sorted(set(ga_all) & set(gb_all))
    wa, wb = task_weighted(ga_all, common), task_weighted(gb_all, common)
    print(f"equal cap ({CAP // 1000}k), task-weighted, {len(common)} tasks, all trials: {names[0]} {pct(wa)}, {names[1]} {pct(wb)}")
    if names == FABLE:
        check(len(common) == 55 and round(100 * wa) == 44 and round(100 * wb) == 57, "reproduces 28 Oct (55 tasks, 44% and 57%)")
    ga, gb = groups(a, lambda x: not x["switched"]), groups(b, lambda x: not x["switched"])
    common2 = sorted(set(ga) & set(gb))
    print(f"equal cap ({CAP // 1000}k), task-weighted, switched trials set apart: {len(common2)} tasks: "
          f"{names[0]} {pct(task_weighted(ga, common2))}, {names[1]} {pct(task_weighted(gb, common2))}")
    def all_caps(rows, keep):
        g = defaultdict(list)
        for x in rows:
            if not x["api_error"] and keep(x):
                g[x["task"]].append(x["passed"])
        return g
    for label, keep in (("all trials", lambda x: True), ("switched set apart", lambda x: not x["switched"])):
        g1, g2 = all_caps(a, keep), all_caps(b, keep)
        c = sorted(set(g1) & set(g2))
        print(f"as run (any cap), task-weighted, {label}: {len(c)} tasks: "
              f"{names[0]} {pct(task_weighted(g1, c))}, {names[1]} {pct(task_weighted(g2, c))}")

print()
print(f"   wrote {OUT} ({sum(len(r) for r in jobs.values())} trials)")
print("PASS: model audit done" if not fails else f"FAIL: {fails} check(s) failed; read the lines above")
sys.exit(1 if fails else 0)