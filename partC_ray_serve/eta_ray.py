from __future__ import annotations
import hashlib
import logging
import signal
import time
from pathlib import Path
from typing import Any
import joblib
import pandas as pd
import ray
from fastapi import FastAPI, Header, HTTPException
from ray import serve
from eta_data import FEATURES

#config
PROJECT_ROOT = Path(__file__).resolve().parents[1]
V1_MODEL_PATH = PROJECT_ROOT / "data" / "eta_v1.joblib"
V2_MODEL_PATH = PROJECT_ROOT / "data" / "eta_v2.joblib"
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8002
CPU_PER_MODEL_REPLICA = 0.25
CPU_PER_ROUTER_REPLICA = 0.25
logger = logging.getLogger("swiftparcel.ray")

# costraints
def customer_bucket(customer_id: str) -> int:
    digest = hashlib.blake2b(str(customer_id).encode("utf-8"),digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big") % 10_000

def choose_arm(customer_id: str, weight: float) -> str:
    if not 0.0 <= weight <= 1.0:
        raise ValueError("weight must be between 0 and 1")
    threshold = int(weight * 10_000)
    return ("v2"
        if customer_bucket(customer_id) < threshold
        else "v1")

#model deployment
@serve.deployment(ray_actor_options={"num_cpus": CPU_PER_MODEL_REPLICA})
class EtaModel:
    def __init__(self,model_path: str,extra_work_ms: float = 0.0):
        bundle = joblib.load(model_path)
        self.model = bundle["model"]
        self.feature_columns = bundle["feature_columns"]
        self.version = bundle["version"]
        self.extra_work_ms = float(extra_work_ms)
        logger.info("Loaded model version %s from %s",self.version,model_path)

    def predict(self, instances: list[dict[str, Any]]) -> dict:
        if self.extra_work_ms > 0:
            time.sleep(self.extra_work_ms / 1000.0)
        frame = pd.DataFrame(instances)
        frame = frame[self.feature_columns]
        predictions = self.model.predict(frame)
        return {
            "predictions": [
                round(float(value), 1)
                for value in predictions
            ],
            "model_version": self.version,
        }


router_app = FastAPI(
    title="SwiftParcel ETA Canary Router",
    version="1.0.0",
)


@serve.deployment(
    ray_actor_options={"num_cpus": CPU_PER_ROUTER_REPLICA},
)
@serve.ingress(router_app)
class EtaRouter:
    """Routes customer requests to production or candidate models."""

    def __init__(
        self,
        production_handle,
        candidate_handle,
        weight: float = 0.1,
    ):
        if not 0.0 <= weight <= 1.0:
            raise ValueError("weight must be between 0 and 1")

        self.production = production_handle
        self.candidate = candidate_handle
        self.weight = float(weight)

        logger.info(
            "Router initialized with candidate weight %.2f",
            self.weight,
        )

    @router_app.get("/healthz")
    async def health(self):
        return {
            "status": "healthy",
            "candidate_weight": self.weight,
        }

    @router_app.post("/v1/models/eta:predict")
    async def predict(
        self,
        payload: dict,
        x_beta: str | None = Header(
            default=None,
            alias="X-Beta",
        ),
    ):
        """Validate, route and return ETA predictions."""

        customer_id = payload.get("customer_id")

        if not isinstance(customer_id, str) or not customer_id.strip():
            raise HTTPException(
                status_code=422,
                detail="customer_id must be a non-empty string",
            )

        instances = payload.get("instances")

        if not isinstance(instances, list) or not instances:
            raise HTTPException(
                status_code=422,
                detail="instances must be a non-empty list",
            )

        for index, instance in enumerate(instances):
            if not isinstance(instance, dict):
                raise HTTPException(
                    status_code=422,
                    detail=f"Instance {index} must be a JSON object",
                )

            missing = [
                feature
                for feature in FEATURES
                if feature not in instance
            ]

            if missing:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "row": index,
                        "missing_features": missing,
                    },
                )

        # Explicit beta override takes precedence.
        if x_beta == "1":
            arm = "v2"
            routing_reason = "beta_override"
        else:
            arm = choose_arm(customer_id, self.weight)
            routing_reason = "customer_hash"

        # Invoke the selected deployment.
        if arm == "v2":
            result = await self.candidate.predict.remote(instances)
        else:
            result = await self.production.predict.remote(instances)

        return {
            **result,
            "arm": arm,
            "routing_reason": routing_reason,
        }



#application builder
def build(weight=0.1, extra_work_ms=0.0):
    weight = float(weight)
    extra_work_ms = float(extra_work_ms)

    if not 0.0 <= weight <= 1.0:
        raise ValueError("weight must be between 0 and 1")

    if extra_work_ms < 0:
        raise ValueError("extra_work_ms cannot be negative")

    if not V1_MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Production model not found: {V1_MODEL_PATH}"
        )

    if not V2_MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Candidate model not found: {V2_MODEL_PATH}"
        )

    # Production: exactly two fixed replicas.
    production = EtaModel.options(
        name="production_model",
        num_replicas=2,
        ray_actor_options={
            "num_cpus": CPU_PER_MODEL_REPLICA,
        },
    ).bind(
        str(V1_MODEL_PATH),
        extra_work_ms,
    )

    # Candidate: autoscaling between one and three replicas.
    candidate = EtaModel.options(
        name="candidate_model",
        autoscaling_config={
            "min_replicas": 1,
            "max_replicas": 3,
            "target_ongoing_requests": 5,
            "upscale_delay_s": 5,
            "downscale_delay_s": 30,
        },
        ray_actor_options={
            "num_cpus": CPU_PER_MODEL_REPLICA,
        },
    ).bind(
        str(V2_MODEL_PATH),
        extra_work_ms,
    )

    return EtaRouter.bind(
        production,
        candidate,
        weight,
    )


def serve_build(args: dict):
    args = args or {}
    return build(
        weight=args.get("weight", 0.1),
        extra_work_ms=args.get("extra_work_ms", 0.0),
    )

#direct execution
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Connect to the existing two-CPU Ray cluster.
    ray.init(address="auto")
    # Start Serve on port 8002.
    serve.start(
        http_options={
            "host": HTTP_HOST,
            "port": HTTP_PORT,
        },
    )

    # Initial candidate traffic allocation: 20%.
    application = build(
        weight=0.2,
        extra_work_ms=0.0,
    )

    serve.run(
        application,
        name="swiftparcel_eta",
        route_prefix="/",
    )

    logger.info(
        "SwiftParcel Ray Serve is running at http://%s:%s",
        HTTP_HOST,
        HTTP_PORT,
    )

    # Keep the process alive.
    signal.pause()
