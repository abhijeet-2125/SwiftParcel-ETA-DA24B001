# Model evaluation and automated promotion gate.
import time
from pathlib import Path
import mlflow
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error
from eta_data import FEATURES, make_data

#config
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRACKING_URI = f"sqlite:///{PROJECT_ROOT / 'mlflow.db'}"
REGISTERED_MODEL_NAME = "eta-model"



#B2: Promotion decision logic
def decide(prod,cand,min_rel_gain=0.03,max_p95_ms=10.0, max_slice_regression=0.05):
    if prod["mae"] <= 0:
        raise ValueError("Production MAE must be positive.")
    if prod["mae_rush"] <= 0:
        raise ValueError("Production rush-hour MAE must be positive.")
    if prod["mae_rain"] <= 0:
        raise ValueError("Production rainy-slice MAE must be positive.")

    # 1. Overall relative MAE improvement.
    relative_gain = (prod["mae"] - cand["mae"]) / prod["mae"]
    overall_pass = relative_gain >= min_rel_gain

    # 2. Candidate p95 inference latency.
    latency_pass = cand["p95_ms"] <= max_p95_ms

    # 3. Relative regression on rush-hour deliveries.
    rush_regression = (cand["mae_rush"] - prod["mae_rush"]) / prod["mae_rush"]
    rush_pass = rush_regression <= max_slice_regression

    # 4. Relative regression on rainy deliveries.
    rain_regression = (cand["mae_rain"] - prod["mae_rain"]) / prod["mae_rain"]
    rain_pass = rain_regression <= max_slice_regression

    criteria = {
        "overall_mae": {
            "pass": overall_pass,
            "relative_gain": round(relative_gain, 4),
            "required_gain": min_rel_gain
        },
        "latency": {
            "pass": latency_pass,
            "candidate_p95_ms": round(cand["p95_ms"], 3),
            "maximum_p95_ms": max_p95_ms
        },
        "rush_hour": {
            "pass": rush_pass,
            "relative_regression": round(rush_regression, 4),
            "maximum_regression": max_slice_regression
        },
        "rain": {
            "pass": rain_pass,
            "relative_regression": round(rain_regression, 4),
            "maximum_regression": max_slice_regression}}

    promote = all(criterion["pass"] for criterion in criteria.values())
    return {"criteria": criteria,"promote": promote}


#B2: Evaluate a registered model
def evaluate_model(model, data):
    X = data[FEATURES]
    y = data["eta_minutes"]
    predictions = model.predict(X)

    # The custom PyFunc model returns a DataFrame.
    if isinstance(predictions, pd.DataFrame):
        predictions = predictions["eta_minutes"].to_numpy()
    predictions = np.asarray(predictions).reshape(-1)

    # Rush-hour periods: 7–9 AM and 4–7 PM.
    rush_mask = data["hour"].isin([7, 8, 9, 16, 17, 18, 19])

    # Rainy deliveries.
    rain_mask = data["rain"] == 1

    metrics = {
        "mae": float(mean_absolute_error(y, predictions)),
        "mae_rush": float(
            mean_absolute_error(
                y[rush_mask],
                predictions[rush_mask]
            )
        ),
        "mae_rain": float(
            mean_absolute_error(
                y[rain_mask],
                predictions[rain_mask]
            )
        ),
        "p95_ms": None
    }
    return metrics

# B2: Automated promotion gate
def promotion_gate(**thresholds):
    # Connect to the SQLite-backed MLflow tracking store.
    mlflow.set_tracking_uri(TRACKING_URI)
    print("Loading production and candidate models...")
    prod_model = mlflow.pyfunc.load_model(f"models:/{REGISTERED_MODEL_NAME}@prod" )
    candidate_model = mlflow.pyfunc.load_model(f"models:/{REGISTERED_MODEL_NAME}@candidate")
    # Generate the required fresh evaluation dataset.
    print("\nGenerating fresh evaluation data...")
    data = make_data(2000, seed=99)
    print(f"Evaluation rows: {len(data)}")

    # Evaluate production model.
    print("\nEvaluating production model...")
    prod_metrics = evaluate_model( prod_model,data)

    # Evaluate candidate model.
    print("Evaluating candidate model...")
    candidate_metrics = evaluate_model( candidate_model,data)

    # Measure candidate single-row inference latency.
    print("\nMeasuring candidate single-row latency...")
    X = data[FEATURES].head(200)
    latencies = []

    # Warm-up prediction, excluded from the measurement.
    candidate_model.predict(X.iloc[[0]])
    # Exactly 200 individual predictions.
    for i in range(len(X)):
        row = X.iloc[[i]]
        start = time.perf_counter()
        candidate_model.predict(row)
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)

    candidate_metrics["p95_ms"] = float(np.percentile(latencies, 95))

    # decide() only requires candidate p95 latency.
    prod_metrics["p95_ms"] = None

    # Display evaluation metrics.
    print("\nProduction metrics:")
    print(prod_metrics)
    print("\nCandidate metrics:")
    print(candidate_metrics)

    # Apply the promotion decision.
    result = decide(prod_metrics,
        candidate_metrics,
        **thresholds)

    print("\nPROMOTION GATE RESULT")
    print("=" * 50)
    print(result)
    return result

if __name__ == "__main__":
    print("\nRunning promotion gate with default thresholds...")
    default_result = promotion_gate()
    print("\n\nRunning promotion gate with 60% minimum MAE gain...")
    strict_result = promotion_gate(min_rel_gain=0.60)
