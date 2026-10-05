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