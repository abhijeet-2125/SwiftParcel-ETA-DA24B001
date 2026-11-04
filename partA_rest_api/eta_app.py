import os
import uuid
import time
import threading
import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ConfigDict, model_validator

MODEL_PATH = os.getenv("ETA_MODEL", "data/eta_v1.joblib")  #to load the model
bundle = joblib.load(MODEL_PATH)

model = bundle["model"]
FEATURE_COLUMNS = bundle["feature_columns"]
MODEL_VERSION = bundle["version"]

#fastapi setting
app = FastAPI(title="SwiftParcel ETA Prediction API",description="Production-style delivery time prediction service",version="1.0.0")


METRICS_LOCK = threading.Lock()
REQUEST_COUNTS = {}
LATENCY_BUCKETS = [0.005, 0.01,0.025,0.05,0.1,0.25,0.5,1.0,2.5,5.0]

# Histogram bucket counts.
LATENCY_COUNTS = { bucket: 0 for bucket in LATENCY_BUCKETS}
LATENCY_COUNTS["+Inf"] = 0
LATENCY_SUM = 0.0
LATENCY_TOTAL = 0

def record_metrics(path: str, status: int, latency: float):
    """Record request count and latency."""
    global LATENCY_SUM, LATENCY_TOTAL
    with METRICS_LOCK:
        key = (path, status)
        REQUEST_COUNTS[key] = ( REQUEST_COUNTS.get(key, 0) + 1)
        LATENCY_SUM += latency
        LATENCY_TOTAL += 1
        for bucket in LATENCY_BUCKETS:
            if latency <= bucket:
                LATENCY_COUNTS[bucket] += 1
        LATENCY_COUNTS["+Inf"] += 1


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    # Reuse client-provided ID or generate a UUID.
    request_id = (request.headers.get("X-Request-ID") or str(uuid.uuid4()))
    request.state.request_id = request_id
    start_time = time.perf_counter()
    response = await call_next(request)
    latency = time.perf_counter() - start_time

    # Do not count requests made to /metrics itself.
    if request.url.path != "/metrics":
        record_metrics(
            path=request.url.path,
            status=response.status_code,
            latency=latency)
    response.headers["X-Request-ID"] = request_id
    return response

class Delivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    distance_km: float = Field(ge=0.1, le=500)
    n_stops: int = Field(ge=1,le=60)
    hour: int = Field(ge=0,le=23 )
    weekday: int = Field(ge=0,le=6)
    traffic_index: float = Field(ge=0,le=1)
    rain: int = Field(ge=0,le=1)
    weight_kg: float = Field(
        gt=0,
        le=1000)

    @model_validator(mode="after")
    def validate_delivery_feasibility(self):

        if (self.distance_km > 150 and self.n_stops > 20):
            raise ValueError(
                "infeasible delivery: distance_km > 150 "
                "and n_stops > 20")
        return self

class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instances: list[Delivery] = Field( min_length=1,max_length=500)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError
):

    request_id = request.state.request_id
    details = []
    for error in exc.errors():
        location = error.get("loc", ())
        field_name = ".".join(
            str(part) for part in location
        )

        details.append({
            "field": field_name,
            "msg": error.get("msg", "Invalid input")
        })

    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_failed",
            "details": details,
            "request_id": request_id
        }
    )


@app.exception_handler(Exception)
async def generic_exception_handler(
    request: Request,
    exc: Exception
):

    request_id = getattr(
        request.state,
        "request_id",
        str(uuid.uuid4())
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "request_id": request_id
        }
    )

@app.post("/v1/models/eta:predict")
def predict_eta(
    payload: PredictionRequest,
    request: Request
):

    request_id = request.state.request_id

    try:

        # Convert validated instances to a DataFrame.
        rows = [
            instance.model_dump()
            for instance in payload.instances
        ]

        dataframe = pd.DataFrame(
            rows,
            columns=FEATURE_COLUMNS
        )

        # Batch inference.
        predictions = model.predict(dataframe)

        # Round all predictions to one decimal place.
        predictions = np.round(
            predictions,
            1
        ).tolist()

        return {
            "predictions": predictions,
            "model_version": MODEL_VERSION,
            "request_id": request_id
        }

    except Exception:

        # Never expose internal model errors.
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "request_id": request_id
            }
        )



@app.get("/healthz")
async def health_check(request: Request):
    return { "status": "ok", "model_version": MODEL_VERSION,"request_id": request.state.request_id}

@app.get("/metrics")
async def metrics():
    lines = []
    with METRICS_LOCK:
        # Request counter.
        lines.append(
            "# HELP eta_requests_total Total ETA API requests")

        lines.append(
            "# TYPE eta_requests_total counter")

        for (path, status), count in sorted(
            REQUEST_COUNTS.items()):
            lines.append(
                f'eta_requests_total{{path="{path}",status="{status}"}} {count}')

        # Latency histogram.
        lines.append("# HELP eta_request_latency_seconds Request latency")
        lines.append("# TYPE eta_request_latency_seconds histogram")
        for bucket, count in LATENCY_COUNTS.items():
            lines.append(f'eta_request_latency_seconds_bucket{{le="{bucket}"}} {count}')
        lines.append( f"eta_request_latency_seconds_sum {LATENCY_SUM}")
        lines.append(f"eta_request_latency_seconds_count {LATENCY_TOTAL}")
    return PlainTextResponse(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
