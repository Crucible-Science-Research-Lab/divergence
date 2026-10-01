# divergence/analyse.py
"""
Experiment analysis -> analysis.json (the analysis-to-viewer contract).

Reads   <experiment>/runs/**/*.json   (OTLP-JSON, one trace per run, from our own
                                       harness or from any importer)
        <experiment>/experiment.yaml  (optional: meta, limitations, task groups,
                                       models, hypotheses)
Writes  <experiment>/analysis.json

The viewer reads only analysis.json plus the trace files it points to. If the
viewer ever needs a field that is not here, the change belongs in this file.

Contract v0.2 (backward-compatible with contract v0.1)
  meta       slug, status, question, models, temperature, counts, limitations,
             step_key, has_verdicts, has_cost, model_roles, task_groups
  cells[]    one per (task, model):
               summary   n, distinct paths, first split, branch points, turns,
                         token spread, terminations; new in v0.2: verdicts,
                         outcome_flip, same_path_diff_outcome, cost_usd spread,
                         cost_ratio, task_validity, task_group, model_role,
                         first_split_kind
               runs[]    per run: id, index, termination, failure, turns, tokens,
                         per-turn tokens, trajectory, final answer, trace path;
                         new in v0.2: verdict, cost_usd, handoff, replies,
                         user_turns, tool_errors, task_validity
               tree      prefix tree of trajectories = the river.
                         nodes[] {id, parent, depth, step, n, run_ids, children,
                                  is_branch, is_end, failure}
                         Every run ends in an END node. With verdicts the END
                         step is "END:pass" / "END:fail", so runs with the same
                         path but a different outcome split at the very end;
                         without verdicts it is "END:<termination>" as before.
  exhibits   featured cell, divergent pairs, failures; new in v0.2:
             outcome_pairs (pass run vs fail run, same cell),
             same_path_diff_outcome (pass and fail runs with identical paths)
  hypotheses scored per experiment.yaml (H1-H5 as defined there), when present
  per_model  pass rate, flips, cost and token spreads per model, over the cells

failure = verdict is "fail" when the run carries a verdict; otherwise the v0.1
rule (termination is not "answered").

Step key: tool name only (divergence.trajectory).

Usage: python divergence/analyse.py experiments/<slug>
"""
import collections
import json
import os
import statistics
import sys

CONTRACT_VERSION = "0.2"
ANSWERED = "answered"
HANDOFF_TOOL = "transfer_to_human_agents"


# ── trace reading ────────────────────────────────────────────────────────────

def attrs(span):
    out = {}
    for a in span.get("attributes", []):
        v = a.get("value", {})
        out[a["key"]] = next(iter(v.values()), None) if v else None
    return out


def all_spans(data):
    for rs in data.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            yield from ss.get("spans", [])


def as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return None


def as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def text_from_messages(raw):
    """Pull plain text out of gen_ai.output.messages; fall back to the raw string."""
    if raw is None:
        return None
    try:
        msgs = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return str(raw)
    texts = []
    for m in msgs if isinstance(msgs, list) else [msgs]:
        if not isinstance(m, dict):
            continue
        for p in m.get("parts", []) or []:
            if isinstance(p, dict) and p.get("type") == "text" and p.get("content"):
                texts.append(str(p["content"]))
        if isinstance(m.get("content"), str):
            texts.append(m["content"])
    return "\n".join(texts) if texts else None


