# experiments/002-terminal-bench/hero_facts.py
"""
Note 002: the raw facts behind the worked example, sparql-university on GLM-4.7 (with
Opus 4.6 beside it). Reads the published trial folders, changes nothing. Every run it
prints names the trial folder it came from.

Run from the repository root, after fetch.py (the importer need not have run):
    python experiments/002-terminal-bench/hero_facts.py

Reads   experiments/002-terminal-bench/raw/submissions/terminal-bench/2.0/
            Terminus2__GLM-4.7/... and Terminus2__Claude-Opus-4.6/...   the raw trials
        divergence/importers/harbor.py   (its parsing helpers only)
Writes  experiments/002-terminal-bench/out/hero_facts.txt   (also printed)
The output quotes the published logs at length. It is generated and is not committed.

What it prints
  1. The task as the agent was given it.
  2. Per GLM-4.7 run: the files in the trial folder, every write to the query file, the
     final query in full with a short fingerprint, the agent's last message, and what
     result.json and the verifier's own files say.
  3. For the two failing runs: every step, with what the terminal printed after the
     steps that ran something. For the passing runs: one line per command.
  4. Opus 4.6's five final queries, for comparison.
Run numbers follow the importer (trials of the task sorted by start time, then name).
"""
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from divergence.importers import harbor as H   # noqa: E402  parsing helpers; main() is not run

RAW = HERE / "raw"
OUT = HERE / "out"
TASK = "sparql-university"
TARGET = "solution.sparql"
HERO = ("GLM-4.7", "Terminus2__GLM-4.7")
BESIDE = ("Claude-Opus-4.6", "Terminus2__Claude-Opus-4.6")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n\s*\1\b", re.S)
TEXT_SUFFIXES = {".txt", ".log", ".json", ".xml", ".out", ".err", ""}
OBS_CAP = {"run": (1400, 900), "install": (250, 250)}      # (head, tail) characters

lines_out = []


def say(s=""):
    print(s)
    lines_out.append(s)


def clip(text, head, tail=0):
    text = str(text or "").replace("\r", "")
    if len(text) <= head + tail + 40:
        return text
    cut = len(text) - head - tail
    return text[:head] + f"\n   [... {cut:,} characters cut ...]\n" + (text[-tail:] if tail else "")


def indent(text, pad="      "):
    return "\n".join(pad + ln for ln in str(text).split("\n"))


def fingerprint(text):
    return hashlib.sha1(" ".join(str(text).split()).encode()).hexdigest()[:8]


def trials_for(sub):
    """The task's trials in the importer's order -> [(run index, folder, result.json)]."""
    found = [(d, r) for d, r in H.find_trials(RAW / H.BASE / sub) if r["task_name"] == TASK]
    found.sort(key=lambda x: (str(x[1].get("started_at") or ""), x[1]["trial_name"]))
    return [(i, d, r) for i, (d, r) in enumerate(found)]


def steps_of(tdir):
    p = tdir / "agent" / "trajectory.json"
    if not p.exists():
        return []
    steps, _ = H.find_steps(json.loads(p.read_text(errors="replace")))
    return [s for s in (steps or []) if isinstance(s, dict)]


