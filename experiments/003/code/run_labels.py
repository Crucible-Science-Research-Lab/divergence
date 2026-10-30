# ~/crucible/divergence/scratch/run_labels.py   (reads the job folders; writes one JSON file)
# Labels every trial with its output cap, its longest reply, any fallback and how it ended,
# using the same reads as ~/crucible/harbor/cap_groups.py and reply_audit.py (28 Oct).
# v2: with --analysis, also files each label under the viewer's run id, joined through the
# source trial recorded inside each run file (never by position), plus a summary per cell.
# The committed importer is not touched. Safe to run twice: the output is rebuilt each time.
#   python scratch/run_labels.py ~/crucible/harbor/jobs/tb4-fable-5 ~/crucible/harbor/jobs/tb4-fable-5-1 \
#          --out experiments/003-fable/run_labels.json --analysis experiments/003-fable/analysis.json \
#          --task embedding-drift-monitor
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_CAP = 64000          # Claude Code's default when CLAUDE_CODE_MAX_OUTPUT_TOKENS is not set

# Yesterday's numbers (STAGED_28OCT.md, E), checked again here; only for these two job names.
EXPECT = {
    "tb4-fable-5":   {"trials": 330, "caps": {64000: 330}, "fallback_trials": 61, "over_at_128k": 0},
    "tb4-fable-5-1": {"trials": 330, "caps": {64000: 125, 128000: 205}, "fallback_trials": 44, "over_at_128k": 56},
}


def load(p):
    return json.loads(p.read_text(errors="replace"))


def label_trial(tdir):
    r = load(tdir / "result.json")
    cfg = load(tdir / "config.json")
    agent = cfg.get("agent") or {}
    env = (agent.get("env") or {}).get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
    exc = r.get("exception_info")
    etype = (exc.get("exception_type") if isinstance(exc, dict) else str(exc)) if exc else ""
    reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    replies = largest = fb_replies = fb_out = 0
    fb_models = Counter()
    tp = tdir / "agent" / "trajectory.json"
    if tp.exists():
        for s in load(tp).get("steps") or []:
            if s.get("source") != "agent":
                continue
            m = s.get("metrics") or {}
            replies += 1
            largest = max(largest, int(m.get("completion_tokens") or 0))
            fb = [it for it in (m.get("extra") or {}).get("iterations") or [] if it.get("type") == "fallback_message"]
            if fb:
                fb_replies += 1
                for it in fb:
                    fb_models[it.get("model") or "?"] += 1
                    fb_out += int(it.get("output_tokens") or 0)
    cap = int(env) if env else DEFAULT_CAP
    return {
        "task": r["task_name"].split("/")[-1],
        "model": agent.get("model_name"),
        "cap": cap,
        "cap_source": "env" if env else "default",
        "largest_reply": largest,
        "over_default_cap": largest > DEFAULT_CAP,
        "replies": replies,
        "fallback_replies": fb_replies,
        "fallback_models": sorted(fb_models),
        "fallback_output_tokens": fb_out,
        "ending": etype or "completed",
        "api_error": "api" in etype.lower(),
        "passed": reward is not None and float(reward) >= 1.0,
        "has_trajectory": tp.exists(),
    }


ap = argparse.ArgumentParser()
ap.add_argument("jobs", nargs="+")
ap.add_argument("--out", required=True)
ap.add_argument("--task", default="embedding-drift-monitor")
ap.add_argument("--analysis", help="the experiment's analysis.json; run files are read beside it")
a = ap.parse_args()

fails = 0
def check(ok, what):
    global fails
    fails += not ok
    print(f"   {'ok  ' if ok else 'FAIL'} {what}")

labels = {"version": 2, "default_cap": DEFAULT_CAP, "jobs": {}}
for job in a.jobs:
    job = Path(job).expanduser()
    runs = {}
    for rp in sorted(job.glob("*/result.json")):
        if not load(rp).get("trial_name"):
            continue
        runs[rp.parent.name] = label_trial(rp.parent)
    labels["jobs"][job.name] = runs
    v = list(runs.values())
    caps = Counter(x["cap"] for x in v)
    fbt = sum(x["fallback_replies"] > 0 for x in v)
    over128 = sum(x["over_default_cap"] for x in v if x["cap"] == 128000 and not x["api_error"])
    print(f"== {job.name}: {len(v)} trials; caps " + ", ".join(f"{c // 1000}k {n}" for c, n in sorted(caps.items()))
          + f"; trials with a fallback {fbt}; at 128k with a reply over 64k {over128}; API errors {sum(x['api_error'] for x in v)}")
    check(all(x["has_trajectory"] for x in v), "every trial has a trajectory")
    e = EXPECT.get(job.name)
    if e is None:
        print("   (no saved numbers for this job name; checks above only)")
        continue
    check(len(v) == e["trials"], f"trials {len(v)} (expected {e['trials']})")
    check(dict(caps) == e["caps"], f"caps match 28 Oct ({dict(caps)})")
    check(fbt == e["fallback_trials"], f"fallback trials {fbt} (expected {e['fallback_trials']})")
    check(over128 == e["over_at_128k"], f"128k trials over 64k {over128} (expected {e['over_at_128k']})")

