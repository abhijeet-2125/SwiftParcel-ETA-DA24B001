# Part C Report — Ray Serve Deployment and Sticky Canary Routing

## 1. Objective

The objective of Part C was to deploy SwiftParcel's ETA prediction models using **Ray Serve**, expose them through a FastAPI ingress, and implement a customer-level sticky canary rollout.

The deployment had to support:
- A production model deployment with two replicas.
- A candidate model deployment that can autoscale between one and three replicas.
- Batch ETA prediction requests through a single ingress endpoint.
- Deterministic customer-level routing between production (`v1`) and candidate (`v2`).
- A beta-request override using the `X-Beta: 1` HTTP header.
- Verification of traffic allocation, stickiness, redeployment stability, monotonic rollout, and beta override behavior.

The Ray Serve application was deployed locally on the two-CPU Ray cluster and exposed at `http://127.0.0.1:8002`.

## 2. System Architecture and Implementation

### 2.1 Model deployments

The implementation is in `partC_ray_serve/eta_ray.py`.

Two `EtaModel` deployments are created:

| Deployment | Model bundle | Replica configuration | CPU allocation |
|---|---|---:|---:|
| `production_model` | `data/eta_v1.joblib` | 2 fixed replicas | 0.25 CPU per replica |
| `candidate_model` | `data/eta_v2.joblib` | Autoscaling: 1–3 replicas | 0.25 CPU per replica |

Each replica loads its model bundle once during initialization. The deployment then reuses the loaded model for incoming prediction requests. The prediction method supports batches and returns the ETA predictions together with the model version.

### 2.2 FastAPI ingress

The `EtaRouter` deployment uses FastAPI ingress and exposes:

- `GET /healthz` — reports application health and the configured candidate traffic weight.
- `POST /v1/models/eta:predict` — accepts a customer ID and a list of delivery-feature instances, routes the request, and returns the prediction response.

The prediction response includes the model output, the selected arm (`v1` or `v2`), and the routing reason. The router validates the request and sends the prediction to the selected model deployment.

### 2.3 Sticky customer-level canary routing

The router uses a deterministic customer bucket generated with **BLAKE2b** (`hashlib.blake2b`, 8-byte digest), followed by modulo 10,000.

The bucket is compared with a threshold derived from the configured candidate weight. At a 20% candidate weight, customers whose bucket falls below the 20% threshold are routed to candidate (`v2`); the remaining customers are routed to production (`v1`).

This design has two important properties:

1. The same customer ID produces the same bucket across requests, processes, and redeployments.
2. Increasing the candidate threshold from 20% to 50% expands the candidate group without moving customers already assigned to candidate back to production.

The `X-Beta: 1` header bypasses normal hash-based assignment for that request and forces candidate routing. The response identifies this path with `routing_reason: "beta_override"`.

### 2.4 Resource allocation and autoscaling

The candidate deployment uses the following autoscaling configuration:

| Parameter | Configured value |
|---|---:|
| Minimum replicas | 1 |
| Maximum replicas | 3 |
| `target_ongoing_requests` | 5 |
| Upscale delay | 5 seconds |
| Downscale delay | 30 seconds |

The minimum replica keeps the candidate deployment available. The maximum limits resource consumption on the two-CPU cluster. The request target provides a load signal for scaling, while the delays reduce reactions to short-lived traffic fluctuations.

Both model deployments use fractional CPU allocation (`0.25` CPU per replica), allowing the model replicas and router to coexist within the available cluster resources. Production uses two fixed replicas; candidate uses autoscaling.

**Ray version compatibility note:** The application builder is implemented as `build(args: dict)` and receives settings such as `weight` and `extra_work_ms` through that dictionary. This follows the application-builder calling convention required by the Ray Serve version used in this environment (Ray 2.59.0).

## 3. Deployment and Runtime Verification

The application was started using the existing Ray cluster and deployed on port `8002`.

The Ray Serve status API reported:

| Component | Running replicas | Reported status |
|---|---:|---|
| HTTP proxy | 1 | Healthy |
| `production_model` | 2 | Healthy |
| `candidate_model` | 1 | Healthy |
| `EtaRouter` | 1 | Healthy |
| Application `swiftparcel_eta` | — | Running |

The health endpoint returned:

```json
{
  "status": "healthy",
  "candidate_weight": 0.2
}
```

A normal request for a test customer was routed to production and returned an ETA with model version `v1`. Repeating the request for the same customer continued to select `v1`.

A request with `X-Beta: 1` was routed to candidate and returned model version `v2`, with `routing_reason` set to `beta_override`.

These checks confirmed that both model deployments were reachable through the ingress and that the routing response exposed the selected arm.

## 4. C2 — Automated Canary Verification

The automated verification script is:

`partC_ray_serve/verify_ray.py`

The run was saved to:

`partC_ray_serve/results/c2_verification.log`

The script exercised the live Ray Serve application and checked the five required behaviors.

