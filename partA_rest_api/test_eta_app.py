import pytest
from fastapi.testclient import TestClient
from partA_rest_api import eta_app

@pytest.fixture
def client():
    with eta_app.METRICS_LOCK:
        eta_app.REQUEST_COUNTS.clear()
        for bucket in eta_app.LATENCY_BUCKETS:
            eta_app.LATENCY_COUNTS[bucket] = 0
        eta_app.LATENCY_COUNTS["+Inf"] = 0
        eta_app.LATENCY_SUM = 0.0
        eta_app.LATENCY_TOTAL = 0
    with TestClient(eta_app.app) as test_client:
        yield test_client

@pytest.fixture
def valid_delivery():
    return {"distance_km": 25,
        "n_stops": 5,
        "hour": 10,
        "weekday": 2,
        "traffic_index": 0.4,
        "rain": 0,
        "weight_kg": 15}

def test_valid_prediction(client, valid_delivery):
    response = client.post(
        "/v1/models/eta:predict",
        json={"instances": [valid_delivery]})
    assert response.status_code == 200
    body = response.json()
    assert len(body["predictions"]) == 1
    assert isinstance(body["predictions"][0], (int, float))
    assert round(body["predictions"][0], 1) == body["predictions"][0]
    assert body["model_version"] == "v1"
    assert "request_id" in body

def test_batch_order_preserved(client, valid_delivery, monkeypatch):
    class DummyModel:
        def predict(self, dataframe):
            return [10.0, 20.0, 30.0]
    monkeypatch.setattr(eta_app, "model", DummyModel())
    deliveries = []
    for distance in [10, 20, 30]:
        row = valid_delivery.copy()
        row["distance_km"] = distance
        deliveries.append(row)
    response = client.post(
        "/v1/models/eta:predict",
        json={"instances": deliveries})
    assert response.status_code == 200
    assert response.json()["predictions"] == [10.0, 20.0, 30.0]

def test_invalid_distance(client, valid_delivery):
    row = valid_delivery.copy()
    row["distance_km"] = 0.05
    response = client.post(
        "/v1/models/eta:predict",
        json={"instances": [row]})
    assert response.status_code == 422


def test_invalid_hour(client, valid_delivery):
    row = valid_delivery.copy()
    row["hour"] = 25
    response = client.post(
        "/v1/models/eta:predict",
        json={"instances": [row]})
    assert response.status_code == 422

def test_missing_required_feature(client, valid_delivery):
    row = valid_delivery.copy()
    del row["weight_kg"]
    response = client.post(
        "/v1/models/eta:predict",
        json={"instances": [row]})
    assert response.status_code == 422


def test_infeasible_delivery_rejected(client, valid_delivery):  #cross field validation
    row = valid_delivery.copy()
    row["distance_km"] = 200
    row["n_stops"] = 25
    response = client.post(
        "/v1/models/eta:predict",
        json={"instances": [row]})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_failed"
    messages = " ".join(detail["msg"] for detail in body["details"])
    assert "infeasible" in messages.lower()


def test_empty_batch_rejected(client): #batch size validation
    response = client.post(
        "/v1/models/eta:predict",
        json={"instances": []})
    assert response.status_code == 422

def test_batch_over_500_rejected(client, valid_delivery):
    rows = [valid_delivery.copy() for _ in range(501)]
    response = client.post(
        "/v1/models/eta:predict",
        json={"instances": rows})
    assert response.status_code == 422

def test_batch_of_500_accepted(client, valid_delivery):
    rows = [valid_delivery.copy() for _ in range(500)]
    response = client.post(
        "/v1/models/eta:predict",
        json={"instances": rows})
    assert response.status_code == 200
    assert len(response.json()["predictions"]) == 500


def test_request_id_echo(client, valid_delivery): #request id validation
    custom_id = "swiftparcel-custom-123"
    response = client.post(
        "/v1/models/eta:predict",
        headers={"X-Request-ID": custom_id},
        json={"instances": [valid_delivery]})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == custom_id
    assert response.json()["request_id"] == custom_id

def test_request_id_generated(client, valid_delivery):
    response = client.post(
        "/v1/models/eta:predict",
        json={"instances": [valid_delivery]}
    )
    assert response.status_code == 200
    request_id = response.headers["X-Request-ID"]
    assert request_id
    assert response.json()["request_id"] == request_id

def test_request_id_present_in_validation_error(client):
    response = client.post(
        "/v1/models/eta:predict",
        json={"instances": []})
    assert response.status_code == 422
    request_id = response.headers["X-Request-ID"]
    assert request_id
    assert response.json()["request_id"] == request_id

def test_health_check(client):  #health checks
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_version"] == "v1"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_metrics_output(client): #preometric output
    # Generate one measurable request.
    client.get("/healthz")
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert "eta_requests_total" in body
    assert "eta_request_latency_seconds_bucket" in body
    assert "eta_request_latency_seconds_sum" in body
    assert "eta_request_latency_seconds_count" in body
    assert 'le="+Inf"' in body
    assert 'path="/healthz"' in body


def test_metrics_endpoint_not_counted(client):
    client.get("/healthz")
    # Request metrics twice.
    first = client.get("/metrics").text
    second = client.get("/metrics").text
    assert first == second
    assert 'path="/metrics"' not in second


def test_generic_500_on_model_failure(client,valid_delivery,monkeypatch):  #generic 500 error on model failure
    class FailingModel:
        def predict(self, dataframe):
            raise RuntimeError("Sensitive internal model failure")
    monkeypatch.setattr(
        eta_app,
        "model",
        FailingModel())
    response = client.post( "/v1/models/eta:predict",json={"instances": [valid_delivery]})
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_server_error"
    assert "Sensitive internal model failure" not in response.text
    assert body["request_id"] == response.headers["X-Request-ID"]
