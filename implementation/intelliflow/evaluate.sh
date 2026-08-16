#!/bin/bash
# IntelliFlow Evaluation Script
# Usage: ./evaluate.sh {intelliflow|baseline} [duration]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DURATION=${2:-30}
RESULTS_DIR="${SCRIPT_DIR}/results"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "${RESULTS_DIR}"

MODE=${1:-help}

print_usage() {
    echo "Usage: $0 {intelliflow|baseline} [duration_seconds]"
    echo ""
    echo "This script runs inside Mininet CLI. Start from host h1:"
    echo "  h1 bash evaluate.sh intelliflow 30"
    echo ""
    echo "Or run the full experiment manually:"
    echo "  1. Start controller:  ./run.sh controller  (or ./run.sh baseline)"
    echo "  2. Start mininet:     ./run.sh mininet"
    echo "  3. In mininet CLI:"
    echo "     h2 iperf3 -s &"
    echo "     h1 bash evaluate.sh intelliflow 30"
    echo ""
    echo "Results saved to: ${RESULTS_DIR}/"
}

run_experiment() {
    local name=$1
    local result_file="${RESULTS_DIR}/${name}_${TIMESTAMP}.json"
    
    echo "========================================"
    echo "Running ${name} experiment"
    echo "Duration: ${DURATION}s"
    echo "Results: ${result_file}"
    echo "========================================"
    echo ""
    
    # Test 1: Steady traffi
    echo "[Phase 1/3] Steady traffic (10 Mbps for 10s)..."
    iperf3 -c 10.0.0.2 -u -b 10M -t 10 -i 1 --json > "${RESULTS_DIR}/${name}_steady_${TIMESTAMP}.json" 2>/dev/null || true
    sleep 2
    
    # Test 2: Bursty traffic
    echo "[Phase 2/3] Bursty traffic (60 Mbps bursts)..."  
    for i in 1 2 3; do
        echo "  Burst $i/3..."
        iperf3 -c 10.0.0.2 -u -b 60M -t 3 -i 0.5 --json >> "${RESULTS_DIR}/${name}_burst_${TIMESTAMP}.json" 2>/dev/null || true
        sleep 3
    done
    sleep 2
    
    # Test 3: Sustained overload 
    echo "[Phase 3/3] Sustained overload (65 Mbps for ${DURATION}s, above Path A capacity)..."
    iperf3 -c 10.0.0.2 -u -b 65M -t ${DURATION} -i 1 --json > "${RESULTS_DIR}/${name}_sustained_${TIMESTAMP}.json" 2>/dev/null || true
    
    echo ""
    echo "Experiment complete. Parsing results..."
    
    # Parse and summarize
    python3 "${SCRIPT_DIR}/parse_results.py" "${RESULTS_DIR}" "${name}" "${TIMESTAMP}" || echo "Could not parse results (install python3 with json support)"
}

case "${MODE}" in
    intelliflow|baseline|predictive)
        run_experiment "${MODE}"
        ;;
    help|*)
        print_usage
        ;;
esac
