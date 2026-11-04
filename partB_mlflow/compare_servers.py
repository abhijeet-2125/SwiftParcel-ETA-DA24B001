import time
import json
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from eta_data import FEATURES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "eta_fresh.csv"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

PROD_URL = "http://127.0.0.1:5101/invocations"
CANDIDATE_URL = "http://127.0.0.1:5102/invocations"

N_REQUESTS = 200


def send_single_request(session, url, row):
    #to send one row to an MLflow scoring server and measure latency
    payload = {"dataframe_records": [row]}
    start = time.perf_counter()
    response = session.post(url,json=payload,timeout=30)
    latency_ms = (time.perf_counter() - start) * 1000
    response.raise_for_status()
    result = response.json()

    # MLflow PyFunc scoring response.
    predictions = result["predictions"]

    if isinstance(predictions, list):
        prediction = predictions[0]
        if isinstance(prediction, dict):
            eta = float(prediction["eta_minutes"])
            version = prediction["model_version"]
        else:
            eta = float(prediction)
            version = None
    else:
        raise ValueError(f"Unexpected prediction response: {result}")
    return eta, version, latency_ms

def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading fresh evaluation data...")
    data = pd.read_csv(DATA_PATH)

    #to select exactly 200 rows and only the model features.
    evaluation_data = data[FEATURES].head(N_REQUESTS)
    if len(evaluation_data) != N_REQUESTS:
        raise ValueError(f"Expected {N_REQUESTS} rows, found {len(evaluation_data)}")
    rows = evaluation_data.to_dict(orient="records")

    prod_predictions = []
    candidate_predictions = []

    prod_latencies = []
    candidate_latencies = []

    prod_versions = []
    candidate_versions = []

    print("\nSending 200 requests to production...")
    with requests.Session() as session:
        for i, row in enumerate(rows, start=1):
            eta, version, latency = send_single_request(session,PROD_URL,row)
            prod_predictions.append(eta)
            prod_versions.append(version)
            prod_latencies.append(latency)

            if i % 50 == 0:
                print(f"Production: {i}/{N_REQUESTS}")
    print("\nSending the same 200 requests to candidate...")
    with requests.Session() as session:
        for i, row in enumerate(rows, start=1):
            eta, version, latency = send_single_request( session,CANDIDATE_URL,row)
            candidate_predictions.append(eta)
            candidate_versions.append(version)
            candidate_latencies.append(latency)
            if i % 50 == 0:
                print(f"Candidate: {i}/{N_REQUESTS}")
    # Convert predictions to NumPy arrays.
    prod_predictions = np.asarray(
        prod_predictions,
        dtype=float)

    candidate_predictions = np.asarray(
        candidate_predictions,
        dtype=float)

    # Absolute difference between corresponding predictions.
    differences = np.abs(prod_predictions - candidate_predictions)
    mean_absolute_difference = float( np.mean(differences))
    percentage_over_10 = float(np.mean(differences > 10) * 100)
    prod_p95 = float(np.percentile(prod_latencies, 95))
    candidate_p95 = float(np.percentile(candidate_latencies, 95))

    #to verify that the expected aliases served the requests.
    print("\nModel versions observed:")
    print("Production:", set(prod_versions))
    print("Candidate:", set(candidate_versions))

    results = {
        "requests_per_server": N_REQUESTS,
        "mean_absolute_prediction_difference_minutes":
            round(mean_absolute_difference, 4),
        "requests_differing_more_than_10_minutes_percent":
            round(percentage_over_10, 2),
        "production_p95_latency_ms":
            round(prod_p95, 3),
        "candidate_p95_latency_ms":
            round(candidate_p95, 3),
        "production_model_versions": list(set(prod_versions)),
        "candidate_model_versions": list(set(candidate_versions))}

    print("\nB3 COMPARISON RESULTS")
    print("=" * 55)
    print(json.dumps(results, indent=4))

    output_path = RESULTS_DIR / "server_comparison.json"

    with open(output_path, "w") as file:
        json.dump(results, file, indent=4)
    print(f"\nResults saved to: {output_path}")

if __name__ == "__main__":
    main()
