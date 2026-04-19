#!/usr/bin/env bash
# Run all anomaly scenarios through the Validance engine via simulation_loop.py.
# Each scenario: 1 training cycle + 2 inference cycles = 25 task executions per scenario.
#
# Usage: bash scripts/run_all_scenarios.sh [cycles]
# Default: 2 inference cycles per scenario
set -euo pipefail

CYCLES="${1:-2}"
API_URL="https://api.validance.io"
COST_MODEL="data/cost_model.json"
BASE_DIR="data/validance_run/scenarios"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

SCENARIOS=(psu_degradation cooling_failure summer_heatwave asic_aging)
PASSED=0
FAILED=0
RESULTS=()

echo "===== Running ${#SCENARIOS[@]} anomaly scenarios (${CYCLES} inference cycles each) ====="
echo ""

for scenario in "${SCENARIOS[@]}"; do
    OUT_DIR="${BASE_DIR}/${scenario}"
    mkdir -p "$OUT_DIR"

    echo "──── ${scenario} ────"
    echo "  Output: ${OUT_DIR}"
    START=$(date +%s)

    if python3 scripts/simulation_loop.py \
        --scenario "data/scenarios/${scenario}.json" \
        --cycles "$CYCLES" \
        --api-url "$API_URL" \
        --output-dir "$OUT_DIR" \
        --cost-model "$COST_MODEL" 2>&1 | tee "${OUT_DIR}/run.log"; then
        END=$(date +%s)
        ELAPSED=$((END - START))
        echo "  Result: PASSED (${ELAPSED}s)"
        RESULTS+=("${scenario}: PASSED (${ELAPSED}s)")
        PASSED=$((PASSED + 1))
    else
        END=$(date +%s)
        ELAPSED=$((END - START))
        echo "  Result: FAILED (${ELAPSED}s)"
        RESULTS+=("${scenario}: FAILED (${ELAPSED}s)")
        FAILED=$((FAILED + 1))
    fi
    echo ""
done

echo "===== Summary ====="
for r in "${RESULTS[@]}"; do
    echo "  $r"
done
echo ""
echo "Passed: ${PASSED}/${#SCENARIOS[@]}"
echo "Failed: ${FAILED}/${#SCENARIOS[@]}"

# Exit with failure if any scenario failed
[ "$FAILED" -eq 0 ]
