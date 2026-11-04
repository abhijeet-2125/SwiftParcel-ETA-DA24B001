import pytest
from partB_mlflow.eta_mlflow import decide

@pytest.fixture
def production_metrics():
    return {
        "mae": 10.0,
        "mae_rush": 15.0,
        "mae_rain": 12.0,
        "p95_ms": 5.0}

def test_candidate_passes_all_criteria(production_metrics):
    candidate = {
        "mae": 9.0,
        "mae_rush": 15.3,
        "mae_rain": 12.4,
        "p95_ms": 7.0}
    result = decide(production_metrics, candidate)
    assert result["promote"] is True

def test_insufficient_overall_improvement(production_metrics):
    candidate = {
        "mae": 9.8,
        "mae_rush": 15.0,
        "mae_rain": 12.0,
        "p95_ms": 7.0}
    result = decide(production_metrics, candidate)
    assert result["criteria"]["overall_mae"]["pass"] is False
    assert result["promote"] is False

def test_latency_just_above_limit(production_metrics):
    candidate = {
        "mae": 9.0,
        "mae_rush": 15.0,
        "mae_rain": 12.0,
        "p95_ms": 10.01}
    result = decide(production_metrics, candidate)
    assert result["criteria"]["latency"]["pass"] is False
    assert result["promote"] is False

def test_rush_hour_regression_just_above_limit(production_metrics):
    candidate = {
        "mae": 9.0,
        "mae_rush": 15.765,
        "mae_rain": 12.0,
        "p95_ms": 7.0}
    result = decide(production_metrics, candidate)
    assert result["criteria"]["rush_hour"]["pass"] is False
    assert result["promote"] is False

def test_rain_regression_just_above_limit(production_metrics):
    candidate = {
        "mae": 9.0,
        "mae_rush": 15.0,
        "mae_rain": 12.612,
        "p95_ms": 7.0}
    result = decide(production_metrics, candidate)
    assert result["criteria"]["rain"]["pass"] is False
    assert result["promote"] is False

def test_exact_threshold_boundaries_pass(production_metrics):
    candidate = {
        "mae": 9.7,       # Exactly 3% improvement
        "mae_rush": 15.75, # Exactly 5% regression
        "mae_rain": 12.6,  # Exactly 5% regression
        "p95_ms": 10.0     # Exactly 10 ms
    }

    result = decide(production_metrics, candidate)
    assert all(
        criterion["pass"]
        for criterion in result["criteria"].values()
    )
    assert result["promote"] is True


def test_custom_improvement_threshold(production_metrics):
    candidate = {
        "mae": 9.0,
        "mae_rush": 15.0,
        "mae_rain": 12.0,
        "p95_ms": 7.0
    }

    result = decide(
        production_metrics,
        candidate,
        min_rel_gain=0.15
    )

    assert result["criteria"]["overall_mae"]["pass"] is False
    assert result["promote"] is False