#!/usr/bin/env bash
#created for video demonstration of my SwiftParcel project
set -Eeuo pipefail

cd "$(dirname "$0")"

trap 'status=$?; echo; echo "❌ VIDEO CHECK RUN FAILED (exit code: $status)"; exit "$status"' ERR

echo
echo "          SWIFTPARCEL - FINAL VIDEO TEST CHECKS"
echo "Running from: $(pwd)"
echo


echo " [PRE-FLIGHT] Checking required services..."

if ! curl -fsS http://127.0.0.1:8000/healthz >/dev/null; then
    echo "❌ Part A API is not running on port 8000."
    echo "Start the Part A API before running this script."
    exit 1
fi

echo "✅ Part A API is running."

if ! curl -fsS http://127.0.0.1:8002/healthz >/dev/null; then
    echo "❌ Ray Serve is not running on port 8002."
    echo "Start the Ray Serve application before running this script."
    exit 1
fi

echo "✅ Ray Serve is running."

#part B

echo
echo " PART B - MLFLOW MODEL REGISTRY & PROMOTION GATE"

echo
echo "[B2] Running promotion-gate unit tests..."
python -m pytest partB_mlflow/test_gate.py -v

echo
echo "[B2] Evaluating default and strict promotion thresholds..."
python -m partB_mlflow.eta_mlflow

echo
echo "[B3] Previously recorded production vs candidate comparison:"
cat partB_mlflow/results/server_comparison.json

echo
echo "✅ PART B CHECKS COMPLETED."


#part c about ray
echo
echo " PART C - RAY SERVE & STICKY CANARY ROUTING"

echo
echo "[C2] Running all five Ray Serve verification tests..."

python -m partC_ray_serve.verify_ray 2>&1 | grep --line-buffered -E \
'TEST [1-5]|PASS:|VERIFICATION RESULT|ALL FIVE|ERROR|Traceback|AssertionError|Deployment healthy|Restoring|Candidate allocation|Consistent assignments|Assignments preserved|Customers moved|Beta override'

echo
echo "✅ PART C VERIFICATION COMPLETED."


#part d about production smoke test
echo
echo " PART D - RELEASE SMOKE TEST"

echo
echo "[D1] Running production API smoke tests..."

python partD_release_gate/smoke_eta.py \
    --url http://127.0.0.1:8000 \
    --expect-version v1 \
    --max-p95-ms 80

echo
echo "✅ PART D SMOKE TEST COMPLETED."


#final summary
echo
echo "             ALL VIDEO CHECKS COMPLETED"
echo
echo "Part B: MLflow tests and promotion-gate evaluation"
echo "Part C: Ray Serve canary-routing verification"
echo "Part D: Production API smoke tests"
echo
echo "✅ No test failure was reported."
echo "============================================================"