def load_run(path, exp_dir):
    with open(path) as f:
        data = json.load(f)
    spans = list(all_spans(data))
    roots = [s for s in spans if not s.get("parentSpanId")]
    if not roots:
        raise ValueError("no root span")
    ra = attrs(roots[0])

    chats = sorted(
        (s for s in spans if attrs(s).get("gen_ai.operation.name") == "chat"),
        key=lambda s: int(s.get("startTimeUnixNano", 0) or 0),
    )
    in_turn = [int(attrs(s).get("gen_ai.usage.input_tokens", 0) or 0) for s in chats]
    out_turn = [int(attrs(s).get("gen_ai.usage.output_tokens", 0) or 0) for s in chats]
    # final answer: the last agent message that contains text
    final_answer = None
    for s in reversed(chats):
        final_answer = text_from_messages(attrs(s).get("gen_ai.output.messages"))
        if final_answer:
            break

    traj_raw = ra.get("divergence.trajectory", "") or ""
    trajectory = traj_raw.split("|") if traj_raw else []
    termination = ra.get("divergence.termination", "?") or "?"
    verdict = ra.get("divergence.verdict")
    verdict = verdict if verdict in ("pass", "fail") else None
    failure = (verdict == "fail") if verdict else (termination != ANSWERED)
    stem = os.path.splitext(os.path.basename(path))[0]

    def opt_int(key):
        v = ra.get(key)
        return int(v) if v not in (None, "") else None

    return {
        "id": ra.get("divergence.run_id") or stem,
        "task": str(ra.get("divergence.task_id") or os.path.basename(os.path.dirname(path))),
        "model": str(ra.get("gen_ai.request.model", "?")),
        "index": int(ra.get("divergence.run_index", 0) or 0),
        "termination": termination,
        "verdict": verdict,
        "failure": failure,
        "answered": as_bool(ra.get("divergence.answered")),
        "turns": int(ra.get("divergence.turns", 0) or 0),
        "in_tok": sum(in_turn),
        "out_tok": sum(out_turn),
        "in_turn": in_turn,
        "out_turn": out_turn,
        "cost_usd": as_float(ra.get("divergence.cost_usd")),
        "trajectory": trajectory,
        "handoff": HANDOFF_TOOL in trajectory,
        "replies": opt_int("divergence.replies"),
        "user_turns": opt_int("divergence.user_turns"),
        "tool_errors": opt_int("divergence.tool_errors"),
        "task_validity": ra.get("divergence.task_validity"),
        "final_answer": final_answer,
        "trace_path": os.path.relpath(path, exp_dir),
    }


def load_runs(exp_dir):
    runs_dir = os.path.join(exp_dir, "runs")
    runs = []
    for root, _, files in os.walk(runs_dir):
        for fname in sorted(files):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(root, fname)
            try:
                runs.append(load_run(path, exp_dir))
            except Exception as e:
                print(f"  skip {path}: {e}", file=sys.stderr)
    return runs


# ── the river: prefix tree ───────────────────────────────────────────────────

def end_step(run):
    return f"END:{run['verdict']}" if run["verdict"] else f"END:{run['termination']}"


def build_tree(runs):
    nodes = [{"id": 0, "parent": None, "depth": 0, "step": "START", "n": len(runs),
              "run_ids": [r["id"] for r in runs], "children": [],
              "is_branch": False, "is_end": False, "failure": False}]
    index = {}  # (parent_id, step) -> node_id

    def child(parent_id, step, depth, run, is_end=False, failure=False):
        key = (parent_id, step)
        if key not in index:
            nid = len(nodes)
            nodes.append({"id": nid, "parent": parent_id, "depth": depth, "step": step,
                          "n": 0, "run_ids": [], "children": [],
                          "is_branch": False, "is_end": is_end, "failure": failure})
            nodes[parent_id]["children"].append(nid)
            index[key] = nid
        node = nodes[index[key]]
        node["n"] += 1
        node["run_ids"].append(run["id"])
        return node["id"]

    for r in runs:
        cur = 0
        for d, step in enumerate(r["trajectory"], start=1):
            cur = child(cur, step, d, r)
        child(cur, end_step(r), len(r["trajectory"]) + 1, r,
              is_end=True, failure=r["failure"])

    for n in nodes:
        n["is_branch"] = len(n["children"]) > 1
    return nodes


def branch_points(nodes):
    """'step k' = the k-th step (0-based) is where runs sharing a prefix disagree."""
    out = []
    for n in nodes:
        if n["is_branch"]:
            out.append({
                "node_id": n["id"],
                "step": n["depth"],
                "options": {nodes[c]["step"]: nodes[c]["n"] for c in n["children"]},
            })
    return sorted(out, key=lambda b: (b["step"], b["node_id"]))


def split_kind(bp):
    """Classify a branch point by what the runs disagreed about."""
    if bp is None:
        return None
    opts = list(bp["options"])
    ends = [o for o in opts if o.startswith("END:")]
    tools = [o for o in opts if not o.startswith("END:")]
    if HANDOFF_TOOL in tools:
        return "escalation"
    if ends and tools:
        return "stop_vs_continue"
    if ends and not tools:
        return "outcome_only"
    return "tool_choice"


