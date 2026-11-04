# SwiftParcel ETA — Module 4 Assignment Report

**Student:** Abhijeet Kumar, DA24B001
**Project:** SwiftParcel ETA Prediction and Safe Model Delivery  
**Environment:** Python 3.14.8, MLflow 3.17.2, Ray 2.59.0

## 1. System overview

SwiftParcel predicts delivery ETA from delivery features and exposes the prediction service through a REST API. The assignment extends the service into a model-delivery workflow: production and candidate models are tracked in MLflow, evaluated through a promotion gate, compared through shadow requests, and deployed behind a customer-sticky canary router using Ray Serve. A smoke test provides a release check.

The workflow separates model quality from serving behaviour. A candidate is not promoted merely because it is newer: it must satisfy overall and slice-level accuracy constraints as well as a latency limit.

## 2. Part A — REST API and performance

The FastAPI service exposes `POST /v1/models/eta:predict`, `GET /healthz`, and `GET /metrics`. It supports single and batch predictions, returns the model version and request ID, validates feature ranges and cross-field constraints, and returns structured validation errors (HTTP 422). The model bundle is loaded once when the application starts. Request metrics include prediction counts and latency observations.

**Validation:** 16 API tests passed. The tests cover valid predictions, batch handling, validation failures, health/metrics endpoints, and error behaviour.

**Load-test results:** The best measured configuration used two Uvicorn workers. At concurrency 8 it achieved 1,829.8 requests/second with p95 latency of 5.4 ms, p99 of 6.8 ms, and zero errors. The single-worker baseline peaked at 802.2 requests/second (concurrency 8), with p95 latency of 14.7 ms. Thus, the two-worker configuration delivered approximately 2.28× the baseline peak throughput in these tests. Batch-100 testing achieved 371.8 requests/second with p95 latency of 3.4 ms; amortized p50 latency was approximately 0.026 ms per row.

These are local benchmark results on the stated machine.

## 3. Part B — MLflow tracking, promotion gate and shadow comparison

### B1. Model logging and registry

A custom MLflow PyFunc wrapper loads the model bundle and returns `eta_minutes` rounded to one decimal place together with `model_version`. Both model versions were logged with a signature, input example, and pinned requirements. The tracking store uses SQLite. The registered model is `eta-model`, with alias `prod` pointing to version 1 and alias `candidate` pointing to version 2.

### B2. Promotion gate

The pure `decide()` function evaluates overall MAE, rush-hour MAE, rainy-condition MAE, and candidate p95 prediction latency. The gate requires the candidate to improve overall MAE by the configured relative amount, remain within the latency ceiling, and avoid slice regressions beyond the configured tolerance. The evaluation uses fresh data generated with `eta_data.make_data(2000, seed=99)` and measures p95 using 200 single-row predictions.

| Metric | Production (v1) | Candidate (v2) |
|---|---:|---:|
| Overall MAE | 11.9553 | 6.1049 |
| Rush-hour MAE | 19.6799 | 8.5795 |
| Rain MAE | 11.6227 | 6.2853 |

The candidate reduced overall MAE by approximately **48.94%**. Rush-hour and rainy-slice MAE also improved (approximately 56.40% and 45.92%, respectively). Candidate p95 latency was 0.927 ms in the default gate run.

- **Default threshold (`min_rel_gain=0.03`):** promotion passed.
- **Strict threshold (`min_rel_gain=0.60`):** promotion failed because the measured 48.94% improvement did not meet the required 60%; the other criteria passed.

Seven gate tests passed, including boundary-condition checks. This demonstrates that the gate can reject a candidate when a deliberately stricter requirement is not met.

### B3. Shadow comparison

The production and candidate aliases were served separately on ports 5101 and 5102. The same 200 one-row requests from `data/eta_fresh.csv` were sent to both servers.

| Shadow comparison | Result |
|---|---:|
| Requests per server | 200 |
| Mean absolute prediction difference | 9.4845 minutes |
| Requests differing by more than 10 minutes | 35.0% |
| Production p95 latency | 3.509 ms |
| Candidate p95 latency | 3.659 ms |

Changing an MLflow alias does not hot-reload a model already loaded by a running serving process. Safe update options are (1) start and health-check a replacement instance, switch traffic, then drain the old instance (rolling replacement), or (2) load and validate a new model in-process and atomically switch the model reference while allowing in-flight requests to finish.