print()
print(f"{a.task}:")
print(f"   {'job':<14} {'trial':<34} {'cap':>5} {'largest':>8} {'fallback':>8} {'ending':<22} passed")
for jname, runs in labels["jobs"].items():
    for tname, x in runs.items():
        if x["task"] == a.task:
            print(f"   {jname.replace('tb4-', ''):<14} {tname[:34]:<34} {x['cap'] // 1000:>4}k {x['largest_reply']:>8,} "
                  f"{x['fallback_replies']:>8} {x['ending'][:22]:<22} {'yes' if x['passed'] else 'no'}")

def source_of(run_file):
    """The trial and submission the importer recorded in a run file's attributes."""
    found = {}
    def walk(o):
        if isinstance(o, dict):
            k = o.get("key")
            if isinstance(k, str) and isinstance(o.get("value"), dict):
                # the key seen on 29 Oct ends in "_trial" (prefix cut off in the paste);
                # model and task are cross-checked below, so a wrong pick shows as FAIL
                if k.endswith("_trial"):
                    found.setdefault("source_trial", o["value"].get("stringValue"))
                elif k.endswith("source_submission"):
                    found.setdefault("source_submission", o["value"].get("stringValue"))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(load(run_file))
    return found.get("source_trial"), found.get("source_submission")


if a.analysis:
    an_path = Path(a.analysis)
    an = load(an_path)
    by_trial = {}
    for jname, runs in labels["jobs"].items():
        for tname, x in runs.items():
            by_trial.setdefault(tname, []).append((jname, x))
    by_run, by_cell = {}, {}
    missing, model_bad, task_bad, agree, total = [], [], [], 0, 0
    print()
    print(f"== joining labels to {an_path} through each run file's source trial")
    for c in an["cells"]:
        rows = []
        for r in c["runs"]:
            total += 1
            rf = an_path.parent / r["trace_path"]
            trial, sub = source_of(rf) if rf.exists() else (None, None)
            cands = [(j, x) for j, x in by_trial.get(trial, []) if sub is None or j == sub] or by_trial.get(trial, [])
            if len(cands) != 1:
                missing.append(r["id"])
                continue
            jname, x = cands[0]
            if str(x["model"]).split("/")[-1] != str(r["model"]).split("/")[-1]:
                model_bad.append(r["id"])
            if x["task"] != r["task"]:
                task_bad.append(r["id"])
            agree += max(r.get("out_turn") or [0]) == x["largest_reply"]
            by_run[r["id"]] = {"trial": trial, "job": jname, **{k: x[k] for k in (
                "cap", "cap_source", "largest_reply", "over_default_cap", "fallback_replies",
                "fallback_models", "fallback_output_tokens", "ending", "api_error")}}
            rows.append(by_run[r["id"]])
        if rows:
            by_cell[c["cell_id"]] = {
                "caps": {str(k): v for k, v in sorted(Counter(x["cap"] for x in rows).items())},
                "largest_reply": max(x["largest_reply"] for x in rows),
                "runs_over_default_cap": sum(x["over_default_cap"] for x in rows),
                "runs_with_fallback": sum(x["fallback_replies"] > 0 for x in rows),
                "api_errors": sum(x["api_error"] for x in rows),
            }
    labels["by_run"], labels["by_cell"] = by_run, by_cell
    check(not missing, f"every run found its trial ({total - len(missing)} of {total})" + (f"; missing e.g. {missing[:3]}" if missing else ""))
    check(not model_bad, "model agrees on every run" + (f"; not on {model_bad[:3]}" if model_bad else ""))
    check(not task_bad, "task agrees on every run" + (f"; not on {task_bad[:3]}" if task_bad else ""))
    print(f"   info: largest reply equals the tree's own largest out-turn on {agree} of {total - len(missing)} runs"
          " (sub-agent replies can make these differ)")
    print()
    print(f"{a.task} by run id:")
    for rid, x in by_run.items():
        if rid.startswith(a.task + "__"):
            print(f"   {rid:<48} {x['trial']:<36} {x['cap'] // 1000:>4}k {x['largest_reply']:>8,} fb {x['fallback_replies']}")
    for cid, s in by_cell.items():
        if cid.startswith(a.task + "__"):
            print(f"   {cid:<48} caps {s['caps']} largest {s['largest_reply']:,} over-64k {s['runs_over_default_cap']} fallback {s['runs_with_fallback']}")

out = Path(a.out)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(labels, indent=1, sort_keys=True) + "\n")
n = sum(len(r) for r in labels["jobs"].values())
print()
print(f"   wrote {out} ({n} trials" + (f", {len(labels['by_run'])} runs" if "by_run" in labels else "") + ")")
print("PASS: run labels written and checked" if not fails else f"FAIL: {fails} check(s) failed; the file was written, read the lines above")
sys.exit(1 if fails else 0)