# ── cells ────────────────────────────────────────────────────────────────────

def spread(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"min": min(xs), "median": statistics.median(xs), "max": max(xs)}


def ratio(xs):
    xs = [x for x in xs if x is not None]
    if not xs or min(xs) <= 0:
        return None
    return round(max(xs) / min(xs), 2)


def same_path_diff_outcome(runs):
    by_path = collections.defaultdict(list)
    for r in runs:
        if r["verdict"]:
            by_path["|".join(r["trajectory"])].append(r)
    out = []
    for path, rs in by_path.items():
        passes = [r["id"] for r in rs if r["verdict"] == "pass"]
        fails = [r["id"] for r in rs if r["verdict"] == "fail"]
        if passes and fails:
            out.append({"path": path, "pass_runs": passes, "fail_runs": fails})
    return out


def build_cell(task, model, runs, groups, roles):
    runs = sorted(runs, key=lambda r: r["index"])
    tree = build_tree(runs)
    bps = branch_points(tree)
    in_toks = [r["in_tok"] for r in runs]
    costs = [r["cost_usd"] for r in runs]
    idx = [r["index"] for r in runs]
    verdicts = collections.Counter(r["verdict"] for r in runs if r["verdict"])
    validity = {r["task_validity"] for r in runs if r["task_validity"]}
    return {
        "cell_id": f"{task}__{model.split('/')[-1]}",
        "task": task,
        "model": model,
        "summary": {
            "n": len(runs),
            "distinct_paths": len({"|".join(r["trajectory"]) for r in runs}),
            "first_split_step": bps[0]["step"] if bps else None,
            "first_split_kind": split_kind(bps[0]) if bps else None,
            "branch_points": bps,
            "turns": spread([r["turns"] for r in runs]),
            "in_tok": spread(in_toks),
            "out_tok": spread([r["out_tok"] for r in runs]),
            "in_tok_ratio": ratio(in_toks),
            "cost_usd": spread(costs),
            "cost_ratio": ratio(costs),
            "terminations": dict(collections.Counter(r["termination"] for r in runs)),
            "verdicts": {"pass": verdicts.get("pass", 0), "fail": verdicts.get("fail", 0)} if verdicts else None,
            "outcome_flip": bool(verdicts.get("pass") and verdicts.get("fail")),
            "same_path_diff_outcome": same_path_diff_outcome(runs),
            "handoffs": sum(r["handoff"] for r in runs),
            "task_validity": validity.pop() if len(validity) == 1 else (sorted(validity) or None),
            "task_group": groups.get(task),
            "model_role": roles.get(model),
            "missing_indices": sorted(set(range(max(idx) + 1)) - set(idx)),
        },
        "runs": runs,
        "tree": {"nodes": tree},
    }


# ── exhibits ─────────────────────────────────────────────────────────────────

