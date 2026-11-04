"""
Log,register and version ETA models using MLflow.
"""
from pathlib import Path
import joblib
import mlflow
import numpy as np
import pandas as pd
import sklearn
from mlflow import MlflowClient
from mlflow.models import infer_signature
from eta_data import FEATURES
from partB_mlflow.eta_pyfunc import ETApyfunc

#cofig
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "mlflow.db"
TRAIN_DATA_PATH = PROJECT_ROOT / "data" / "eta_train.csv"
TRACKING_URI = f"sqlite:///{DB_PATH}"
REGISTERED_MODEL_NAME = "eta-model"
EXPERIMENT_NAME = "SwiftParcel-ETA"

mlflow.set_tracking_uri(TRACKING_URI)
mlflow.set_experiment(EXPERIMENT_NAME)

client = MlflowClient()

print("=" * 60)
print("SWIFTPARCEL MLflow MODEL REGISTRATION")
print("=" * 60)
print("Tracking URI:", mlflow.get_tracking_uri())
print("Experiment:", EXPERIMENT_NAME)

MODELS = {
    "v1": PROJECT_ROOT / "data" / "eta_v1.joblib",
    "v2": PROJECT_ROOT / "data" / "eta_v2.joblib"}


# Load training data to generate example predictions and infer model signature.
train_df = pd.read_csv(TRAIN_DATA_PATH)
X_example = train_df[FEATURES].head(5).copy()
print("\nInput features:", FEATURES)
print("Input example shape:", X_example.shape)


pip_requirements = [
    f"mlflow=={mlflow.__version__}",
    f"scikit-learn=={sklearn.__version__}",
    f"pandas=={pd.__version__}",
    f"numpy=={np.__version__}",
    f"joblib=={joblib.__version__}"]



#log and register each model version.
registered_versions = {}
for version_name, model_path in MODELS.items():
    print("\n" + "-" * 60)
    print(f"Processing model {version_name}")
    print("-" * 60)

    if not model_path.exists():
        raise FileNotFoundError( f"Model artifact not found: {model_path}")

    # Load the complete model bundle.
    bundle = joblib.load(model_path)

    # Extract the actual sklearn estimator.
    sklearn_model = bundle["model"]

    # Validate feature columns.
    bundle_features = bundle["feature_columns"]

    if list(bundle_features) != list(FEATURES):
        raise ValueError(f"Feature mismatch in {version_name}: " f"{bundle_features}")
    print("Underlying model:", type(sklearn_model).__name__)
    print("Bundle version:", bundle["version"])

    # Generate example predictions.
    example_predictions = sklearn_model.predict(X_example)
    output_example = pd.DataFrame({"eta_minutes": example_predictions})

    # Infer model signature.
    signature = infer_signature(X_example,output_example)

    # Log model and register it.
    with mlflow.start_run(run_name=f"ETA-{version_name}") as run:
        model_info = mlflow.pyfunc.log_model(
            name=f"eta_{version_name}",
            python_model=ETApyfunc(),
            artifacts={"model": str(model_path)},
            signature=signature,
            input_example=X_example,
            pip_requirements=pip_requirements)
        print("Run ID:", run.info.run_id)
        print("Model URI:", model_info.model_uri)

        registered_model = mlflow.register_model(model_uri=model_info.model_uri,name=REGISTERED_MODEL_NAME,)
        registered_versions[version_name] = str(registered_model.version)
        print("Registered version:",registered_model.version)


# to promote a model version,i created aliases for the registered model.
client.set_registered_model_alias(
    name=REGISTERED_MODEL_NAME,
    alias="prod",
    version=registered_versions["v1"])

client.set_registered_model_alias(
    name=REGISTERED_MODEL_NAME,
    alias="candidate",
    version=registered_versions["v2"])

# Verify that the aliases point to the expected model versions.
prod_model = client.get_model_version_by_alias(
    REGISTERED_MODEL_NAME,
    "prod")

candidate_model = client.get_model_version_by_alias(
    REGISTERED_MODEL_NAME,
    "candidate")

print("\n" + "=" * 60)
print("MODEL REGISTRY VERIFICATION")
print("=" * 60)
print(f"prod      -> version {prod_model.version}")
print(f"candidate -> version {candidate_model.version}")
print("\nB1 registration completed successfully.")
print("=" * 60)
