#!/usr/bin/env bash
# experiments/001-tau-retail/fetch_raw.sh
#
# Downloads the four source files this experiment reads, from Sierra's τ²-bench
# repository at the pinned commit, and checks each against its sha256.
#
#   raw/results/<3 results files>        the published runs and their grades
#   raw/retail_tasks_5bfa7e37.json       the retail task set at the same commit
#                                        (used by the importer's task-validity check)
#
# Run from the repository root:   bash experiments/001-tau-retail/fetch_raw.sh
# Files land in experiments/001-tau-retail/raw/ (ignored by git). Nothing else is touched.

set -euo pipefail

COMMIT=5bfa7e37b36656b37dc6d022156be6563c1007f3
BASE="https://raw.githubusercontent.com/sierra-research/tau2-bench/${COMMIT}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW="${HERE}/raw"
mkdir -p "${RAW}/results"

# file name | sha256 (from manifest.json; tasks file pinned separately)
RESULTS=(
  "gpt-4.1-2025-04-14_retail_default_gpt-4.1-2025-04-14_4trials.json|5fc5b96ada0fe46a463eaed98d1bfed9947fe073bac052290162dae18d71394e"
  "claude-3-7-sonnet-20250219_retail_default_gpt-4.1-2025-04-14_4trials.json|ed41dbd18c080154156484e3a0122c095e324a11367a640d88e15956daed7b9d"
  "o4-mini-2025-04-16_retail_default_gpt-4.1-2025-04-14_4trials.json|7135f38bbbbd46d6babe830574c8623bb276d8fd6da316580b905cf513853f98"
)
TASKS_FILE="retail_tasks_${COMMIT:0:8}.json"
TASKS_SHA256=""   # pinned after the first verified download

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

fetch() {   # url, destination, expected sha256 (may be empty)
  local url="$1" dest="$2" want="$3" got
  if [[ -f "$dest" && -n "$want" && "$(sha256 "$dest")" == "$want" ]]; then
    echo "ok (cached)  $(basename "$dest")"; return 0
  fi
  echo "fetching     $(basename "$dest")"
  curl -fsSL --retry 3 -o "${dest}.part" "$url"
  if head -c 40 "${dest}.part" | grep -q "git-lfs"; then
    rm -f "${dest}.part"
    echo "ERROR: $(basename "$dest") came back as a Git LFS pointer, not the file." >&2
    echo "       Download it from the GitHub web page for commit ${COMMIT:0:8} instead." >&2
    exit 1
  fi
  got="$(sha256 "${dest}.part")"
  if [[ -n "$want" && "$got" != "$want" ]]; then
    rm -f "${dest}.part"
    echo "ERROR: sha256 mismatch for $(basename "$dest")" >&2
    echo "       expected $want" >&2
    echo "       got      $got" >&2
    exit 1
  fi
  mv "${dest}.part" "$dest"
  if [[ -n "$want" ]]; then echo "ok           $(basename "$dest")"
  else echo "fetched      $(basename "$dest")  sha256 $got  (not yet pinned)"; fi
}

for entry in "${RESULTS[@]}"; do
  name="${entry%%|*}"; want="${entry##*|}"
  fetch "${BASE}/data/tau2/results/final/${name}" "${RAW}/results/${name}" "$want"
done
fetch "${BASE}/data/tau2/domains/retail/tasks.json" "${RAW}/${TASKS_FILE}" "$TASKS_SHA256"

echo
echo "All source files in ${RAW}. Next, from the repository root:"
echo "  python divergence/importers/tau.py experiments/001-tau-retail --clean"
echo "  python divergence/analyse.py experiments/001-tau-retail"