def build_exhibits(cells, has_verdicts):
    divergent = [c for c in cells if c["summary"]["distinct_paths"] > 1]

    featured = None
    if has_verdicts:
        flips = [c for c in cells if c["summary"]["outcome_flip"]
                 and c["summary"]["task_validity"] != "grader_suspect"
                 and c["summary"]["model_role"] != "exploration"]
        flips = flips or [c for c in cells if c["summary"]["outcome_flip"]]
        featured = max(flips, key=lambda c: (c["summary"]["distinct_paths"],
                                             -(c["summary"]["first_split_step"] or 99),
                                             c["summary"]["cost_ratio"] or 0), default=None)
    if featured is None:
        featured = max(divergent, key=lambda c: c["summary"]["in_tok_ratio"] or 0, default=None)

    pairs = []
    for c in divergent:
        lo = min(c["runs"], key=lambda r: r["in_tok"])
        hi = max(c["runs"], key=lambda r: r["in_tok"])
        pairs.append({"cell_id": c["cell_id"], "short_run": lo["id"], "long_run": hi["id"],
                      "turns": [lo["turns"], hi["turns"]], "in_tok": [lo["in_tok"], hi["in_tok"]],
                      "cost_usd": [lo["cost_usd"], hi["cost_usd"]]})
    pairs.sort(key=lambda p: p["in_tok"][1] / max(p["in_tok"][0], 1), reverse=True)

    outcome_pairs = []
    for c in cells:
        if not c["summary"]["outcome_flip"]:
            continue
        p = [r for r in c["runs"] if r["verdict"] == "pass"]
        f = [r for r in c["runs"] if r["verdict"] == "fail"]
        best = min(((a, b) for a in p for b in f),
                   key=lambda ab: -first_diff(ab[0]["trajectory"], ab[1]["trajectory"]))
        outcome_pairs.append({"cell_id": c["cell_id"], "pass_run": best[0]["id"],
                              "fail_run": best[1]["id"],
                              "diverge_at": first_diff(best[0]["trajectory"], best[1]["trajectory"])})

    spdo = [{"cell_id": c["cell_id"], **g} for c in cells for g in c["summary"]["same_path_diff_outcome"]]
    failures = [{"cell_id": c["cell_id"], "run": r["id"], "termination": r["termination"],
                 "verdict": r["verdict"], "turns": r["turns"]}
                for c in cells for r in c["runs"] if r["failure"]]
    return {"featured_cell": featured["cell_id"] if featured else None,
            "divergent_pairs": pairs, "outcome_pairs": outcome_pairs,
            "same_path_diff_outcome": spdo, "failures": failures}


# ── hypotheses (as defined in experiment.yaml) ───────────────────────────────

