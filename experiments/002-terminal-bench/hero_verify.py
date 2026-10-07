# experiments/002-terminal-bench/hero_verify.py
"""
Note 002: re-run the verifier's comparison on the ten final queries of the worked example
(sparql-university: GLM-4.7 and Opus 4.6 on Terminus2). Changes nothing.

The published trial folders hold no verifier output, only the reward. This script takes
each run's final query from its raw trajectory and does what the task's own test does:
run it with rdflib on the task's separate test graph and compare the rows with the
reference rows in tests/test_outputs.py. If that reproduces all ten recorded rewards, the
reason for each failure can be read from the rows.

One-time set-up, from the repository root, after fetch.py. The task is fetched from the
benchmark's own repository at the commit named in inputs.json (task_repo):
    pip install rdflib==7.1.4
    T=experiments/002-terminal-bench/tasks
    git clone --filter=blob:none --sparse https://github.com/laude-institute/terminal-bench-2 $T
    git -C $T sparse-checkout set sparql-university
    git -C $T checkout 2fd12b88aafdd04a52c298e3940bcb189f9766d6

Run:
    python experiments/002-terminal-bench/hero_verify.py

Reads   experiments/002-terminal-bench/tasks/sparql-university/
            tests/test_outputs.py               the reference rows
            tests/university_graph_test.ttl     the graph the verifier uses
            environment/university_graph.ttl    the graph the agent could see
        the ten raw trial folders (through hero_facts.py and the importer's helpers)
Writes  experiments/002-terminal-bench/out/hero_verify.txt   (also printed)
The output shows the task's reference rows. It is generated and is not committed, and
tasks/ is ignored by git.
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hero_facts as F             # noqa: E402  trial lookup and query extraction

OUT = HERE / "out"
TASKS = HERE / "tasks"             # the task repository, checked out at the pinned commit
TASK_DIR = TASKS / F.TASK
PINNED = json.loads((HERE / "inputs.json").read_text())["task_repo"]["commit"]
VERIFIER_RDFLIB = "7.1.4"
TEST_GRAPH = TASK_DIR / "tests" / "university_graph_test.ttl"
SEEN_GRAPH = TASK_DIR / "environment" / "university_graph.ttl"
TEST_FILE = TASK_DIR / "tests" / "test_outputs.py"

lines_out = []


def say(s=""):
    print(s)
    lines_out.append(s)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def normalize_countries(countries_str):
    """As in the task's test: sort the countries so their order is ignored."""
    return ", ".join(sorted(c.strip() for c in countries_str.split(",")))


def rows_of(graph, query_text):
    """The rows in the form the test compares -> (set of (name, countries), error or None)."""
    try:
        return {(str(r.professorName), normalize_countries(str(r.countries)))
                for r in graph.query(query_text)}, None
    except Exception as e:                 # the test fails on any error, so record it
        return set(), f"{type(e).__name__}: {e}"


def fmt_rows(rows):
    return "; ".join(f"{n} | {c}" for n, c in sorted(rows)) or "(no rows)"


def main():
    try:
        import rdflib
        from rdflib import Graph
    except ImportError:
        raise SystemExit("rdflib is not installed: pip install rdflib==7.1.4")
    for p in (TEST_FILE, TEST_GRAPH, SEEN_GRAPH):
        if not p.exists():
            raise SystemExit(f"not found: {p.relative_to(F.REPO)}\n"
                             "See the set-up commands at the top of this file.")

    spec = importlib.util.spec_from_file_location("task_test_outputs", TEST_FILE)
    task_test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(task_test)
    reference = {(str(n), normalize_countries(str(c))) for n, c in task_test.REFERENCE_RESULTS}

    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(TASKS), *args],
                                  capture_output=True, text=True).stdout.strip()
        except OSError:
            return ""

    # tasks/ must be its own clone; inside another repository git would answer for that one
    own = git("rev-parse", "--show-toplevel")
    commit = (git("rev-parse", "HEAD") if own and Path(own).resolve() == TASKS.resolve()
              else "") or "unknown"

    say(f"Hero check: the verifier's comparison, re-run on the ten final queries ({F.TASK})")
    say(f"task repo laude-institute/terminal-bench-2 at {commit}"
        + ("" if commit == PINNED else f"   !! expected {PINNED}"))
    say(f"rdflib {rdflib.__version__}"
        + ("" if rdflib.__version__ == VERIFIER_RDFLIB
           else f"   !! the verifier uses {VERIFIER_RDFLIB}"))
    for p in (TEST_FILE, TEST_GRAPH, SEEN_GRAPH):
        say(f"   {str(p.relative_to(TASKS)):<52} {p.stat().st_size:>7,} bytes  sha256 {sha(p)}")
    say("reference rows (tests/test_outputs.py), on the TEST graph:")
    say(f"   {fmt_rows(reference)}")

    test_graph, seen_graph = Graph(), Graph()
    test_graph.parse(str(TEST_GRAPH), format="ttl")
    seen_graph.parse(str(SEEN_GRAPH), format="ttl")

    agree = total = 0
    for arm, sub in (F.HERO, F.BESIDE):
        say()
        say(f"== {arm} " + "=" * (66 - len(arm)))
        for idx, tdir, result in F.trials_for(sub):
            recorded = F.H.read_result(result)["verdict"]
            writes, _ = F.query_writes(F.steps_of(tdir))
            total += 1
            say()
            say(f"-- run {idx:03d}  recorded {recorded.upper()}  trial {result['trial_name']}")
            if not writes:
                say("   !! no final query found in the trajectory")
                continue
            step, query = writes[-1]
            got, err = rows_of(test_graph, query)
            here = "pass" if err is None and got == reference else "fail"
            agree += here == recorded
            say(f"   final query: step {step}, fingerprint {F.fingerprint(query)}")
            say(f"   on the TEST graph: {here.upper()} here"
                + ("  (same as recorded)" if here == recorded else "  !! DIFFERS from recorded"))
            if err:
                say(f"      query error: {err[:300]}")
            say(f"      rows: {fmt_rows(got)}")
            if here == "fail" and not err:
                say(f"      reference rows not returned:    {fmt_rows(reference - got)}")
                say(f"      returned rows not in reference: {fmt_rows(got - reference)}")
            seen, err2 = rows_of(seen_graph, query)
            say("   on the graph the agent could see: "
                + (f"query error: {err2[:300]}" if err2 else fmt_rows(seen)))

    say()
    say(f"the verifier's comparison reproduces {agree} of {total} recorded outcomes")
    OUT.mkdir(exist_ok=True)
    report = OUT / "hero_verify.txt"
    report.write_text("\n".join(lines_out) + "\n")
    print(f"\nreport: {report.relative_to(F.REPO)}")


if __name__ == "__main__":
    main()