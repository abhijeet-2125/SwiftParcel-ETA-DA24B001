import joblib
import mlflow.pyfunc
import numpy as np
import pandas as pd

class ETApyfunc(mlflow.pyfunc.PythonModel):
    def load_context(self, context):
        # Load the serialized model bundle.
        bundle = joblib.load(context.artifacts["model"])

        # Extract the estimator and its expected feature order.
        self.model = bundle["model"]
        self.feature_columns = bundle["feature_columns"]

        # Preserve the model's version identifier.
        self.version = bundle["version"]

    def predict(self, context, model_input, params=None):
        if not isinstance(model_input, pd.DataFrame):
            model_input = pd.DataFrame(model_input)

        # Validate and preserve feature order.
        model_input = model_input[self.feature_columns]

        # Generate predictions.
        predictions = self.model.predict(model_input)

        # Round ETA predictions to one decimal place.
        predictions = np.round(np.asarray(predictions).reshape(-1),1)

        # Return the required output schema.
        return pd.DataFrame({
            "eta_minutes": predictions,
            "model_version": [self.version] * len(predictions)})