def first_diff(a, b):
    """Step at which two tool paths first differ; identical paths score len + 1."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    if len(a) == len(b):
        return len(a) + 1
    return min(len(a), len(b))


def score_hypotheses(cells, y):
    hyp = (y or {}).get("hypotheses") or {}
    if not hyp:
        return None
    models = (y.get("models") or {})
    conf = [str(m) for m in (models.get("confirmation") or [])]
    groups = (y.get("tasks") or {})
    control = {str(t) for t in (groups.get("control") or [])}
    all_tasks = {str(t) for g in ("flip", "handoff", "control") for t in (groups.get(g) or [])}
    cc = [c for c in cells if c["model"] in conf and (not all_tasks or c["task"] in all_tasks)]
    valid = lambda c: c["summary"]["task_validity"] != "grader_suspect"
    out = {"scored_on": conf,
           "excluded_grader_suspect": sorted({c["task"] for c in cc if not valid(c)})}

    if "H1" in hyp:
        per = {}
        for m in conf:
            pool = [c for c in cc if c["model"] == m and c["task"] not in control and valid(c)]
            flips = [c["task"] for c in pool if c["summary"]["outcome_flip"]]
            per[m] = {"flips": len(flips), "of": len(pool),
                      "share": round(len(flips) / len(pool), 3) if pool else None,
                      "tasks": sorted(flips, key=lambda t: int(t) if t.isdigit() else t)}
        shares = [v["share"] for v in per.values() if v["share"] is not None]
        verdict = ("supported" if shares and all(s >= 0.25 for s in shares) else
                   "killed" if shares and all(s < 0.10 for s in shares) else "mixed")
        out["H1"] = {"verdict": verdict, "per_model": per}

    if "H2" in hyp:
        kinds = collections.Counter(c["summary"]["first_split_kind"] for c in cc
                                    if c["summary"]["first_split_kind"])
        decision = kinds.get("stop_vs_continue", 0) + kinds.get("escalation", 0)
        tool = kinds.get("tool_choice", 0)
        out["H2"] = {"verdict": "supported" if decision > tool else "killed",
                     "counts": dict(kinds), "decision_splits": decision, "tool_choice_splits": tool}

    if "H3" in hyp:
        pf, pp = [], []
        for c in cc:
            if not (c["summary"]["outcome_flip"] and valid(c)):
                continue
            rs = c["runs"]
            for i in range(len(rs)):
                for j in range(i + 1, len(rs)):
                    a, b = rs[i], rs[j]
                    d = first_diff(a["trajectory"], b["trajectory"])
                    if {a["verdict"], b["verdict"]} == {"pass", "fail"}:
                        pf.append(d)
                    elif a["verdict"] == b["verdict"] == "pass":
                        pp.append(d)
        mpf = round(statistics.mean(pf), 2) if pf else None
        mpp = round(statistics.mean(pp), 2) if pp else None
        verdict = ("supported" if mpf is not None and mpp is not None and mpf < mpp else
                   "killed" if mpf is not None and mpp is not None else "not scorable")
        out["H3"] = {"verdict": verdict, "pass_fail_mean": mpf, "pass_fail_pairs": len(pf),
                     "pass_pass_mean": mpp, "pass_pass_pairs": len(pp)}

    if "H4" in hyp:
        per, any2x = {}, False
        for m in conf:
            pool = [c for c in cc if c["model"] == m]
            hits = [c["task"] for c in pool if (c["summary"]["cost_ratio"] or 0) >= 2]
            any2x = any2x or bool(hits)
            per[m] = {"tasks_2x": len(hits), "of": len(pool),
                      "share": round(len(hits) / len(pool), 3) if pool else None,
                      "max_ratio": max((c["summary"]["cost_ratio"] or 0 for c in pool), default=None),
                      "tasks": hits}
        shares = [v["share"] for v in per.values() if v["share"] is not None]
        verdict = ("supported" if shares and max(shares) >= 0.10 else
                   "killed" if not any2x else "mixed")
        out["H4"] = {"verdict": verdict, "per_model": per}

    if "H5" in hyp:
        hits = [{"cell_id": c["cell_id"], "groups": c["summary"]["same_path_diff_outcome"]}
                for c in cc if valid(c) and c["summary"]["outcome_flip"]
                and c["summary"]["same_path_diff_outcome"]]
        out["H5"] = {"verdict": "supported" if hits else "killed", "cells": hits}
    return out


def per_model(cells):
    out = {}
    for m in sorted({c["model"] for c in cells}):
        mc = [c for c in cells if c["model"] == m]
        runs = [r for c in mc for r in c["runs"]]
        with_v = [r for r in runs if r["verdict"]]
        out[m] = {
            "cells": len(mc), "runs": len(runs),
            "pass_rate": round(sum(r["verdict"] == "pass" for r in with_v) / len(with_v), 3) if with_v else None,
            "flipping_cells": sum(c["summary"]["outcome_flip"] for c in mc),
            "divergent_cells": sum(c["summary"]["distinct_paths"] > 1 for c in mc),
            "cells_cost_2x": sum((c["summary"]["cost_ratio"] or 0) >= 2 for c in mc),
            "max_cost_ratio": max((c["summary"]["cost_ratio"] or 0 for c in mc), default=None),
            "cost_usd_total": round(sum(r["cost_usd"] or 0 for r in runs), 4),
            "handoff_runs": sum(r["handoff"] for r in runs),
        }
    return out


# ── meta ─────────────────────────────────────────────────────────────────────

def load_yaml(exp_dir):
    ypath = os.path.join(exp_dir, "experiment.yaml")
    if not os.path.exists(ypath):
        return {}
    try:
        import yaml
    except ImportError:
        print("  PyYAML not installed; meta taken from traces only (pip install pyyaml)",
              file=sys.stderr)
        return {}
    with open(ypath) as f:
        return yaml.safe_load(f) or {}


def load_meta(exp_dir, y, runs):
    meta = {"slug": os.path.basename(os.path.normpath(exp_dir))}
    for k in ("slug", "status", "question", "preregistered", "temperature",
              "provider", "tier", "max_turns", "limitations"):
        if k in y:
            meta[k] = y[k]
    meta["models"] = sorted({r["model"] for r in runs})
    meta["tasks"] = sorted({r["task"] for r in runs}, key=lambda t: (not t.isdigit(), int(t) if t.isdigit() else 0, t))
    meta["n_runs"] = len(runs)
    meta["step_key"] = "tool_name"
    meta["has_verdicts"] = any(r["verdict"] for r in runs)
    meta["has_cost"] = any(r["cost_usd"] is not None for r in runs)
    return json.loads(json.dumps(meta, default=str))


def roles_and_groups(y):
    roles = {}
    for role in ("exploration", "confirmation"):
        for m in ((y.get("models") or {}).get(role) or []):
            roles[str(m)] = role
    groups = {}
    for g in ("flip", "handoff", "control"):
        for t in ((y.get("tasks") or {}).get(g) or []):
            groups[str(t)] = g
    return roles, groups


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python divergence/analyse.py experiments/<slug>")
    exp_dir = sys.argv[1]
    runs = load_runs(exp_dir)
    if not runs:
        sys.exit(f"no traces under {exp_dir}/runs")
    y = load_yaml(exp_dir)
    roles, groups = roles_and_groups(y)

    by_cell = collections.defaultdict(list)
    for r in runs:
        by_cell[(r["task"], r["model"])].append(r)
    cells = [build_cell(t, m, rs, groups, roles) for (t, m), rs in sorted(by_cell.items())]

    meta = load_meta(exp_dir, y, runs)
    meta["n_cells"] = len(cells)
    meta["model_roles"] = roles
    meta["task_groups"] = groups
    hypotheses = score_hypotheses(cells, y) if meta["has_verdicts"] else None
    out = {
        "contract_version": CONTRACT_VERSION,
        "meta": meta,
        "cells": cells,
        "exhibits": build_exhibits(cells, meta["has_verdicts"]),
        "per_model": per_model(cells),
        "hypotheses": hypotheses,
    }

    path = os.path.join(exp_dir, "analysis.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1, default=str)

    ex = out["exhibits"]
    print(f"wrote {path}  (contract {CONTRACT_VERSION})")
    print(f"  {len(runs)} runs, {len(cells)} cells, "
          f"{sum(1 for c in cells if c['summary']['distinct_paths'] > 1)} divergent, "
          f"{len(ex['failures'])} failed runs")
    print(f"  featured cell: {ex['featured_cell']}")
    if ex["divergent_pairs"]:
        p = ex["divergent_pairs"][0]
        print(f"  top token pair: {p['cell_id']}  {p['in_tok'][0]} vs {p['in_tok'][1]} in_tok, "
              f"{p['turns'][0]} vs {p['turns'][1]} turns")
    missing_answers = sum(1 for r in runs if not r["final_answer"])
    print(f"  final answers captured: {len(runs) - missing_answers}/{len(runs)}")

    if meta["has_verdicts"]:
        print("\nper model")
        for m, s in out["per_model"].items():
            role = roles.get(m, "")
            print(f"  {m:<28} {role:<12} pass {s['pass_rate']:.0%}  flipping cells {s['flipping_cells']}/{s['cells']}  "
                  f"cost 2x+ {s['cells_cost_2x']}  max cost x{s['max_cost_ratio']}  handoff runs {s['handoff_runs']}")
        print(f"\nsame path, different outcome: {len(ex['same_path_diff_outcome'])} groups")
    if hypotheses:
        print(f"\nhypotheses (scored on {', '.join(hypotheses['scored_on'])}; "
              f"excluded as grader-suspect: {', '.join(hypotheses['excluded_grader_suspect']) or 'none'})")
        h = hypotheses
        if "H1" in h:
            pm = "; ".join(f"{m}: {v['flips']}/{v['of']} ({v['share']:.0%})" for m, v in h["H1"]["per_model"].items())
            print(f"  H1 {h['H1']['verdict'].upper():<10} flips  {pm}")
        if "H2" in h:
            print(f"  H2 {h['H2']['verdict'].upper():<10} first split  {h['H2']['counts']}")
        if "H3" in h:
            print(f"  H3 {h['H3']['verdict'].upper():<10} pass-fail mean {h['H3']['pass_fail_mean']} "
                  f"({h['H3']['pass_fail_pairs']} pairs) vs pass-pass {h['H3']['pass_pass_mean']} "
                  f"({h['H3']['pass_pass_pairs']} pairs)")
        if "H4" in h:
            pm = "; ".join(f"{m}: {v['tasks_2x']}/{v['of']} at 2x+, max x{v['max_ratio']}" for m, v in h["H4"]["per_model"].items())
            print(f"  H4 {h['H4']['verdict'].upper():<10} cost  {pm}")
        if "H5" in h:
            print(f"  H5 {h['H5']['verdict'].upper():<10} same path, different outcome in "
                  f"{len(h['H5']['cells'])} flipping cells: {', '.join(c['cell_id'] for c in h['H5']['cells'])}")


if __name__ == "__main__":
    main()