def calls_of(st):
    out = []
    for tc in (st.get("tool_calls") or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function_name") or tc.get("tool_name") or tc.get("name") or "?"
        args = H.as_args(tc.get("arguments") if "arguments" in tc
                         else tc.get("parameters", tc.get("input")))
        cmd = H.command_text(args)
        out.append((fn, H.classify_call(fn, args)["category"],
                    cmd if cmd is not None else json.dumps(args)))
    return out


def obs_of(st):
    ob = st.get("observation")
    if isinstance(ob, dict):
        return "\n".join(H.text_of(r.get("content")) for r in (ob.get("results") or [])
                         if isinstance(r, dict))
    return H.text_of(ob)


def message_of(st):
    return H.text_of(st.get("message") if st.get("message") is not None else st.get("content"))


def is_agent(st):
    return (st.get("source") or st.get("role") or "agent") not in ("user", "system")


def query_writes(steps):
    """Every heredoc write to the query file, and later commands that touch it otherwise."""
    writes, touches = [], []
    for i, st in enumerate(steps):
        for fn, cat, cmd in calls_of(st):
            if TARGET not in cmd:
                continue
            m = HEREDOC.search(cmd) if "<<" in cmd else None
            if m and TARGET in cmd.split("\n", 1)[0]:
                writes.append((i, m.group(2)))
                touches = []
            else:
                touches.append((i, cat, " ".join(cmd.split())[:200]))
    return writes, touches


def task_text(steps):
    for st in steps:
        if not is_agent(st):
            msg = message_of(st)
            k = msg.find("Task Description")
            return msg[k:] if k >= 0 else msg
    return ""


def show_files(tdir):
    files = sorted(p for p in tdir.rglob("*") if p.is_file())
    say("   files in the trial folder:")
    for p in files:
        say(f"      {str(p.relative_to(tdir)):<50} {p.stat().st_size:>10,} bytes")
    return files


def show_verifier(tdir, result, files):
    say("   result.json:")
    for key in ("verifier_result", "exception_info"):
        say(f"      {key}: {clip(json.dumps(result.get(key)), 1500)}")
    shown = 0
    for p in files:
        rel = p.relative_to(tdir)
        if "verifier" not in [x.lower() for x in rel.parts[:-1]]:
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES or p.stat().st_size > 400_000:
            say(f"   verifier file {rel}: not printed (binary or large)")
            continue
        say(f"   verifier file {rel}:")
        say(indent(clip(p.read_text(errors="replace"), 3500, 3000)))
        shown += 1
    if not shown:
        say("   no readable file under a 'verifier' folder in this trial")


def show_query(steps):
    writes, touches = query_writes(steps)
    say(f"   writes to {TARGET}: {len(writes)}"
        + (" (at steps " + ", ".join(str(i) for i, _ in writes) + ")" if writes else ""))
    for n, (i, body) in enumerate(writes[:-1], 1):
        say(f"   earlier version {n}, step {i}, fingerprint {fingerprint(body)}:")
        say(indent(clip(body, 2500)))
    if writes:
        i, body = writes[-1]
        say(f"   FINAL QUERY, step {i}, fingerprint {fingerprint(body)}, "
            f"{len(body):,} characters:")
        say(indent(body))
    else:
        say("   !! no heredoc write found; commands that mention the file:")
    for i, cat, cmd in touches:
        say(f"   {'after the last write' if writes else 'mention'}, step {i} [{cat}]: {cmd}")
    return writes[-1][1] if writes else None


def show_steps(steps, full):
    say("   steps" + (" (terminal output shown after steps that ran or installed something):"
                      if full else " (commands only):"))
    for i, st in enumerate(steps):
        if not is_agent(st):
            continue
        calls = calls_of(st)
        if not calls:
            say(f"   step {i}: no command; message: {clip(' '.join(message_of(st).split()), 300)}")
            continue
        cats = [c for _, c, _ in calls]
        for fn, cat, cmd in calls:
            text = clip(cmd, 1600, 300) if full else " ".join(cmd.split())[:200]
            say(f"   step {i} [{cat}] " + (("\n" + indent(text)) if "\n" in text else text))
        if full:
            head, tail = next((OBS_CAP[c] for c in ("run", "install") if c in cats), (300, 0))
            obs = obs_of(st)
            if obs.strip():
                say("      -- terminal:")
                say(indent(clip(obs, head, tail), "      | "))


def main():
    say(f"Hero facts: {TASK}, {HERO[0]} with {BESIDE[0]} beside it (raw trial folders)")
    hero = trials_for(HERO[1])
    if not hero:
        raise SystemExit(f"no {TASK} trials under {(RAW / H.BASE / HERO[1]).relative_to(REPO)}; "
                         "run fetch.py first")
    say()
    say("== 1. The task as given to the agent " + "=" * 40)
    say(indent(clip(task_text(steps_of(hero[0][1])), 5000), "   "))

    prints = {}
    say()
    say(f"== 2. {HERO[0]}: the five runs " + "=" * 45)
    for idx, tdir, result in hero:
        info = H.read_result(result)
        steps = steps_of(tdir)
        say()
        say(f"-- run {idx:03d}  {info['verdict'].upper()}  ended {info['termination']}  "
            f"reward {info['reward']}  trial {result['trial_name']}")
        say(f"   folder {tdir.relative_to(REPO)}")
        files = show_files(tdir)
        final = show_query(steps)
        prints[HERO[0], idx] = (info["verdict"], fingerprint(final) if final else "-")
        last = [st for st in steps if is_agent(st)]
        if last:
            say("   the agent's last message:")
            say(indent(clip(message_of(last[-1]), 1500)))
        show_verifier(tdir, result, files)

    say()
    say(f"== 3. {HERO[0]}: what each run did, step by step " + "=" * 28)
    for idx, tdir, result in hero:
        verdict = H.read_result(result)["verdict"]
        say()
        say(f"-- run {idx:03d}  {verdict.upper()}")
        show_steps(steps_of(tdir), full=verdict == "fail")

    say()
    say(f"== 4. {BESIDE[0]}: the five final queries " + "=" * 33)
    for idx, tdir, result in trials_for(BESIDE[1]):
        info = H.read_result(result)
        say()
        say(f"-- run {idx:03d}  {info['verdict'].upper()}  trial {result['trial_name']}")
        say(f"   folder {tdir.relative_to(REPO)}")
        final = show_query(steps_of(tdir))
        prints[BESIDE[0], idx] = (info["verdict"], fingerprint(final) if final else "-")

    say()
    say("== 5. Fingerprints of the final queries (same fingerprint = same query text) " + "=" * 2)
    for (arm, idx), (verdict, fp) in prints.items():
        say(f"   {arm:<16} run {idx:03d}  {verdict:<4}  {fp}")

    OUT.mkdir(exist_ok=True)
    report = OUT / "hero_facts.txt"
    report.write_text("\n".join(lines_out) + "\n")
    print(f"\nreport: {report.relative_to(REPO)}")


if __name__ == "__main__":
    main()