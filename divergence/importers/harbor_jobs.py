# divergence/importers/harbor_jobs.py
"""
Importer: one Harbor job folder (Claude Code trials) -> divergence runs (OTLP-JSON, one
file per trial), with the version-2 step vocabulary and sub-agent branches.

Run from the repository root:
    python divergence/importers/harbor_jobs.py ~/crucible/harbor/jobs/tb4-fable-5-1 \
        --out experiments/003/out/tb4-fable-5-1 --clean

Reads   <job>/<trial>/config.json, result.json, agent/trajectory.json, and the sub-agent
        transcripts agent/sessions/projects/*/*/subagents/agent-*.jsonl (+ .meta.json)
Writes  <out>/runs/<task>/<run_id>.json   one run per trial, same span shapes as note two
        <out>/manifest.json               importer and harbor.py hashes, vocabulary, counts
        <out>/import_report.txt           the report (also printed)

Vocabulary, version 2 (decided 28 Oct 2026): inspect, edit, run, verify, install, fetch,
delete, delegate, done; plan and control are recorded but left out of the key.
  verify    a call v1 labels "run" whose program is a test runner (pytest, python -m
            pytest or unittest, npm/yarn/pnpm/bun test, cargo test, go test, make with a
            test or check target) or a script named test, check, verify or validate.
            Writing a check script stays edit; reading tests stays inspect.
  delegate  Agent, Task, SendMessage.   control  ListAgents, TaskStop, TaskOutput.
  plan      ToolSearch (and v1's plan tools).
Programs v1 does not know, added (0.1.1): run lake, sbt, verilator, yosys, sage, freecadcmd,
tesseract, ncu, npx, ruff, supervisorctl, vncdo; inspect paste, comm, getent; no-op shift;
setsid is read as a wrapper and "<(" as the start of the command inside it. Shell functions
the agent defines itself (run, p, t ...) stay run, defaulted.
The classifier is note two's (divergence/importers/harbor.py, unchanged on disk). The
version-2 rules are applied by rebinding names inside the imported module, in this
process only; note two's importer and its runs are not affected.

Sub-agents. Claude Code merges a sub-agent's steps into trajectory.json, flagged
extra.is_sidechain but without an agent id. Here the main run is built from the steps
that are not flagged; each branch is built from its own transcript and hung off the
launch call named in its .meta.json (toolUseId). The flagged steps are used only as a
check: their tool-call ids must equal the tool-call ids found in the transcripts.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from divergence.importers import harbor as v1          # noqa: E402  (note two's importer)

IMPORTER_VERSION = "0.1.1"
SOURCE = "harbor-job-folder"
STEP_KEY = "action_category_v2_per_tool_call_collapsed"
V1_SHA256 = "1abf6c31a0275264"                          # first 16 hex of the published file

PRIORITY = ["done", "install", "fetch", "verify", "run", "edit", "delete", "inspect",
            "delegate", "other", "control", "plan"]
EXCLUDED_FROM_KEY = {"plan", "control"}
DELEGATE_TOOLS = {"agent", "task", "sendmessage"}
CONTROL_TOOLS = {"listagents", "taskstop", "taskoutput"}
PLAN_TOOLS = {"toolsearch"}
KNOWN_RUN = {"lake", "sbt", "verilator", "yosys", "sage", "freecadcmd", "tesseract", "ncu",
             "npx", "ruff", "supervisorctl", "vncdo"}    # found in the 28 Oct survey and run
KNOWN_INSPECT = {"paste", "comm", "getent"}              # read-only, unknown to v1
KNOWN_NOOPS = {"shift"}                                  # a shell builtin
CHECK_WORDS = {"test", "tests", "check", "checks", "verify", "verification", "validate",
               "validation"}
SCRIPT_RUNNERS = {"node", "ruby", "rscript", "julia", "perl", "deno"}
BRANCH_ATTRS = ("divergence.trajectory", "divergence.trajectory_raw", "divergence.turns",
                "divergence.tool_calls", "divergence.replies", "divergence.step_key")

lines_out = []


def say(s=""):
    print(s)
    lines_out.append(s)


def check_value(path):
    """sha256 with final newlines stripped (the project's check value for files sent whole)."""
    return hashlib.sha256(Path(path).read_bytes().rstrip(b"\n")).hexdigest()


# ── version-2 rules on top of v1 ─────────────────────────────────────────────

def check_name(name):
    stem = re.sub(r"\.(py|sh|bash|js|mjs|ts|rb|r|jl|pl)$", "", Path(str(name)).name.lower())
    return any(p in CHECK_WORDS for p in re.split(r"[_\-.:]", stem))


def plain_words(seg):
    """A segment's words without redirections, assignments and wrappers (as v1 strips them)."""
    words, skip = [], False
    for t in seg:
        if skip:
            skip = False
            continue
        if t and all(ch in "<>&" for ch in t):
            skip = True
            continue
        words.append(t)
    while words:
        w = words[0]
        if v1.ASSIGN.match(w) or w in v1.WRAPPERS or w == "setsid":
            words = words[1:]
            if w in ("timeout", "nice", "stdbuf", "env", "sudo", "xargs", "runuser", "sshpass",
                     "su", "setsid"):
                while words and (words[0].startswith("-") or v1.DURATION.match(words[0])
                                 or v1.ASSIGN.match(words[0])):
                    takes_arg = words[0] in ("-u", "-p", "-g", "-l", "-s", "-k", "-n", "-I")
                    words = words[2:] if takes_arg else words[1:]
            continue
        break
    return words


def is_verify(seg):
    w = plain_words(seg)
    if not w:
        return False
    prog, rest = w[0].split("/")[-1].lower(), w[1:]
    args = [r for r in rest if not r.startswith("-")]
    if prog in ("pytest", "py.test"):
        return True
    if prog.startswith("python") or prog in ("pypy", "pypy3"):
        if "-m" in rest:
            i = rest.index("-m")
            return i + 1 < len(rest) and rest[i + 1] in ("pytest", "unittest")
        if "-c" in rest:
            return False
        script = next((r for r in rest if r.endswith(".py")), None)
        return bool(script) and check_name(script)
    if prog in ("npm", "yarn", "pnpm", "bun"):
        if not args:
            return False
        if prog == "npm" and args[0] == "t":
            return True
        return check_name(args[1]) if args[0] == "run" and len(args) > 1 else check_name(args[0])
    if prog in ("cargo", "go"):
        return args[:1] == ["test"]
    if prog == "make":
        return any(check_name(a) for a in args if "=" not in a)
    if prog in v1.SHELLS or prog in SCRIPT_RUNNERS:
        if any(r in ("-c", "-lc", "-ic", "-e") for r in rest):
            return False
        return bool(args) and check_name(args[0])
    if "/" in w[0] or prog.endswith((".sh", ".py")):
        return check_name(prog)
    return False


_v1_segment = v1.classify_segment
_v1_call = v1.classify_call


def segment_v2(seg, bodies, depth):
    if seg and seg[0] == "setsid":                      # a wrapper v1 does not know
        w = plain_words(seg)
        seg = w if w else seg
    while seg and seg[0] in ("<(", ">("):               # process substitution: read the inner command
        seg = seg[1:]
    cats, prog, dflt = _v1_segment(seg, bodies, depth)
    if "run" in cats and is_verify(seg):
        cats = (cats - {"run"}) | {"verify"}
    return cats, prog, dflt


def call_v2(fn, args):
    name = str(fn or "").lower()
    for tools, cat in ((DELEGATE_TOOLS, "delegate"), (CONTROL_TOOLS, "control"),
                       (PLAN_TOOLS, "plan")):
        if name in tools:
            return {"category": cat, "cats": [cat], "programs": [], "defaulted": [],
                    "command": ""}
    return _v1_call(fn, args)                           # its primary() now reads PRIORITY v2


def install_v2_rules():
    v1.PRIORITY = PRIORITY
    v1.EXCLUDED_FROM_KEY = EXCLUDED_FROM_KEY
    v1.RUN_PROGS = v1.RUN_PROGS | KNOWN_RUN
    v1.INSPECT_PROGS = v1.INSPECT_PROGS | KNOWN_INSPECT
    v1.NOOPS = v1.NOOPS | KNOWN_NOOPS
    v1.classify_segment = segment_v2
    v1.classify_call = call_v2
    v1.SOURCE = SOURCE


# ── Claude Code transcripts (sub-agents) -> ATIF-shaped steps ───────────────

def transcript_steps(path):
    """One sub-agent .jsonl -> steps shaped like trajectory.json's, one per model call."""
    rows = [json.loads(x) for x in Path(path).read_text(errors="replace").splitlines()
            if x.strip()]
    results = {}
    for r in rows:
        if r.get("type") == "user" and isinstance((r.get("message") or {}).get("content"), list):
            for b in r["message"]["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = v1.text_of(b.get("content"))
    steps, by_msg = [], {}
    for r in rows:
        m = r.get("message") or {}
        if r.get("type") == "user":
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                steps.append({"source": "user", "timestamp": r.get("timestamp"), "message": c})
            continue
        if r.get("type") != "assistant":
            continue
        mid = m.get("id") or r.get("uuid")
        if mid not in by_msg:
            by_msg[mid] = {"source": "agent", "timestamp": r.get("timestamp"), "message": "",
                           "tool_calls": [], "observation": {"results": []}, "metrics": {}}
            steps.append(by_msg[mid])
        st = by_msg[mid]
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                st["message"] += b.get("text") or ""
            elif b.get("type") == "tool_use":
                st["tool_calls"].append({"tool_call_id": b.get("id"), "function_name": b.get("name"),
                                         "arguments": b.get("input") or {}})
                st["observation"]["results"].append({"source_call_id": b.get("id"),
                                                     "content": results.get(b.get("id"), "")})
        u = m.get("usage") or {}
        if u:
            st["metrics"] = {
                "prompt_tokens": int(u.get("input_tokens") or 0)
                + int(u.get("cache_creation_input_tokens") or 0)
                + int(u.get("cache_read_input_tokens") or 0),
                "completion_tokens": int(u.get("output_tokens") or 0),
                "cached_tokens": int(u.get("cache_read_input_tokens") or 0)}
    return steps


def tool_ids(steps):
    return {tc.get("tool_call_id") for s in steps for tc in (s.get("tool_calls") or [])
            if isinstance(tc, dict) and tc.get("tool_call_id")}


# ── one trial ────────────────────────────────────────────────────────────────

def attr(span, key):
    for a in span["attributes"]:
        if a["key"] == key:
            return next(iter(a["value"].values()))
    return None


def set_attr(span, key, value):
    span["attributes"] = [a for a in span["attributes"] if a["key"] != key]
    span["attributes"] += v1.ATTRS({key: value})


def import_trial(tdir, trial):
    traj = json.loads((tdir / "agent" / "trajectory.json").read_text(errors="replace"))
    steps, fm = v1.find_steps(traj)
    steps = [s for s in (steps or []) if isinstance(s, dict)]
    flag = [bool((s.get("extra") or {}).get("is_sidechain")) for s in steps]
    side = [s for s, f in zip(steps, flag) if f]
    main = [s for s, f in zip(steps, flag) if not f]

    doc, calls, stats = v1.convert(trial, main, fm)
    spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    doc["resourceSpans"][0]["scopeSpans"][0]["scope"] = {
        "name": "crucible.divergence.importers.harbor_jobs", "version": IMPORTER_VERSION}
    root = spans[0]
    set_attr(root, "divergence.step_key", STEP_KEY)
    for c in calls:
        c["branch"] = ""

    # branches, shallowest first, each hung off the call that launched it
    metas = []
    for mp in sorted(tdir.glob("agent/sessions/projects/*/*/subagents/agent-*.meta.json")):
        meta = json.loads(mp.read_text(errors="replace"))
        metas.append((meta.get("spawnDepth") or 1, mp, meta))
    by_call = {attr(s, "gen_ai.tool.call.id"): s["spanId"] for s in spans
               if attr(s, "gen_ai.operation.name") == "execute_tool"}
    branch_ids, orphans, branches = set(), [], []
    for depth, mp, meta in sorted(metas, key=lambda x: (x[0], x[1].name)):
        agent_id = mp.name[: -len(".meta.json")]
        jl = mp.with_name(agent_id + ".jsonl")
        bsteps = transcript_steps(jl) if jl.exists() else []
        branch_ids |= tool_ids(bsteps)
        parent = by_call.get(meta.get("toolUseId"))
        if parent is None:
            orphans.append(agent_id)
            continue
        btrial = dict(trial, run_id=f"{trial['run_id']}/{agent_id}",
                      started_at=bsteps[0]["timestamp"] if bsteps else trial["started_at"],
                      finished_at=bsteps[-1]["timestamp"] if bsteps else trial["started_at"])
        bdoc, bcalls, bstats = v1.convert(btrial, bsteps, {})
        bspans = bdoc["resourceSpans"][0]["scopeSpans"][0]["spans"]
        broot = bspans[0]
        keep = {a["key"]: a for a in broot["attributes"] if a["key"] in BRANCH_ATTRS}
        broot["attributes"] = list(keep.values()) + v1.ATTRS({
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": meta.get("agentType") or "subagent",
            "divergence.branch": agent_id,
            "divergence.branch_description": meta.get("description"),
            "divergence.spawn_depth": depth,
            "divergence.launch_call_id": meta.get("toolUseId")})
        set_attr(broot, "divergence.step_key", STEP_KEY)
        broot["name"] = f"invoke_agent {meta.get('agentType') or 'subagent'}"
        broot["parentSpanId"] = parent
        for s in bspans:
            s["traceId"] = root["traceId"]
            if attr(s, "gen_ai.operation.name") == "execute_tool":
                by_call[attr(s, "gen_ai.tool.call.id")] = s["spanId"]
        spans.extend(bspans)
        for c in bcalls:
            c.update(run_id=trial["run_id"], branch=agent_id)
        calls += bcalls
        branches.append({"agent": agent_id, "depth": depth, "calls": len(bcalls),
                         "path": bstats["path"]})

    flagged = tool_ids(side)
    if not side and not metas:
        check = "none"
    elif flagged == branch_ids and not orphans:
        check = "ok"
    else:
        check = (f"mismatch: {len(flagged - branch_ids)} flagged calls not in transcripts, "
                 f"{len(branch_ids - flagged)} transcript calls not flagged, "
                 f"{len(orphans)} branches with no launch call")
    launches = sum(c["category"] == "delegate" and c["fn"].lower() in ("agent", "task")
                   for c in calls if not c["branch"])
    set_attr(root, "divergence.branches", len(branches))
    set_attr(root, "divergence.branch_check", check)
    stats.update(branches=branches, check=check, launches=launches, flagged_steps=len(side))
    return doc, calls, stats


# ── main ─────────────────────────────────────────────────────────────────────

def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "-"


def main():
    ap = argparse.ArgumentParser(description="Import one Harbor job folder (version-2 vocabulary).")
    ap.add_argument("job", type=Path, help="the job folder, e.g. ~/crucible/harbor/jobs/tb4-fable-5-1")
    ap.add_argument("--out", type=Path, required=True, help="where runs/, manifest.json and the report go")
    ap.add_argument("--clean", action="store_true", help="delete <out>/runs first")
    a = ap.parse_args()
    job, out = a.job.expanduser().resolve(), a.out.expanduser().resolve()
    if not job.is_dir():
        raise SystemExit(f"not a folder: {job}")
    if out == job or job in out.parents:
        raise SystemExit("--out must not be inside the job folder")
    v1_hash = check_value(v1.__file__)
    install_v2_rules()
    runs_dir = out / "runs"
    if a.clean and runs_dir.exists():
        shutil.rmtree(runs_dir)

    say(f"Harbor job import {IMPORTER_VERSION}, vocabulary v2, "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    say(f"job {job.name}")
    say(f"note two's classifier harbor.py {v1_hash[:16]}: "
        + ("ok" if v1_hash.startswith(V1_SHA256) else f"FAIL (expected {V1_SHA256})"))

    found = []
    for rp in sorted(job.glob("*/result.json")):
        r = json.loads(rp.read_text(errors="replace"))
        if isinstance(r, dict) and r.get("task_name") and r.get("trial_name"):
            found.append((rp.parent, r))
    by_task = defaultdict(list)
    for tdir, r in found:
        by_task[r["task_name"].split("/")[-1]].append((tdir, r))

    trials, calls_all, problems = [], [], Counter()
    models, versions = Counter(), Counter()
    for task, items in sorted(by_task.items()):
        items.sort(key=lambda x: (str(x[1].get("started_at") or ""), x[1]["trial_name"]))
        for index, (tdir, r) in enumerate(items):
            cfg = json.loads((tdir / "config.json").read_text()) if (tdir / "config.json").exists() else {}
            agent = cfg.get("agent") or {}
            model = agent.get("model_name") or (r.get("agent_info") or {}).get("model_info", {}).get("name", "?")
            arm = str(model).split("/")[-1]
            models[model] += 1
            versions[(r.get("agent_info") or {}).get("version") or "?"] += 1
            trial = {"submission": job.name, "arm": arm, "harness": agent.get("name") or "claude-code",
                     "model": model, "task": task, "trial_name": r["trial_name"], "run_index": index,
                     "run_id": f"{task}__{arm}__{index:03d}", **v1.read_result(r), "has_log": False}
            if not (tdir / "agent" / "trajectory.json").exists():
                problems["no trajectory.json"] += 1
                trials.append(trial)
                continue
            try:
                doc, calls, stats = import_trial(tdir, trial)
            except Exception as e:                       # one bad trial is reported, not fatal
                problems[f"error {type(e).__name__}"] += 1
                say(f"!! {r['trial_name']}: {type(e).__name__}: {e}")
                trials.append(trial)
                continue
            trial.update(stats, has_log=bool(stats["n_steps"]))
            if trial["has_log"]:
                f = runs_dir / task / f"{trial['run_id']}.json"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(doc))
                calls_all += calls
            trials.append(trial)

    logged = [t for t in trials if t["has_log"]]
    say(f"trials {len(trials)}, run files {len(logged)}, tasks {len(by_task)}; "
        f"problems: {dict(problems) or 'none'}")
    say("models: " + ", ".join(f"{k} {v}" for k, v in models.items())
        + "; Claude Code: " + ", ".join(f"{k} {v}" for k, v in versions.items()))
    say(f"pass {pct(sum(t['verdict'] == 'pass' for t in trials), len(trials))} of all trials")

    v1.section("1. Kinds, share of tool calls (v2)")
    kinds = [k for k in PRIORITY]
    say(f"{'':<10} {'calls':>7} " + " ".join(f"{k:>8}" for k in kinds))
    for label, sel in (("main", lambda c: not c["branch"]), ("branches", lambda c: bool(c["branch"]))):
        cs = [c for c in calls_all if sel(c)]
        n = Counter(c["category"] for c in cs)
        say(f"{label:<10} {len(cs):>7} " + " ".join(f"{pct(n[k], len(cs)):>8}" for k in kinds))

    v1.section("2. Sub-agents")
    with_b = [t for t in logged if t.get("branches") or t.get("launches") or t.get("flagged_steps")]
    say(f"trials with sub-agents {len(with_b)}; launches {sum(t['launches'] for t in with_b)}; "
        f"branches built {sum(len(t['branches']) for t in with_b)}; "
        f"branch calls {sum(b['calls'] for t in with_b for b in t['branches'])}")
    for t in with_b:
        say(f"   {t['run_id']}: launches {t['launches']}, branches {len(t['branches'])}, "
            f"flagged steps {t['flagged_steps']}, check {t['check']}")

    v1.section("3. Verify, to read by eye")
    ver = [c for c in calls_all if c["category"] == "verify"]
    say(f"verify calls {len(ver)} ({pct(len(ver), len(calls_all))} of all)")
    for cmd, k in Counter(re.sub(r"\s+", " ", c["command"])[:90] for c in ver).most_common(12):
        say(f"   {k:>5}  {cmd}")
    mk = [c for c in calls_all if "make" in c["programs"].split(",")]
    say(f"calls with make: {len(mk)}, of which verify {sum(c['category'] == 'verify' for c in mk)}")

    v1.section("4. Still defaulted to run, top 20")
    dflt = Counter(p for c in calls_all for p in filter(None, c["defaulted"].split(",")))
    say("  " + (", ".join(f"{k} {v}" for k, v in dflt.most_common(20)) or "none"))

    bad = [t for t in with_b if t["check"] not in ("ok", "none")]
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps({
        "importer": f"harbor_jobs {IMPORTER_VERSION}",
        "importer_check_value": check_value(__file__),
        "classifier": {"file": "divergence/importers/harbor.py", "check_value": v1_hash},
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job": job.name, "step_key": {"name": STEP_KEY, "priority": PRIORITY,
                                      "excluded": sorted(EXCLUDED_FROM_KEY), "collapsed": True},
        "trials": len(trials), "runs": len(logged), "trials_with_subagents": len(with_b),
        "branch_check_failures": [t["run_id"] for t in bad]}, indent=1))
    say()
    ok = v1_hash.startswith(V1_SHA256) and not bad and not problems
    say(("PASS" if ok else "FAIL") + f": {len(logged)} of {len(trials)} trials imported, "
        f"branch check ok in {len(with_b) - len(bad)} of {len(with_b)} trials with sub-agents")
    (out / "import_report.txt").write_text("\n".join(lines_out) + "\n")


if __name__ == "__main__":
    main()
