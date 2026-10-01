# divergence/importers/tau.py
"""
Importer: τ²-bench results files -> divergence runs (OTLP-JSON, one file per run).

Everything it needs comes from the experiment's experiment.yaml:
  source.local_dir      folder holding the τ results files
  source.files          exploration + confirmation file names
  tasks                 flip / handoff / control task ids (only these are imported)

Writes, inside the experiment folder:
  runs/<task_id>/<run_id>.json   one OTLP-JSON trace per τ simulation
  validity.json                  the pre-registered task validity check
  manifest.json                  provenance: source files + sha256, counts, importer version

Run contract (what analyse.py reads from the root span):
  divergence.run_id, divergence.task_id, divergence.run_index, divergence.trajectory
  (tool names joined by "|"), divergence.termination, divergence.turns,
  gen_ai.request.model, and the optional divergence.verdict ("pass"/"fail"),
  divergence.cost_usd, divergence.task_validity ("ok"/"grader_suspect"/"unchecked").
Child spans: one `chat` span per agent message (usage + output messages), one
`execute_tool` span per tool call. Customer messages are events on the root span.

Task validity check (pre-registered in experiment.yaml): each selected task's gold
state-changing actions in the results file are compared with the same task at the
pinned repo commit. Changed -> grader_suspect. Needs the current tasks file:
  data/tau2/domains/retail/tasks.json at source.commit, saved as
  <source.local_dir>/../retail_tasks_<commit[:8]>.json   (or pass --current-tasks)

Usage, from the repo root:
  python divergence/importers/tau.py experiments/001-tau-retail
  python divergence/importers/tau.py experiments/001-tau-retail --clean
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

IMPORTER_VERSION = "0.1.0"
SOURCE = "tau2-bench"
HANDOFF_TOOL = "transfer_to_human_agents"
# Tools that only read state; every other gold action changes the database.
READ_TOOLS = {
    "find_user_id_by_email", "find_user_id_by_name_zip", "get_order_details",
    "get_product_details", "get_user_details", "list_all_product_types",
    "calculate", "think",
}
RESULT_MAX_CHARS = 4000
USER_MSG_MAX_CHARS = 2000


# ── OTLP-JSON encoding: reuse trace.py so files match our own runs ───────────

def _load_encoder():
    """Load _attrs from divergence/trace.py by path (the name 'trace' would clash
    with Python's stdlib module). Falls back to an identical local copy if the
    OpenTelemetry SDK that trace.py imports is not installed."""
    here = Path(__file__).resolve().parent.parent / "trace.py"
    try:
        spec = importlib.util.spec_from_file_location("divergence_trace", here)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._attrs, "trace.py"
    except Exception:
        def _attr_value(v):
            if isinstance(v, bool):
                return {"boolValue": v}
            if isinstance(v, int):
                return {"intValue": str(v)}
            if isinstance(v, float):
                return {"doubleValue": v}
            if isinstance(v, (list, tuple)):
                return {"arrayValue": {"values": [_attr_value(x) for x in v]}}
            return {"stringValue": str(v)}

        def _attrs(d):
            return [{"key": k, "value": _attr_value(v)} for k, v in (d or {}).items()]
        return _attrs, "local copy"


ATTRS, ENCODER = _load_encoder()


def _id(seed, n_hex):
    return hashlib.sha256(seed.encode()).hexdigest()[:n_hex]


def _ns(ts, fallback_ns):
    """τ timestamps are naive ISO strings; treat as UTC. Returns int nanoseconds."""
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1_000_000) * 1000
        except ValueError:
            pass
    return fallback_ns


def _span(trace_id, span_id, parent_id, name, start, end, attrs, events=None, error=False):
    s = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": 1,                                   # INTERNAL, as in trace.py
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(max(end, start)),
        "attributes": ATTRS(attrs),
        "events": events or [],
        "status": {"code": 2 if error else 0},
    }
    if parent_id:
        s["parentSpanId"] = parent_id
    return s


def _provider(model):
    m = model.lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    if m.startswith("gemini"):
        return "gcp.gemini"
    return "unknown"


# ── one τ simulation -> one OTLP-JSON document ───────────────────────────────

def convert_sim(sim, model, validity, source_file):
    task_id = str(sim["task_id"])
    index = int(sim.get("trial") or 0)
    run_id = f"{task_id}__{model}__{index:03d}"
    trace_id = _id(run_id, 32)
    root_id = _id(run_id + "/root", 16)
    provider = _provider(model)
    messages = sim.get("messages") or []

    base_ns = _ns(sim.get("start_time"), 0)
    tool_results = {m.get("id"): m for m in messages if m.get("role") == "tool"}

    spans, user_events, trajectory = [], [], []
    turns = replies = user_turns = tool_errors = 0
    in_tok = out_tok = 0
    last_ns = base_ns

    for seq, m in enumerate(messages):
        # monotonic start times: real timestamp, nudged by seq so ordering is stable
        start = max(_ns(m.get("timestamp"), last_ns + 1_000_000), last_ns + 1) + seq
        nxt = messages[seq + 1] if seq + 1 < len(messages) else None
        end = _ns(nxt.get("timestamp"), start + 1_000_000) if nxt else start + 1_000_000
        last_ns = start
        role = m.get("role")

        if role == "user":
            user_turns += 1
            content = m.get("content") or ""
            user_events.append({
                "timeUnixNano": str(start),
                "name": "user_message",
                "attributes": ATTRS({
                    "divergence.turn_idx": int(m.get("turn_idx") or seq),
                    "divergence.content": content[:USER_MSG_MAX_CHARS],
                }),
            })
            continue

        if role != "assistant":
            continue   # tool messages become results on execute_tool spans

        turns += 1
        usage = m.get("usage") or {}
        it = int(usage.get("prompt_tokens") or 0)
        ot = int(usage.get("completion_tokens") or 0)
        in_tok += it
        out_tok += ot
        calls = m.get("tool_calls") or []
        if not calls:
            replies += 1

        parts = []
        if m.get("content"):
            parts.append({"type": "text", "content": m["content"]})
        for tc in calls:
            parts.append({"type": "tool_call", "id": tc.get("id", ""),
                          "name": tc.get("name", ""), "arguments": tc.get("arguments") or {}})
        out_msg = [{"role": "assistant", "parts": parts,
                    "finish_reason": "tool_call" if calls else "stop"}]

        chat_id = _id(f"{run_id}/chat/{seq}", 16)
        spans.append(_span(trace_id, chat_id, root_id, f"chat {model}", start, end, {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": model,
            "gen_ai.usage.input_tokens": it,
            "gen_ai.usage.output_tokens": ot,
            "gen_ai.output.messages": json.dumps(out_msg),
            "divergence.cost_usd": float(m.get("cost") or 0.0),
            "divergence.turn_idx": int(m.get("turn_idx") or seq),
            "divergence.usage_recorded": bool(m.get("usage")),
        }))

        for k, tc in enumerate(calls):
            name = tc.get("name") or "?"
            trajectory.append(name)
            res = tool_results.get(tc.get("id")) or {}
            content = res.get("content") or ""
            err = bool(res.get("error"))
            tool_errors += int(err)
            t_start = start + 1000 * (k + 1)
            spans.append(_span(trace_id, _id(f"{run_id}/tool/{seq}/{k}", 16), root_id,
                               f"execute_tool {name}", t_start, t_start + 1000, {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
                "gen_ai.tool.call.id": tc.get("id") or "",
                "gen_ai.tool.call.arguments": json.dumps(tc.get("arguments") or {}),
                "gen_ai.tool.call.result": content[:RESULT_MAX_CHARS],
                "divergence.result_empty": (not content.strip()) or err,
                "divergence.tool_error": err,
            }, error=err))

    reward = (sim.get("reward_info") or {}).get("reward")
    verdict = "pass" if reward is not None and float(reward) >= 1.0 else "fail"
    termination = str(sim.get("termination_reason") or "?").lower()
    end_ns = max(_ns(sim.get("end_time"), last_ns + 1_000_000), last_ns + 1)

    root = _span(trace_id, root_id, None, "invoke_agent tau2-retail", base_ns or last_ns, end_ns, {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.provider.name": provider,
        "gen_ai.agent.name": "tau2-retail-baseline",
        "gen_ai.request.model": model,
        "divergence.run_id": run_id,
        "divergence.task_id": task_id,
        "divergence.run_index": index,
        "divergence.trajectory": "|".join(trajectory),
        "divergence.termination": termination,
        "divergence.turns": turns,
        "divergence.answered": termination in ("user_stop", "agent_stop"),
        "divergence.verdict": verdict,
        "divergence.reward": float(reward) if reward is not None else -1.0,
        "divergence.cost_usd": float(sim.get("agent_cost") or 0.0),
        "divergence.user_cost_usd": float(sim.get("user_cost") or 0.0),
        "divergence.replies": replies,
        "divergence.user_turns": user_turns,
        "divergence.tool_errors": tool_errors,
        "divergence.handoff": HANDOFF_TOOL in trajectory,
        "divergence.task_validity": validity,
        "divergence.source": SOURCE,
        "divergence.source_file": source_file,
        "divergence.source_sim_id": sim.get("id", ""),
    }, events=user_events)

    doc = {"resourceSpans": [{
        "resource": {"attributes": ATTRS({"service.name": "divergence",
                                          "divergence.run_id": run_id,
                                          "divergence.source": SOURCE})},
        "scopeSpans": [{
            "scope": {"name": "crucible.divergence.importers.tau", "version": IMPORTER_VERSION},
            "schemaUrl": "https://opentelemetry.io/schemas/1.44.0",
            "spans": [root] + spans,
        }],
    }]}
    summary = {"run_id": run_id, "task": task_id, "model": model, "index": index,
               "verdict": verdict, "turns": turns, "in_tok": in_tok, "out_tok": out_tok,
               "cost_usd": float(sim.get("agent_cost") or 0.0), "steps": len(trajectory),
               "path": "|".join(trajectory)}
    return run_id, task_id, doc, summary


# ── task validity check ──────────────────────────────────────────────────────

def _gold_writes(task):
    acts = ((task.get("evaluation_criteria") or {}).get("actions")) or []
    return sorted(
        (a.get("name"), json.dumps(a.get("arguments") or {}, sort_keys=True))
        for a in acts if a.get("name") not in READ_TOOLS
    )


def _scenario(task):
    return json.dumps(((task.get("user_scenario") or {}).get("instructions")) or {}, sort_keys=True)


def validity_check(results_tasks, current_path, selected):
    if not current_path or not Path(current_path).exists():
        return {t: {"status": "unchecked", "reason": "current tasks file not found"} for t in selected}, False
    current = {str(t["id"]): t for t in json.loads(Path(current_path).read_text())}
    out = {}
    for t in selected:
        old, new = results_tasks.get(t), current.get(t)
        if old is None or new is None:
            out[t] = {"status": "unchecked", "reason": "task missing in one of the files"}
            continue
        ow, nw = _gold_writes(old), _gold_writes(new)
        entry = {"status": "ok" if ow == nw else "grader_suspect",
                 "gold_writes_changed": ow != nw,
                 "scenario_changed": _scenario(old) != _scenario(new)}
        if ow != nw:
            entry["gold_writes_then"] = [f"{n} {a}" for n, a in ow]
            entry["gold_writes_now"] = [f"{n} {a}" for n, a in nw]
        out[t] = entry
    return out, True


# ── main ─────────────────────────────────────────────────────────────────────

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="τ²-bench results -> divergence runs")
    ap.add_argument("experiment", type=Path, help="experiment folder, e.g. experiments/001-tau-retail")
    ap.add_argument("--current-tasks", type=Path, default=None,
                    help="tasks.json at the pinned commit (default: next to the raw folder)")
    ap.add_argument("--clean", action="store_true", help="delete existing runs/ first")
    args = ap.parse_args()

    import yaml
    exp = args.experiment
    y = yaml.safe_load((exp / "experiment.yaml").read_text())
    src = y["source"]
    raw_dir = Path(os.path.expanduser(src["local_dir"]))
    files = list(src["files"].get("exploration") or []) + list(src["files"].get("confirmation") or [])
    t = y["tasks"]
    selected = [str(x) for g in ("flip", "handoff", "control") for x in (t.get(g) or [])]
    group = {str(x): g for g in ("flip", "handoff", "control") for x in (t.get(g) or [])}
    current_path = args.current_tasks or raw_dir.parent / f"retail_tasks_{src['commit'][:8]}.json"

    runs_dir = exp / "runs"
    if args.clean and runs_dir.exists():
        shutil.rmtree(runs_dir)

    print(f"encoder: {ENCODER}")
    validity, checked = None, False
    summaries, manifest_files = [], []

    for fname in files:
        path = raw_dir / fname
        if not path.exists():
            sys.exit(f"missing source file: {path}")
        results = json.loads(path.read_text())
        model = ((results.get("info") or {}).get("agent_info") or {}).get("llm") or fname.split("_")[0]
        results_tasks = {str(x["id"]): x for x in results.get("tasks") or []}

        if validity is None:   # the check uses the task definitions the runs were graded against
            validity, checked = validity_check(results_tasks, current_path, selected)

        n = 0
        for sim in results.get("simulations") or []:
            if str(sim["task_id"]) not in group:
                continue
            tid = str(sim["task_id"])
            run_id, task_id, doc, summary = convert_sim(sim, model, validity[tid]["status"], fname)
            out = runs_dir / task_id / f"{run_id}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(doc, indent=1))
            summary["group"] = group[tid]
            summaries.append(summary)
            n += 1
        manifest_files.append({"file": fname, "model": model, "sha256": _sha256(path),
                               "runs_imported": n})
        print(f"{fname}: {n} runs -> {runs_dir}")

    # validity.json
    (exp / "validity.json").write_text(json.dumps({
        "rule": y.get("definitions", {}).get("task_validity_check", ""),
        "checked_against": str(current_path) if checked else None,
        "commit": src.get("commit"),
        "tasks": validity,
    }, indent=1, default=str))

    # manifest.json
    (exp / "manifest.json").write_text(json.dumps({
        "importer": f"divergence.importers.tau {IMPORTER_VERSION}",
        "encoder": ENCODER,
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {k: src.get(k) for k in ("repo", "commit", "path", "licence", "downloaded",
                                          "evaluator_commit")},
        "files": manifest_files,
        "tasks": selected,
        "runs": len(summaries),
    }, indent=1, default=str))

    # console summary
    print(f"\nvalidity check: {'done against ' + str(current_path) if checked else 'SKIPPED (' + str(current_path) + ' not found)'}")
    for tid in selected:
        v = validity[tid]
        flag = "" if v["status"] == "ok" else f"  <- {v['status'].upper()}"
        extra = " (customer instructions reworded since)" if v.get("scenario_changed") else ""
        print(f"  task {tid:>4} [{group[tid]:<7}] {v['status']}{extra}{flag}")

    by_cell = defaultdict(list)
    for s in summaries:
        by_cell[(s["task"], s["model"])].append(s)
    print(f"\nruns per (task, model): {dict(Counter(len(v) for v in by_cell.values()))} "
          f"across {len(by_cell)} cells, {len(summaries)} runs")
    print(f"{'task':>5} {'group':<8} {'model':<28} {'pass':>5} {'paths':>5} {'cost $ min-max':>16}")
    for (task, model), rs in sorted(by_cell.items(), key=lambda kv: (selected.index(kv[0][0]), kv[0][1])):
        passes = sum(r["verdict"] == "pass" for r in rs)
        costs = [r["cost_usd"] for r in rs]
        print(f"{task:>5} {group[task]:<8} {model:<28} {passes}/{len(rs):<3} "
              f"{len({r['path'] for r in rs}):>5} {min(costs):>7.4f}-{max(costs):<7.4f}")


if __name__ == "__main__":
    main()