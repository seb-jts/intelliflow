#!/bin/bash
# IntelliFlow Quick Start Script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

# Activate virtual environment if it exists
if [ -f "${PROJECT_DIR}/.venv/bin/activate" ]; then
    source "${PROJECT_DIR}/.venv/bin/activate"
fi

# Add project to PYTHONPATH
export PYTHONPATH="${PROJECT_DIR}:${PYTHONPATH}"

# Log file location
LOG_FILE="${SCRIPT_DIR}/intelliflow.log"

echo "=== IntelliFlow Quick Start ==="
echo "Project directory: ${PROJECT_DIR}"
echo ""

case "${1:-help}" in
    controller)
        echo "Starting IntelliFlow controller..."
        echo "Logs: ${LOG_FILE}"
        osken-manager "${SCRIPT_DIR}/controller.py" --log-config-file "${SCRIPT_DIR}/logging.conf" 2>/dev/null || \
        osken-manager "${SCRIPT_DIR}/controller.py" --default-log-level 20
        ;;
    
    baseline)
        echo "Starting BASELINE controller (static routing, no adaptation)..."
        echo "Logs: ${LOG_FILE}"
        osken-manager "${SCRIPT_DIR}/baseline_controller.py" --log-config-file "${SCRIPT_DIR}/logging.conf" 2>/dev/null || \
        osken-manager "${SCRIPT_DIR}/baseline_controller.py" --default-log-level 20
        ;;
    
    predictive)
        echo "Starting PREDICTIVE-ONLY controller (LSTM path selection, no reactive layer)..."
        echo "Logs: ${LOG_FILE}"
        osken-manager "${SCRIPT_DIR}/predictive_controller.py" --log-config-file "${SCRIPT_DIR}/logging.conf" 2>/dev/null || \
        osken-manager "${SCRIPT_DIR}/predictive_controller.py" --default-log-level 20
        ;;
    
    mininet)
        echo "Starting Mininet with diamond topology..."
        echo "Note: Requires sudo"
        mn --custom "${SCRIPT_DIR}/topology.py" --topo diamondEval --controller=remote --link tc
        ;;
    
    clean)
        echo "Cleaning logs..."
        if [ -f "${LOG_FILE}" ]; then
            rm -f "${LOG_FILE}"
            echo "Removed ${LOG_FILE}"
        else
            echo "No log file found"
        fi
        # Also clean any Mininet remnants
        echo "Cleaning Mininet state..."
        mn -c 2>/dev/null || true
        echo "Done."
        ;;
    
    both)
        echo "Starting both controller and Mininet..."
        echo "Controller will run in background, Mininet in foreground"
        echo ""
        
        # Start controller in background
        osken-manager "${SCRIPT_DIR}/controller.py" --verbose &
        CONTROLLER_PID=$!
        echo "Controller started (PID: ${CONTROLLER_PID})"
        
        # Give controller time to start
        sleep 2
        
        # Start Mininet (foreground)
        sudo mn --custom "${SCRIPT_DIR}/topology.py" --topo diamondEval --controller=remote --link tc
        
        # Cleanup
        echo "Stopping controller..."
        kill ${CONTROLLER_PID} 2>/dev/null || true
        ;;
    
    test)
        echo "Running basic connectivity test..."
        echo "This requires both controller and Mininet to be running."
        echo ""
        echo "In Mininet CLI, run:"
        echo "  h2 iperf3 -s &"
        echo "  h1 iperf3 -c 10.0.0.2 -u -b 10M -t 10"
        ;;
    
    help|*)
        echo "Usage: $0 {controller|baseline|predictive|mininet|both|clean|test|help}"
        echo ""
        echo "Commands:"
        echo "  controller  Start IntelliFlow controller (adaptive)"
        echo "  baseline    Start baseline controller (static routing, for comparison)"
        echo "  predictive  Start predictive-only controller (LSTM, no reactive layer)"
        echo "  mininet     Start Mininet topology only (requires sudo)"
        echo "  both        Start controller (background) + Mininet (foreground)"
        echo "  clean       Remove log files and clean Mininet state"
        echo "  test        Print test instructions"
        echo "  help        Show this help"
        echo ""
        echo "Recommended workflow:"
        echo "  Terminal 1: $0 controller    # or '$0 baseline' / '$0 predictive'"
        echo "  Terminal 2: $0 mininet"
        echo ""
        echo "In Mininet CLI:"
        echo "  pingall                        # Test connectivity"
        echo "  h2 iperf3 -s &                 # Start server"
        echo "  h1 bash traffic/bursty.sh     # Generate bursty traffic"
        echo ""
        echo "Evaluation (run experiments and compare):"
        echo "  # Run with IntelliFlow first:"
        echo "  Terminal 1: $0 controller"
        echo "  Terminal 2: $0 mininet"
        echo "  mininet> h2 iperf3 -s &"
        echo "  mininet> h1 bash evaluate.sh intelliflow"
        echo ""
        echo "  # Then run with Baseline:"
        echo "  Terminal 1: $0 baseline"
        echo "  Terminal 2: $0 mininet"
        echo "  mininet> h2 iperf3 -s &"
        echo "  mininet> h1 bash evaluate.sh baseline"
        echo ""
        echo "  # Compare results:"
        echo "  python3 parse_results.py results compare"
        ;;
esac
