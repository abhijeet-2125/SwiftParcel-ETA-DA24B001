import argparse
import json
import statistics
import sys
import time
import requests


def parse_args():
    parser = argparse.ArgumentParser(
        description="SwiftParcel ETA release smoke test"
    )
    parser.add_argument("--url", required=True, help="Base URL of ETA service")
    parser.add_argument(
        "--expect-version",
        required=True,
        help="Expected model version, for example v1",
    )
    parser.add_argument(
        "--max-p95-ms",
        type=float,
        default=80.0,
        help="Maximum allowed p95 latency in milliseconds",
    )
    return parser.parse_args()


def percentile(values, p):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((p / 100) * len(ordered) + 0.999999) - 1))
    return ordered[index]


def main():
    args = parse_args()
    base_url = args.url.rstrip("/")
    predict_url = f"{base_url}/v1/models/eta:predict"
    health_url = f"{base_url}/healthz"

    checks = []

    def record(name, passed, detail):
        checks.append({
            "check": name,
            "passed": bool(passed),
            "detail": str(detail),
        })
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}: {detail}")

    # Valid delivery instance.
    valid_instance = {
        "distance_km": 12.5,
        "n_stops": 3,
        "hour": 10,
        "weekday": 2,
        "traffic_index": 0.6,
        "rain": 0,
        "weight_kg": 5.0,
    }

    # 1. Health check.
    try:
        response = requests.get(health_url, timeout=5)
        record(
            "Health endpoint",
            response.status_code == 200,
            f"HTTP {response.status_code}",
        )
    except requests.RequestException as exc:
        record("Health endpoint", False, exc)

    # 2. Valid prediction and expected model version.
    prediction = None
    try:
        response = requests.post(
            predict_url,
            json={"instances": [valid_instance]},
            timeout=10,
        )
        response.raise_for_status()
        prediction = response.json()

        version_ok = prediction.get("model_version") == args.expect_version
        record(
            "Expected model version",
            version_ok,
            f"expected={args.expect_version}, actual={prediction.get('model_version')}",
        )
    except (requests.RequestException, ValueError) as exc:
        record("Expected model version", False, exc)

    # 3. Prediction is present, finite, and positive.
    try:
        eta = float(prediction["predictions"][0])
        sanity_ok = eta > 0 and eta < 24 * 60 and eta == eta
        record(
            "ETA sanity",
            sanity_ok,
            f"predicted ETA={eta:.1f} minutes",
        )
    except (TypeError, KeyError, IndexError, ValueError):
        record("ETA sanity", False, "Missing or invalid prediction value")

    # 4. A longer distance should not produce a shorter ETA
    # for otherwise identical input.
    try:
        short_trip = dict(valid_instance, distance_km=5.0)
        long_trip = dict(valid_instance, distance_km=25.0)

        short_response = requests.post(
            predict_url,
            json={"instances": [short_trip]},
            timeout=10,
        )
        long_response = requests.post(
            predict_url,
            json={"instances": [long_trip]},
            timeout=10,
        )

        short_response.raise_for_status()
        long_response.raise_for_status()

        short_eta = float(short_response.json()["predictions"][0])
        long_eta = float(long_response.json()["predictions"][0])

        record(
            "Distance monotonicity sanity",
            long_eta >= short_eta,
            f"5 km={short_eta:.1f} min, 25 km={long_eta:.1f} min",
        )
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        record("Distance monotonicity sanity", False, exc)

    # 5. Invalid input must be rejected with HTTP 422.
    try:
        invalid_instance = dict(valid_instance, distance_km=-10)
        response = requests.post(
            predict_url,
            json={"instances": [invalid_instance]},
            timeout=10,
        )
        record(
            "Invalid input rejection",
            response.status_code == 422,
            f"HTTP {response.status_code}",
        )
    except requests.RequestException as exc:
        record("Invalid input rejection", False, exc)

    # 6. Measure p95 latency over 100 one-row requests.
    latencies_ms = []
    latency_errors = 0

    for _ in range(100):
        start = time.perf_counter()
        try:
            response = requests.post(
                predict_url,
                json={"instances": [valid_instance]},
                timeout=10,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

            if response.status_code == 200:
                latencies_ms.append(elapsed_ms)
            else:
                latency_errors += 1
        except requests.RequestException:
            latency_errors += 1

    if latencies_ms:
        p95_ms = percentile(latencies_ms, 95)
        latency_ok = p95_ms <= args.max_p95_ms and latency_errors == 0
        record(
            "Latency p95",
            latency_ok,
            f"p95={p95_ms:.2f} ms, limit={args.max_p95_ms:.2f} ms, errors={latency_errors}",
        )
    else:
        record("Latency p95", False, "No successful latency measurements")

    # Final summary.
    passed = sum(item["passed"] for item in checks)
    failed = len(checks) - passed

    print("\n========== RELEASE SMOKE TEST ==========")
    print(f"Checks passed: {passed}/{len(checks)}")
    print(f"Checks failed: {failed}/{len(checks)}")

    print(json.dumps(checks, indent=2))

    if failed:
        print("\nRELEASE GATE: FAIL")
        sys.exit(1)

    print("\nRELEASE GATE: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
