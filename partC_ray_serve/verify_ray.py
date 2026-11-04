from __future__ import annotations
import logging
import time
import requests
import ray
from ray import serve
from partC_ray_serve.eta_ray import build

BASE_URL = "http://127.0.0.1:8002"
PREDICT_URL = f"{BASE_URL}/v1/models/eta:predict"
HEALTH_URL = f"{BASE_URL}/healthz"
INITIAL_WEIGHT = 0.20
INCREASED_WEIGHT = 0.50
NUM_CUSTOMERS = 1000
STICKINESS_SAMPLE_SIZE = 200
BETA_SAMPLE_SIZE = 50
REQUEST_TIMEOUT = 30
HEALTH_TIMEOUT = 90
FEATURES = {
    "distance_km": 12.5,
    "n_stops": 3,
    "hour": 10,
    "weekday": 2,
    "traffic_index": 0.6,
    "rain": 0,
    "weight_kg": 5.0,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("swiftparcel.verification")

#http helper
def predict(customer_id: str, beta: bool = False) -> dict:
    payload = {
        "customer_id": customer_id,
        "instances": [FEATURES],
    }

    headers = {"X-Beta": "1"} if beta else {}
    response = requests.post(
        PREDICT_URL,
        json=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    result = response.json()
    if "arm" not in result or "model_version" not in result:
        raise AssertionError(f"Unexpected prediction response: {result}")
    return result

def get_health() -> dict:
    response = requests.get(HEALTH_URL,timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()

def wait_until_healthy(timeout: int = HEALTH_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            health = get_health()
            if health.get("status") == "healthy":
                return health
        except requests.RequestException as error:
            last_error = error
        time.sleep(2)
    raise RuntimeError(
        f"Ray Serve did not become healthy within {timeout}s. "
        f"Last error: {last_error}")


#deploy helper
def redeploy(weight: float) -> None:
    logger.info(
        "Redeploying application with candidate weight %.0f%%",
        weight * 100,
    )

    application = build(
        weight=weight,
        extra_work_ms=0.0,
    )

    serve.run(
        application,
        name="swiftparcel_eta",
        route_prefix="/",
    )

    health = wait_until_healthy()

    actual_weight = float(health["candidate_weight"])

    if abs(actual_weight - weight) > 1e-9:
        raise AssertionError(
            f"Expected candidate weight {weight}, "
            f"received {actual_weight}"
        )

    logger.info(
        "Deployment healthy. Candidate weight: %.0f%%",
        actual_weight * 100,
    )


def test_candidate_allocation() -> dict[str, str]:
    """
    Test 1:
    Verify that the candidate receives approximately the
    configured percentage of distinct customers.
    """
    logger.info("TEST 1: Candidate traffic allocation")
    assignments = {}

    for index in range(NUM_CUSTOMERS):
        customer_id = f"allocation-customer-{index}"

        result = predict(customer_id)

        assignments[customer_id] = result["arm"]

    candidate_count = sum(
        arm == "v2"
        for arm in assignments.values()
    )

    candidate_share = candidate_count / NUM_CUSTOMERS

    lower_bound = INITIAL_WEIGHT - 0.04
    upper_bound = INITIAL_WEIGHT + 0.04

    logger.info(
        "Candidate allocation: %d/%d = %.2f%%",
        candidate_count,
        NUM_CUSTOMERS,
        candidate_share * 100,
    )

    logger.info(
        "Allowed range: %.0f%%–%.0f%%",
        lower_bound * 100,
        upper_bound * 100,
    )

    assert lower_bound <= candidate_share <= upper_bound, (
        f"Candidate allocation {candidate_share:.2%} "
        f"is outside the allowed range."
    )

    logger.info("PASS: Candidate allocation is within tolerance.")

    return assignments


def test_customer_stickiness() -> None:
    """
    Test 2:
    Verify that repeated requests from the same customer
    always receive the same model.
    """
    logger.info("TEST 2: Customer-level routing stickiness")
    customer_ids = [
        f"sticky-customer-{index}"
        for index in range(STICKINESS_SAMPLE_SIZE)
    ]

    first_assignments = {
        customer_id: predict(customer_id)["arm"]
        for customer_id in customer_ids
    }

    second_assignments = {
        customer_id: predict(customer_id)["arm"]
        for customer_id in customer_ids
    }

    consistent = sum(
        first_assignments[customer_id]
        == second_assignments[customer_id]
        for customer_id in customer_ids
    )

    logger.info(
        "Consistent assignments: %d/%d",
        consistent,
        STICKINESS_SAMPLE_SIZE,
    )

    assert consistent == STICKINESS_SAMPLE_SIZE, (
        "Customer routing changed between repeated requests."
    )

    logger.info("PASS: Customer assignments are sticky.")


def test_redeployment_stickiness() -> None:
    """
    Test 3:
    Verify that identical redeployment preserves customer
    assignments at the same canary weight.
    """
    logger.info("TEST 3: Stickiness after identical redeployment")
    customer_ids = [
        f"redeploy-customer-{index}"
        for index in range(STICKINESS_SAMPLE_SIZE)
    ]

    before = {
        customer_id: predict(customer_id)["arm"]
        for customer_id in customer_ids
    }

    redeploy(INITIAL_WEIGHT)

    after = {
        customer_id: predict(customer_id)["arm"]
        for customer_id in customer_ids
    }

    consistent = sum(
        before[customer_id] == after[customer_id]
        for customer_id in customer_ids
    )

    logger.info(
        "Assignments preserved: %d/%d",
        consistent,
        STICKINESS_SAMPLE_SIZE,
    )

    assert consistent == STICKINESS_SAMPLE_SIZE, (
        "Customer assignments changed after identical redeployment."
    )

    logger.info(
        "PASS: Identical redeployment preserved customer assignments."
    )


def test_monotonicity() -> None:
    """
    Test 4:
    Increase candidate weight from 20% to 50%.
    Customers already assigned to candidate must remain
    assigned to candidate.
    """
    logger.info("TEST 4: Canary weight monotonicity")
    customer_ids = [
        f"monotonic-customer-{index}"
        for index in range(NUM_CUSTOMERS)
    ]

    before = {
        customer_id: predict(customer_id)["arm"]
        for customer_id in customer_ids
    }

    previous_candidate_customers = {
        customer_id
        for customer_id, arm in before.items()
        if arm == "v2"
    }

    redeploy(INCREASED_WEIGHT)

    after = {
        customer_id: predict(customer_id)["arm"]
        for customer_id in customer_ids
    }

    moved_back = [
        customer_id
        for customer_id in previous_candidate_customers
        if after[customer_id] != "v2"
    ]

    logger.info(
        "Previously assigned candidate customers: %d",
        len(previous_candidate_customers),
    )

    logger.info(
        "Customers moved from candidate back to production: %d",
        len(moved_back),
    )

    assert not moved_back, (
        f"{len(moved_back)} existing candidate customers "
        "were moved back to production."
    )

    logger.info(
        "PASS: Increasing candidate weight preserved all "
        "previous candidate assignments."
    )


def test_beta_override() -> None:
    """
    Test 5:
    Verify that X-Beta: 1 forces every request to candidate.
    """
    logger.info("TEST 5: Beta header override")
    successful = 0

    for index in range(BETA_SAMPLE_SIZE):
        customer_id = f"beta-customer-{index}"

        result = predict(
            customer_id,
            beta=True,
        )

        assert result["arm"] == "v2", (
            f"Beta override failed for {customer_id}: "
            f"received arm {result['arm']}"
        )

        assert result["model_version"] == "v2", (
            f"Expected model version v2 for {customer_id}, "
            f"received {result['model_version']}"
        )

        assert result.get("routing_reason") == "beta_override", (
            f"Unexpected routing reason: {result.get('routing_reason')}"
        )

        successful += 1

    logger.info(
        "Beta override successful: %d/%d",
        successful,
        BETA_SAMPLE_SIZE,
    )

    logger.info("PASS: Beta header forces candidate routing.")


def main() -> None:
    logger.info("=" * 65)
    logger.info("SWIFTPARCEL RAY SERVE — PART C2 VERIFICATION")
    logger.info("=" * 65)

    if not ray.is_initialized():
        ray.init(address="auto")

    passed_tests = 0

    try:
        # Confirm that the existing application is healthy.
        health = wait_until_healthy()

        logger.info("Initial application health: %s", health)

        # Begin with the required 20% candidate allocation.
        redeploy(INITIAL_WEIGHT)

        # Test 1: Candidate allocation.
        test_candidate_allocation()
        passed_tests += 1

        # Test 2: Repeated-request stickiness.
        test_customer_stickiness()
        passed_tests += 1

        # Test 3: Identical redeployment stickiness.
        test_redeployment_stickiness()
        passed_tests += 1

        # Test 4: Monotonicity when increasing candidate weight.
        test_monotonicity()
        passed_tests += 1

        # Test 5: Explicit beta override.
        test_beta_override()
        passed_tests += 1

    finally:
        # Restore the initial 20% allocation even if a test fails.
        logger.info("Restoring candidate allocation to 20%.")

        try:
            redeploy(INITIAL_WEIGHT)
        except Exception:
            logger.exception(
                "WARNING: Could not restore the initial deployment weight."
            )

    logger.info("=" * 65)
    logger.info(
        "VERIFICATION RESULT: %d/5 TESTS PASSED",
        passed_tests,
    )

    if passed_tests == 5:
        logger.info("ALL FIVE PART C2 VERIFICATION TESTS PASSED.")
        logger.info("=" * 65)
    else:
        raise AssertionError(
            f"Only {passed_tests}/5 Part C2 tests passed."
        )


if __name__ == "__main__":
    main()
