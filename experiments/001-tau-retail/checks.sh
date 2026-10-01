#!/usr/bin/env bash
# experiments/001-tau-retail/checks.sh
#
# Recomputes the numbers in NOTE.md that are not stored directly in analysis.json.
# Each check prints what it computed next to what the note says.
#
# Needs: jq (macOS: brew install jq; Debian/Ubuntu: apt install jq).
# Run from the repository root, after fetch_raw.sh, tau.py and analyse.py:
#   bash experiments/001-tau-retail/checks.sh

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
A="${HERE}/analysis.json"
R="${HERE}/raw/results"
command -v jq >/dev/null || { echo "jq is required (brew install jq / apt install jq)" >&2; exit 1; }
[[ -f "$A" ]] || { echo "missing $A: run analyse.py first" >&2; exit 1; }

echo "== 1. Tasks that flip across all 114 retail tasks (section 3.1) =="
echo "   note: Claude 3.7 38 of 114 · GPT-4.1 43 of 114 · o4-mini 52 of 114"
for f in claude-3-7-sonnet-20250219 gpt-4.1-2025-04-14 o4-mini-2025-04-16; do
  file="${R}/${f}_retail_default_gpt-4.1-2025-04-14_4trials.json"
  [[ -f "$file" ]] || { echo "   missing $file: run fetch_raw.sh first" >&2; exit 1; }
  jq -r --arg m "$f" '
    ([.simulations | group_by(.task_id)[] | map((.reward_info.reward // 0) >= 1)
      | select(any and (all | not))] | length) as $flip
    | ([.simulations[].task_id] | unique | length) as $all
    | "   \($m): \($flip) of \($all)"' "$file"
done

echo
echo "== 2. Shared tool steps before two runs part (section 3.4, H3) =="
echo "   note, all pairs:          pass/fail 4.75 (61) · pass/pass 6.1 (31) · fail/fail 16 pairs"
echo "   note, identical removed:  pass/fail 4.65 (60) · pass/pass 5.33 (21) · fail/fail 2.91 (11)"
jq -r '
  def firstdiff($x; $y):
    ([$x, $y] | map(length) | min) as $m
    | ([range(0; $m) | select($x[.] != $y[.])] | first) as $d
    | if $d != null then $d elif ($x | length) == ($y | length) then ($x | length) + 1 else $m end;
  [ .cells[]
    | select(.summary.model_role == "confirmation" and .summary.outcome_flip
             and .summary.task_validity != "grader_suspect")
    | .runs as $r
    | range(0; $r | length) as $i | range($i + 1; $r | length) as $j
    | [$r[$i], $r[$j]]
    | { k: (map(.verdict) | sort | join("/") | if . == "fail/pass" then "pass/fail" else . end),
        same: (.[0].trajectory == .[1].trajectory),
        d: firstdiff(.[0].trajectory; .[1].trajectory) } ] as $p
  | def summ($s): $s | group_by(.k) | map("\(.[0].k) \((map(.d) | add / length * 100 | round) / 100) (\(length))") | join(" · ");
  "   computed, all pairs:         \(summ($p))  [\($p | length) pairs]",
  "   computed, identical removed: \(summ([$p[] | select(.same | not)]))"' "$A"

echo
echo "== 3. What the first split is (section 3.3, H2) =="
echo "   note: 32 cells · 13 lookup-vs-lookup (8 order vs profile) · 12 involve a write (9 act vs look up, 3 different operations) · 7 hand-off"
jq -r '
  ["find_user_id_by_email","find_user_id_by_name_zip","get_order_details","get_product_details",
   "get_user_details","list_all_product_types","calculate","think"] as $read
  | [ .cells[]
      | select(.summary.model_role == "confirmation" and .summary.first_split_kind != null)
      | .summary as $s
      | ($s.branch_points | map(select(.step == $s.first_split_step)) | first | .options | keys) as $o
      | ([$o[] | select((startswith("END:") | not) and (. as $t | $read | index($t) | not)
                        and . != "transfer_to_human_agents")] | length) as $writes
      | if ($o | index("transfer_to_human_agents")) then "hand-off"
        elif $writes == 0 then (if $o == ["get_order_details","get_user_details"] then "lookup (order vs profile)" else "lookup (other)" end)
        elif $writes >= 2 then "write (different operations)"
        else "write (act vs look up)" end ]
  | "   computed: \(length) cells",
    (group_by(.)[] | "     \(.[0]): \(length)")' "$A"

echo
echo "== 4. Cost (section 3.5, H4) =="
echo "   note: task 50 o4-mini \$0.00441 \$0.00613 \$0.02594 \$0.03153 (about 7x) · other same-outcome cells 1.06x-1.72x, median 1.28x"
echo "         task 36 Claude \$1.03 per resolved request vs \$0.52 per run · task 47 o4-mini \$0.054 vs \$0.041"
jq -r '
  def r($n; $p): ($n * pow(10; $p) | round) / pow(10; $p);
  def cell($id): .cells[] | select(.cell_id == $id);
  (cell("50__o4-mini-2025-04-16") | .runs | map(.cost_usd) | sort) as $c
  | "   computed: task 50 o4-mini \($c | map("$" + (r(.; 5) | tostring)) | join(" "))  ratio \(r($c[-1] / $c[0]; 2))x",
    ( [ .cells[] | select(.summary.model_role == "confirmation"
                          and (.summary.verdicts.pass == 0 or .summary.verdicts.fail == 0)
                          and .cell_id != "50__o4-mini-2025-04-16") | .summary.cost_ratio ] | sort
      | "   computed: other same-outcome cells \(.[0])x-\(.[-1])x, median \(.[(length - 1) / 2 | floor])x over \(length) cells" ),
    ( ["36__claude-3-7-sonnet-20250219", "47__o4-mini-2025-04-16"][] as $id
      | cell($id) | .runs as $r
      | ($r | map(.cost_usd) | add) as $sum
      | "   computed: \($id) $\(r($sum / ([$r[] | select(.verdict == "pass")] | length); 3)) per resolved request vs $\(r($sum / ($r | length); 3)) per run" )' "$A"