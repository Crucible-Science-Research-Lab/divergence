# divergence

Run the same request through an AI agent several times and the runs do not always take
the same path. **divergence** reads those repeated runs and shows where they part ways,
which splits change the outcome, and what each path costs.

It works on recorded runs, not on a live agent: any source that logs its tool calls can
be imported. Runs are stored as OpenTelemetry traces, analysed into a single
`analysis.json`, and drawn as trajectory trees in a browser viewer that sends nothing
anywhere.

By [Crucible Science](https://cruciblescience.com).

## Published notes

| # | Note | Data | Published |
|---|---|---|---|
| 001 | [Same customer, same request, four runs](experiments/001-tau-retail/NOTE.md) | Sierra's τ²-bench retail results, 216 runs, 3 models | October 2026 · tag `001-tau-retail-v1` |

Each note lives in its own folder under `experiments/`, with its pre-registration,
analysis, exhibits and the scripts to rebuild it. The run files are not stored here: the
importer generates them from the source data. A note is frozen at its tag; the engine
keeps evolving.

## Check our work in 90 seconds

1. Open [the task 37 exhibit](experiments/001-tau-retail/exhibits/task37_pair.md). It
   quotes the raw lookup and the agent's claim, with turn numbers, and gives the one
   command that pulls the same lines from Sierra's own results file.
2. Open [`experiment.yaml`](experiments/001-tau-retail/experiment.yaml): the hypotheses,
   their pass and kill thresholds, and the task-selection rule, as written before the
   confirmation models were scored. Compare them with the verdicts in the note.
3. Open [`manifest.json`](experiments/001-tau-retail/manifest.json): the commit and
   sha256 of every input file, so you know exactly which data the numbers come from.

To rebuild every number yourself, see
[Reproduce it](experiments/001-tau-retail/README.md#reproduce-it) (about ten minutes).

## See the trajectory trees

```
git clone https://github.com/crucible-science-research-lab/divergence
cd divergence
python -m http.server
```

Open `http://localhost:8000/viewer/?exp=001-tau-retail`. Each tree shows one task on one
model: every run enters on the left, branches split where runs call a different tool,
and endings show pass or fail. Click two runs to compare them step by step.

## How it is built

```
divergence/
  importers/tau.py    τ²-bench results  →  one OpenTelemetry JSON trace per run
  analyse.py          traces            →  analysis.json (trees, splits, costs, hypotheses)
  trace.py            the trace format shared by importers and our own harness
viewer/index.html     analysis.json     →  trajectory trees, one self-contained page
experiments/<slug>/   one folder per published note
```

The analysis never knows where a run came from. A new source needs only an importer
that writes, on each run's root span: `divergence.run_id`, `divergence.task_id`,
`divergence.run_index`, `divergence.trajectory` (tool names joined by `|`),
`divergence.termination`, `divergence.turns` and `gen_ai.request.model`, plus
`divergence.verdict` (`pass` or `fail`) and `divergence.cost_usd` when they are known.

Requirements: Python 3.10 or later, with PyYAML (`pip install pyyaml`).

## Run it on your own agent

If you run a support, collections or other customer-facing agent, send us redacted traces
of the same requests handled several times, and we will show you your trajectory trees.
Crucible Science sells this analysis applied to a client's own agent; every funded
analysis names who paid for it. ashwin@cruciblescience.com

## Licence

Code: MIT ([LICENSE](LICENSE)). Notes and exhibits: CC BY 4.0. Data in each experiment is
derived from its named source under that source's licence; see
[THIRD_PARTY.md](THIRD_PARTY.md).