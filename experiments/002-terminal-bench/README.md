# 002 · Same task, cheaper model, five runs

How many of an agent's dependable tasks stay dependable when a cheaper model takes the
place of the one it was built on, how often a run stops and is wrong, and what a finished
task costs. 4,005 published trials from Harbor's Terminal-Bench 2.0 leaderboard, read
without making any model calls.

**[Read the note →](NOTE.md)** · Crucible Science · October 2026 · tag `002-terminal-bench-v1`

![Trajectory trees for sparql-university on GLM-4.7 and on Claude Opus 4.6](exhibits/sparql_university_glm47_opus46.png)

*Five runs of one task on each model: GLM-4.7 above (three passed, two failed), Claude
Opus 4.6 below (five passed), both on Terminus2. This pair is an illustration, chosen for
readability; most task and model pairs are messier.
[The runs side by side.](exhibits/sparql_university_pair.md)*

## In four numbers

On the 61 tasks not on Terminal-Bench 2.1's list of revised tasks:

- **11 to 14 of 33.** Claude Opus 4.6 passed 33 tasks on all five trials. On the same
  harness, three of the four cheaper models passed 11 to 14 of those every time, and
  GLM-5 passed 20. Most of the rest became unreliable; they did not fail outright (for
  DeepSeek V3.2, 13 of 21).
- **14% against 26% to 46%.** Of the runs that stopped by themselves, 14% had failed on
  Opus and 26% to 46% on the cheaper models.
- **18.6 times per run, 11.6 times per finished task.** In recorded dollars, that is how
  much less DeepSeek V3.2 cost than Opus. The saving shrinks with the runs that fail.
- **26% to 39%.** With Opus 4.6 in five different harnesses, every one still passed some
  trials and failed others on a quarter or more of the tasks.

Eight claims were written down in public before any outcome in these trials was read. Seven hold on
the 61 tasks and on all 89; the eighth is met only at its threshold on the 61 and not on
all 89. The note reports each against its bar.

## What was read

Two comparisons, with one submission in both: the same harness (Terminus2) with five
models, and the same model (Claude Opus 4.6) in five harnesses. Nine submissions, 89
tasks, five trials each.

The eight claims and their thresholds are in
[`prereg/002-terminal-bench.yaml`](../../prereg/002-terminal-bench.yaml). The importer
stops unless that file has the registered sha256.

## What is in this folder

| File | What it is |
|---|---|
| [`NOTE.md`](NOTE.md) | The note |
| [`inputs.json`](inputs.json) | The submissions on each axis, the 28 tasks Terminal-Bench 2.1 revised (with the source of that list), the registered hash of the pre-registration file, and the commit of the task repository |
| `manifest.tsv` | The dataset, its pinned revision, and the sha256 and size of each of the 12,025 input files |
| [`fetch.py`](fetch.py) | Downloads the inputs and checks every file against the manifest; `--verify` checks an existing copy |
| [`run_all.sh`](run_all.sh) | Rebuilds every number from the inputs and compares the result with `reports/` |
| [`intervals.py`](intervals.py) | Recomputes every scored value, then the 90% intervals and the verdict lines without GLM-5 and without trials that ended in an environment error |
| [`dollars.py`](dollars.py) | Dollars per run and per pass: recorded where the logs have them, labelled estimates at list price where they do not |
| [`hero_shortlist.py`](hero_shortlist.py) | How the candidates for the worked example were narrowed, and the rank of the one shown |
| [`hero_facts.py`](hero_facts.py), [`hero_verify.py`](hero_verify.py) | The worked example from the raw trial folders: each run's queries and steps, and the verifier's comparison re-run on the ten final queries |
| [`reports/`](reports/) | The five reports as we produced them: the two scored imports, the intervals, the dollars, the shortlist |
| [`checks/`](checks/) | The outputs of two checks quoted in section 6 of the note that are not part of `run_all.sh`: how often a step label hides a second action, and the comparison of the two public task repositories |
| [`exhibits/`](exhibits/) | The picture in the note, and [the runs side by side](exhibits/sparql_university_pair.md) |

Outside this folder: [`divergence/importers/harbor.py`](../../divergence/importers/harbor.py)
reads the trials, labels each tool call with an action category, and scores the
hypotheses with the thresholds copied from the pre-registration file.

## Reproduce it

From the repository root:

