#!/usr/bin/env bash
# experiments/002-terminal-bench/run_all.sh
#
# Rebuild every number in note 002 from the published trials, then compare the result
# with the reports committed in reports/.
#
#   python experiments/002-terminal-bench/fetch.py      # once: download the inputs (0.91 GB)
#   bash experiments/002-terminal-bench/run_all.sh
#
# Needs Python 3 with pyyaml, pandas and duckdb (fetch.py also needs huggingface_hub).
# To use another interpreter: PYTHON=python3 bash experiments/002-terminal-bench/run_all.sh
#
# Everything written here is generated and ignored by git:
#   experiments/002-terminal-bench/out/           the database, the reports, logs/
#   experiments/002-terminal-bench-models/        runs/, experiment.yaml, analysis.json
#   experiments/002-terminal-bench-harnesses/     the same for the harness axis
# The check of the worked example (hero_facts.py, hero_verify.py) runs only if the task
# repository has been cloned into tasks/; the top of hero_verify.py says how.
#
# The exit code is 0 only if every step ran and every report matches its committed copy.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PYTHON:-python}
E=experiments/002-terminal-bench
LOGS=$E/out/logs
REPORTS="harbor_import_models_confirmation harbor_import_confirmation intervals dollars hero_shortlist"

command -v "$PY" > /dev/null || { echo "'$PY' not found; set PYTHON to your interpreter"; exit 1; }
if [ ! -e "$E/raw" ]; then
  echo "the inputs are not here yet. Download them first (0.91 GB):"
  echo "   $PY $E/fetch.py"
  exit 1
fi
mkdir -p "$LOGS"

step() {                       # step "label" logfile command...
  local label=$1 log=$LOGS/$2
  shift 2
  printf '%-64s' "$label"
  if "$@" > "$log" 2>&1; then
    echo "ok"
  else
    echo "FAILED"
    echo "--- last lines of $log"
    tail -n 8 "$log"
    exit 1
  fi
}

norm() {                       # a report without the run time on its first line
  sed -E '1s/, [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} UTC$//' "$1"
}

step "inputs: every file against manifest.tsv"            fetch_verify.log     "$PY" $E/fetch.py --verify
step "import and score, model axis"                       import_models.log    "$PY" divergence/importers/harbor.py $E --axis model --clean
step "import and score, harness axis"                     import_harnesses.log "$PY" divergence/importers/harbor.py $E --axis harness --clean
step "trajectory trees, model axis (for the viewer)"      trees_models.log     "$PY" divergence/analyse.py $E-models
step "trajectory trees, harness axis (for the viewer)"    trees_harnesses.log  "$PY" divergence/analyse.py $E-harnesses
step "intervals and the extra verdict lines"              intervals.log        "$PY" $E/intervals.py
step "dollars, recorded and estimated"                    dollars.log          "$PY" $E/dollars.py
step "shortlist for the worked example"                   hero_shortlist.log   "$PY" $E/hero_shortlist.py

bad=0
if [ -d "$E/tasks/sparql-university" ]; then
  step "worked example: the raw facts"                    hero_facts.log       "$PY" $E/hero_facts.py
  step "worked example: the verifier's comparison"        hero_verify.log      "$PY" $E/hero_verify.py
  last=$(tail -n 1 "$E/out/hero_verify.txt")
  echo "   $last"
  [ "$last" = "the verifier's comparison reproduces 10 of 10 recorded outcomes" ] || bad=$((bad + 1))
  if grep -q '!!' "$E/out/hero_verify.txt"; then
    echo "   warnings in $E/out/hero_verify.txt:"
    grep '!!' "$E/out/hero_verify.txt" | sed 's/^/   /'
  fi
else
  echo "worked example: skipped ($E/tasks not found; see the top of hero_verify.py)"
fi

echo
echo "comparing with the committed reports (the run time on line 1 is ignored):"
for f in $REPORTS; do
  if [ ! -f "$E/reports/$f.txt" ]; then
    printf '   %-46s %s\n' "$f.txt" "no committed copy in reports/"
    bad=$((bad + 1))
  elif diff <(norm "$E/reports/$f.txt") <(norm "$E/out/$f.txt") > "$LOGS/diff_$f.txt"; then
    printf '   %-46s %s\n' "$f.txt" "same"
  else
    printf '   %-46s %s\n' "$f.txt" "DIFFERS (see $LOGS/diff_$f.txt)"
    bad=$((bad + 1))
  fi
done

echo
if [ "$bad" -eq 0 ]; then
  echo "all reports match the committed copies"
else
  echo "$bad problem(s): see above"
  exit 1
fi