## 4. Part C — Ray Serve canary deployment

The Ray Serve application loads each model bundle once per replica and supports batch inference. Production is configured with two fixed replicas. Candidate autoscaling is configured from one to three replicas, with `target_ongoing_requests=5`, a 5-second upscale delay, and a 30-second downscale delay. Fractional CPU reservations allow the router and model replicas to coexist on the two-CPU Ray cluster.

The FastAPI ingress accepts a customer ID and delivery instances. A deterministic BLAKE2b hash maps each customer to a stable bucket; the configured threshold assigns approximately the specified fraction to candidate. `X-Beta: 1` overrides normal routing and forces candidate inference.

**Verification:** All five C2 checks passed:
1. Candidate allocation was within ±4 percentage points of the configured 20% weight.
2. Repeated requests from 200 sampled customers retained the same arm.
3. The same 200 customers retained their assignments after identical redeployment.
4. Increasing the candidate weight from 20% to 50% moved zero previously assigned candidate customers back to production.
5. The beta header forced candidate routing for all 50 tested requests.

The application was restored to 20% candidate weight after verification. The verification output is saved in `partC_ray_serve/results/c2_verification.log`.

Python's built-in `hash()` is unsuitable for persistent routing because hash randomization can produce different values across interpreter processes. Customer-level stickiness supports consistent customer experience and cleaner experiment attribution. The autoscaling settings balance candidate availability, responsiveness to sustained load, and resource limits; they are configuration choices tested in this local deployment, not the result of a production traffic study.

## 5. Part D — Smoke test and release policy

The smoke-test script accepts a service URL, expected model version, and p95 latency limit. It checks service health, expected model version, plausible positive ETA output, monotonicity for a longer-distance example, invalid-input rejection (HTTP 422), and p95 latency over 100 single-row requests.

**Recorded run:** 6/6 checks passed against the Part A REST API, expecting version `v1`. The observed p95 was 5.79 ms, with zero request errors. The test confirmed 49.5 minutes for the sample ETA and 30.1 versus 81.7 minutes for the 5 km and 25 km sanity cases.

The following are proposed abort policies, not measured incidents:
- Abort if p95 latency exceeds 80 ms for five consecutive minutes.
- Abort if HTTP 5xx rate exceeds 1% over five minutes with at least 500 requests.
- Abort if candidate overall MAE is more than 5% worse than production, based on at least 500 labelled deliveries.
- Abort if rush-hour or rainy-slice MAE is more than 10% worse, based on at least 100 observations in the affected slice.

On abort, stop increasing candidate traffic, route traffic back to the stable production version, and investigate logs and metrics before attempting another rollout.

## 6. Release state machine and limitations

**Release flow:** `REGISTERED → EVALUATED → SHADOWED → CANARY → RELEASED`. A failed promotion gate blocks progression from evaluation. A failed smoke test or an abort threshold returns traffic to the stable production version (`ROLLBACK`). The candidate remains non-production until the required checks pass.

**Not tested / limitations**
- The Part D smoke test was run against the Part A REST API, not the Ray Serve ingress.
- The abort thresholds are documented policies; continuous automated abort monitoring was not implemented.
- Shadow comparison used 200 sequential one-row requests; it is not a large-scale concurrent shadow-load study.
- Load-test figures are local-machine measurements and should not be interpreted as production SLO guarantees.
- The candidate autoscaling configuration was deployed and verified, but no controlled long-duration stress experiment was performed to characterize scaling behaviour under sustained production traffic.
- The promotion gate uses generated evaluation data; its metrics are not a substitute for evaluation on a representative, independently labelled production dataset.

## 7. Reproducibility and submission notes

The project includes the data-generation script `eta_data.py`, source code, tests, verification logs, and part-wise reports. Generated model bundles and tracking databases should be regenerated locally rather than included in the submission archive, in accordance with the assignment instructions. The Python environment and package versions are recorded above; dependency pins are also used for the MLflow model artifacts.

**Overall result:** Parts A–D have been implemented and their recorded tests passed: 16 API tests, 7 promotion-gate tests, 5 Ray Serve verification checks and 6 smoke checks. The promotion gate passed at the default 3% improvement threshold and correctly rejected the stricter 60% threshold.