```
pip install pyyaml pandas duckdb huggingface_hub
python experiments/002-terminal-bench/fetch.py
bash experiments/002-terminal-bench/run_all.sh
```

`fetch.py` downloads 0.91 GB as 12,025 small files and then checks each against the
manifest. It can be stopped and run again; files already present are skipped.
`run_all.sh` takes a minute or two on a laptop and writes about 0.8 GB of generated files,
all ignored by git.

A pass ends with:

```
all reports match the committed copies
```

and exit code 0. The comparison is line by line against the five files in `reports/`;
only the run time on each report's first line is ignored. The intervals use a fixed seed,
so they reproduce exactly. If a report differs, `run_all.sh` says which and keeps the
difference under `out/logs/`.

Tested with Python 3.14.5, duckdb 1.5.6, pandas 3.0.6, PyYAML 6.0.3 and
huggingface_hub 2.1.1.

### The worked example (optional)

The note's worked example is checked from the raw trial folders and the task's own test.
This needs the task from the benchmark's repository, at the commit in `inputs.json`:

```
pip install rdflib==7.1.4
T=experiments/002-terminal-bench/tasks
git clone --filter=blob:none --sparse https://github.com/laude-institute/terminal-bench-2 $T
git -C $T sparse-checkout set sparql-university
git -C $T checkout 2fd12b88aafdd04a52c298e3940bcb189f9766d6
bash experiments/002-terminal-bench/run_all.sh
```

`run_all.sh` then also runs `hero_facts.py` and `hero_verify.py` and prints:

```
the verifier's comparison reproduces 10 of 10 recorded outcomes
```

The published trial folders hold each trial's reward but not the verifier's output, so
`hero_verify.py` re-runs the test's comparison on each run's final query. Its output
shows the task's reference rows. We do not republish those, so the outputs of these two
scripts stay in `out/` and are not in `reports/`.

## See the trajectory trees

The run files for this note are generated, not committed (4,433 files, about 750 MB), so
the trees are available after `run_all.sh`. Then, from the repository root:

```
python -m http.server
```

- Same harness, five models: `http://localhost:8000/viewer/?exp=002-terminal-bench-models`
- Same model, five harnesses: `http://localhost:8000/viewer/?exp=002-terminal-bench-harnesses`
- The worked example: `http://localhost:8000/viewer/?exp=002-terminal-bench-models&cell=sparql-university__GLM-4.7`
- The picture in the note: `http://localhost:8000/viewer/?exp=002-terminal-bench-models&cell=sparql-university__GLM-4.7&with=sparql-university__Claude-Opus-4.6&export=1&key=inspect,edit,run,install,fetch,delete,done`

A step in these trees is one tool call, labelled with an action category (inspect, edit,
run, install, fetch, delete, done, other), with consecutive repeats collapsed. In the
second link, what the viewer calls the model is the harness, because the harness is what
varies there. In the last link, `&with=` draws a second task and model under the first
(in the exhibit view, `&export=1`), and `&key=` gives each kind of step the same number
in every tree. Nothing leaves your machine.

Read the harness trees with care. A call that does several things takes one label, and on
the harness comparison a label hides a second action in 9% to 36% of calls, so the note
draws no tree from it (section 6 of the note, and `checks/hidden_actions.txt`).

## Sources and licences

- **Trials:** [`harborframework/terminal-bench-2-leaderboard`](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard)
  on Hugging Face, revision `572b2614be2c0cb2527e14f5b1e4026f1072e6c1`, Apache 2.0. The
  runs and their rewards are the submitters' and Harbor's. The trial files are not copied
  into this repository; `fetch.py` downloads them from the source.
- **Tasks:** [`laude-institute/terminal-bench-2`](https://github.com/laude-institute/terminal-bench-2)
  at commit `2fd12b88aafdd04a52c298e3940bcb189f9766d6`, Apache 2.0. Read by
  `hero_verify.py` only, and not copied into this repository.
- **The 28 revised tasks:** the list is from the
  [Terminal-Bench 2.1 announcement](https://www.tbench.ai/news/terminal-bench-2-1), read
  2 October 2026.

The note is CC BY 4.0; the code is MIT.

Crucible Science sells this analysis applied to a client's own agent. This study was
self-funded; no vendor paid for it, saw it before publication, or reviewed it.
Contact: ashwin@cruciblescience.com