### 4.1 Results

| Test | Procedure | Observed result | Outcome |
|---|---|---:|---|
| Candidate allocation | Route 1,000 distinct customer IDs at 20% weight | 195/1,000 = **19.5%** candidate | PASS |
| Request stickiness | Repeat requests for 200 customers | **200/200** retained their arm | PASS |
| Redeployment stickiness | Redeploy at the same 20% weight and repeat 200 customers | **200/200** retained their arm | PASS |
| Monotonic rollout | Increase weight from 20% to 50% | **0/195** previous candidate customers moved back to production | PASS |
| Beta override | Send 50 requests with `X-Beta: 1` | **50/50** routed to candidate | PASS |

The candidate allocation of 19.5% is within the required interval of 16% to 24% (20% ± 4 percentage points).

### 4.2 Interpretation

The observed 19.5% candidate share is consistent with the configured 20% customer-level allocation. All 200 repeated customer checks retained their original arm, both before and after identical redeployment. When the candidate weight increased to 50%, none of the 195 customers already assigned to candidate moved back to production. All 50 beta-header requests were routed to candidate.

The verification script restored the application to a 20% candidate weight after completing the tests. The final health check confirmed `candidate_weight: 0.2`.

## 5. C3 — Design Rationale

### 5.1 Why Python's built-in `hash()` was avoided

Python's built-in string hash is randomized between processes by default and is not intended to provide a persistent routing identifier. Consequently, the same customer ID can map to a different bucket after a process restart. That would undermine customer stickiness during restarts or redeployments.

The implementation uses BLAKE2b from `hashlib`, which produces a deterministic digest for the same customer ID. Converting that digest into a bucket from 0 to 9,999 provides a stable basis for percentage-based routing.

### 5.2 Why customer-level stickiness matters

**Customer experience:** A customer should not alternate between model versions on successive ETA requests. Consistent routing avoids unnecessary changes in the behavior and estimates experienced by that customer during an experiment.

**Experiment validity:** Keeping a customer in one experiment arm prevents repeated observations from the same customer from being split between production and candidate. This makes group-level comparisons easier to interpret and reduces contamination between the two arms.

### 5.3 Autoscaling choices

The candidate deployment has one minimum replica so that it remains available, and a maximum of three replicas to bound resource use on the two-CPU cluster. `target_ongoing_requests=5` provides a concurrency target that can trigger scaling when requests accumulate. The five-second upscale delay allows sustained demand to be observed before adding capacity, while the 30-second downscale delay avoids removing capacity in response to brief dips in traffic.

## 6. Observations and Findings

1. **Both model versions were served successfully.** The ingress routed ordinary requests to the selected model deployment and returned the model version in the response.
2. **Customer assignment was deterministic.** Repeated requests for the same customer produced the same arm.
3. **The allocation was close to the configured weight.** The observed candidate share was 19.5% for 1,000 distinct customer IDs at a 20% setting.
4. **The routing rule was monotonic.** Raising the candidate weight from 20% to 50% did not move any previously assigned candidate customer back to production.
5. **The beta override worked independently of normal allocation.** All 50 requests carrying `X-Beta: 1` selected candidate.
6. **The deployment recovered after configuration updates.** Ray Serve replaced replicas during redeployment and reported the application ready afterward; the routing checks still passed.

## 7. Limitations and Scope

- The verification establishes routing behavior and deployment health for the tested local Ray cluster. It does not establish production-scale throughput or availability.
- The 1,000-customer allocation check is an empirical sample; a different set of customer IDs may produce a slightly different percentage.
- The tests verify that the configured autoscaling policy is accepted and that the deployment runs. They do not constitute a sustained-load experiment proving that the candidate actually scales from one to three replicas.
- No end-to-end performance comparison between Ray Serve and the Part A FastAPI service was performed as part of the recorded C2 checks.
- The local deployment uses `127.0.0.1:8002`; external accessibility, authentication, TLS, and production ingress configuration are outside the demonstrated scope.

## 8. Files and Evidence

| File | Purpose |
|---|---|
| `partC_ray_serve/eta_ray.py` | Ray Serve model deployments, FastAPI ingress, routing, and application builder |
| `partC_ray_serve/verify_ray.py` | Automated C2 verification script |
| `partC_ray_serve/C3_explanations.md` | Written C3 design explanations |
| `partC_ray_serve/results/c2_verification.log` | Captured output of all five C2 checks |

## 9. Conclusion

Part C implemented and deployed a Ray Serve-based ETA service with separate production and candidate model deployments, a FastAPI ingress, deterministic customer-level canary routing, and a beta override. The live application reported healthy deployments, and all five automated C2 checks passed. The observed candidate share was 19.5% at a configured 20% weight, customer assignments remained stable across repeated requests and redeployment, increasing the rollout weight was monotonic for the tested customers, and all 50 beta requests reached the candidate model. These results demonstrate the required routing and deployment behavior in the local test environment.
