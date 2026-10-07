# Third-party material

## This repository's own licences

- Code (`divergence/`, `viewer/`, scripts): MIT, see [LICENSE](LICENSE).
- Notes and exhibits (`experiments/*/NOTE.md`, `experiments/*/exhibits/`, READMEs):
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## τ²-bench (Sierra Research), used in experiment 001-tau-retail

The run files the importer writes to `experiments/001-tau-retail/runs/` (generated, not
stored here), the analysis built from them, and the quotations in the note and exhibits,
are derived from τ²-bench's published retail baseline results:

- Repository: https://github.com/sierra-research/tau2-bench
- Commit: 5bfa7e37b36656b37dc6d022156be6563c1007f3
- Files: `data/tau2/results/final/` (three retail results files) and
  `data/tau2/domains/retail/tasks.json`; names and sha256 in
  `experiments/001-tau-retail/manifest.json` and `fetch_raw.sh`

We converted each run to an OpenTelemetry trace and truncated long tool results to
4,000 characters; we did not alter grades. Neither the original files nor the converted
runs are included here; `fetch_raw.sh` downloads the originals and the importer rebuilds
the runs.

τ²-bench is distributed under the MIT License, reproduced below as it appears in
that repository's `LICENSE` file at the commit above.

```
MIT License

Copyright (c) 2025 Sierra Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

```

## Terminal-Bench 2.0 trials (Harbor) and tasks (Laude Institute), used in experiment 002-terminal-bench

The reports in `experiments/002-terminal-bench/reports/` and `checks/`, the run files the
importer generates (not stored here), and the quotations in the note and its exhibits are
derived from:

- Trials: https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard,
  revision 572b2614be2c0cb2527e14f5b1e4026f1072e6c1, Apache License 2.0. The runs and
  their rewards are the submitters' and Harbor's. File names, sizes and sha256 are in
  `experiments/002-terminal-bench/manifest.tsv`.
- Tasks: https://github.com/laude-institute/terminal-bench-2, commit
  2fd12b88aafdd04a52c298e3940bcb189f9766d6, Apache License 2.0. Read by `hero_verify.py`
  and by the task-repository comparison in `checks/`, which also reads
  https://github.com/harbor-framework/terminal-bench-2-1 at commit
  7131e4375048a0e408a8fb404b5f499d726b695b.

Neither the trial files nor the task files are copied into this repository; `fetch.py`
downloads the trials from the source. We converted each trial's step log to an
OpenTelemetry trace, truncated long tool results to 4,000 characters and labelled each
tool call with an action category; we did not alter the recorded rewards. The exhibits
quote short excerpts of the trial logs and of one task's instructions.

The Apache License 2.0 is at https://www.apache.org/licenses/LICENSE-2.0.
