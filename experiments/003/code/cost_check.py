# ~/crucible/harbor/cost_check.py   (read-only; writes nothing)
# Does Harbor's recorded cost price each reply at the model that wrote it, or the whole run at
# the arm's own model? Tokens are attributed to models reply by reply (each model call inside a
# reply carries its own tokens, and its model when it differs), and weighted the usual way:
# uncached input 1, cache write 1.25 (5-minute) or 2 (1-hour), cache read 0.1, output 5.
# A model's rate is then dollars per million weighted tokens: $5 for a model priced $5/$25.
#   1. per model, the rate implied by runs where that model wrote at least 99% of the weighted
#      tokens (a tight spread means the weights are right, and gives the model's rate);
#   2. for switched runs, the recorded cost against two predictions: every reply at its own
#      model's rate ("by reply"), or the whole run at the arm's rate ("whole run").
#   python ~/crucible/harbor/cost_check.py ~/crucible/harbor/jobs/tb4-fable-5 ~/crucible/harbor/jobs/tb4-fable-5-1
# Standard library only.
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

SYN = "<synthetic>"
short = lambda m: str(m or "?").split("/")[-1]


def load(p):
    return json.loads(Path(p).read_text(errors="replace"))


def units_of(u):
    cc = u.get("cache_creation") or {}
    w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
    cw = int(u.get("cache_creation_input_tokens") or 0)
    if w5 is None and w1 is None:
        w5, w1 = cw, 0
    return (int(u.get("input_tokens") or 0) + 1.25 * int(w5 or 0) + 2 * int(w1 or 0)
            + 0.1 * int(u.get("cache_read_input_tokens") or 0) + 5 * int(u.get("output_tokens") or 0))


def trial(tdir):
    r = load(tdir / "result.json")
    want = short((load(tdir / "config.json").get("agent") or {}).get("model_name"))
    cost = (r.get("agent_result") or {}).get("cost_usd")
    by_model = defaultdict(float)
    tp = tdir / "agent" / "trajectory.json"
    if not tp.exists() or cost is None:
        return None
    for s in load(tp).get("steps") or []:
        if s.get("source") != "agent":
            continue
        m = s.get("metrics") or {}
        step_model = short(s.get("model_name"))
        its = (m.get("extra") or {}).get("iterations") or []
        if its:
            for it in its:
                by_model[short(it.get("model")) if it.get("model") else step_model] += units_of(it)
        else:  # no per-call record: use the reply's own totals
            ex = m.get("extra") or {}
            cw = int(ex.get("cache_creation_input_tokens") or 0)
            cr = int(ex.get("cache_read_input_tokens") or 0)
            by_model[step_model] += units_of({
                "input_tokens": int(m.get("prompt_tokens") or 0) - cw - cr,
                "cache_creation_input_tokens": cw, "cache_creation": ex.get("cache_creation") or {},
                "cache_read_input_tokens": cr, "output_tokens": int(m.get("completion_tokens") or 0)})
    by_model.pop(SYN, None)
    return {"trial": tdir.name, "want": want, "cost": float(cost), "units": dict(by_model)}


def spread(xs):
    xs = sorted(xs)
    q = lambda f: xs[min(len(xs) - 1, int(f * len(xs)))]
    return f"median {median(xs):.3f}, middle half {q(.25):.3f} to {q(.75):.3f}, range {xs[0]:.3f} to {xs[-1]:.3f}"


rows = []
for job in sys.argv[1:]:
    for rp in sorted(Path(job).expanduser().glob("*/result.json")):
        if load(rp).get("trial_name"):
            x = trial(rp.parent)
            if x and sum(x["units"].values()) > 0:
                rows.append(x)

rate = {}
print("1. rate implied by runs one model wrote (99% or more of weighted tokens; $ per million)")
for model in sorted({m for x in rows for m in x["units"]}):
    alone = [x["cost"] / sum(x["units"].values()) * 1e6 for x in rows
             if x["units"].get(model, 0) >= 0.99 * sum(x["units"].values())]
    if alone:
        rate[model] = median(alone)
        print(f"   {model:<18} {len(alone):>4} runs: {spread(alone)}")
    else:
        print(f"   {model:<18}    0 runs mostly written by it; rate unknown")

print()
print("2. switched runs: recorded cost against the two predictions (error as % of recorded)")
for want in sorted({x["want"] for x in rows}):
    sw = [x for x in rows if x["want"] == want and set(x["units"]) - {want}]
    known = [x for x in sw if all(m in rate for m in x["units"]) and want in rate]
    if not sw:
        continue
    by_reply, whole, closer = [], [], 0
    for x in known:
        pr = sum(u * rate[m] for m, u in x["units"].items()) / 1e6
        pw = sum(x["units"].values()) * rate[want] / 1e6
        by_reply.append(abs(pr - x["cost"]) / x["cost"] * 100)
        whole.append(abs(pw - x["cost"]) / x["cost"] * 100)
        closer += abs(pr - x["cost"]) < abs(pw - x["cost"])
    print(f"   {want}: {len(sw)} switched runs, {len(known)} with every model's rate known")
    if known:
        print(f"      by reply:  error {spread(by_reply)}")
        print(f"      whole run: error {spread(whole)}")
        print(f"      by reply closer in {closer} of {len(known)}")
print("done")