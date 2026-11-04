# SwiftParcel ETA 
### Production-Oriented ETA Prediction, Model Governance & Safe Deployment

SwiftParcel ETA is an end-to-end machine learning serving and deployment project that demonstrates how an ETA prediction model can move from experimentation to production through controlled evaluation, model governance, shadow testing, canary deployment, and release validation.

The project combines **FastAPI, MLflow, Ray Serve, and Python-based performance testing** to build a reliable model-serving workflow with explicit promotion and rollback controls.

---

## Project Objectives

- Build a REST API for real-time and batch ETA predictions.
- Track and version production and candidate models using MLflow.
- Implement a quantitative promotion gate using accuracy, slice-level performance, and latency.
- Compare production and candidate predictions through shadow testing.
- Deploy models using Ray Serve with customer-level sticky canary routing.
- Validate deployments through automated smoke tests and release-abort policies.

## System Architecture

```text
                 ETA Prediction Models
                         |
                  +------+------+
                  |             |
                Model v1      Model v2
                  |             |
                  +------+------+
                         |
                    MLflow Registry
                         |
                 Promotion Evaluation
                         |
                  Shadow Comparison
                         |
                    Ray Serve
                         |
                 Customer Hash Router
                    /          \
              Production      Candidate
                (v1)             (v2)
                    \          /
                     Prediction
                         |
                  Release Validation
```

## Technology Stack

| Component | Technology |
|---|---|
| Programming language | Python |
| API framework | FastAPI |
| Model tracking | MLflow |
| Model registry | MLflow Model Registry |
| Model serving | Ray Serve |
| Data processing | Pandas, NumPy |
| Model serialization | Joblib |
| API testing | Pytest |
| Performance testing | Custom Python load generator |
| Tracking backend | SQLite |

## Project Structure

```text
Module4_SwiftParcel/
│
├── data/
│   ├── eta_train.csv
│   ├── eta_fresh.csv
│   └── eta_sample.csv
│
├── partA_rest_api/
│   ├── eta_app.py
│   ├── test_eta_app.py
│   └── results/
│
├── partB_mlflow/
│   ├── eta_pyfunc.py
│   ├── eta_mlflow.py
│   ├── log_models.py
│   ├── compare_servers.py
│   ├── test_gate.py
│   └── results/
│
├── partC_ray_serve/
│   ├── eta_ray.py
│   ├── verify_ray.py
│   ├── C3_explanations.md
│   └── results/
│
├── partD_release_gate/
│   ├── smoke_eta.py
│   ├── abort_criteria.md
│   └── results/
│
├── shared/
├── reports/
├── eta_data.py
├── load_test.py
├── requirements.txt
├── REPORT.md
└── README.md
```

## Setup and Installation

### 1. Clone the repository

```bash
git clone https://github.com/<YOUR_GITHUB_USERNAME>/SwiftParcel-ETA.git
cd SwiftParcel-ETA
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Generate the datasets and model bundles

```bash
python eta_data.py
```

This generates the training and fresh evaluation datasets, along with the two model bundles.

Generate the sample dataset used by the load generator:

```bash
python -c "import pandas as pd, eta_data; pd.read_csv('data/eta_fresh.csv')[eta_data.FEATURES].head(200).to_csv('data/eta_sample.csv', index=False)"
```

## Implementation Overview

### Part A — REST API and Performance Engineering

Implemented a FastAPI-based ETA prediction service with:

- Single-row and batch prediction support.
- Request ID generation and propagation.
- Input validation and structured error responses.
- Health and Prometheus-style metrics endpoints.
- Model loading at application startup.

**Performance results**

| Configuration | Throughput | p95 Latency | Errors |
|---|---:|---:|---:|
| Single worker, concurrency 8 | 802.2 RPS | 14.7 ms | 0 |
| Two workers, concurrency 8 | 1,829.8 RPS | 5.4 ms | 0 |
| Batch size 100 | 371.8 RPS | 3.4 ms | 0 |

Two-worker serving achieved approximately 2.28× the single-worker baseline peak throughput in the recorded tests.

### Part B — MLflow Model Governance

Implemented:

- Custom MLflow PyFunc model wrapper.
- Model signatures and input examples.
- Model registration and alias-based version management.
- Production-versus-candidate evaluation.
- Promotion gate based on overall MAE, rush-hour MAE, rainy-slice MAE, and p95 latency.
- Shadow comparison using identical prediction requests.

**Model evaluation**

| Metric | Production v1 | Candidate v2 |
|---|---:|---:|
| Overall MAE | 11.9553 | 6.1049 |
| Rush-hour MAE | 19.6799 | 8.5795 |
| Rain MAE | 11.6227 | 6.2853 |

The candidate achieved approximately 48.94% lower overall MAE.

The default 3% improvement gate passed, while the stricter 60% improvement gate correctly rejected the candidate.

### Part C — Ray Serve and Canary Deployment

Implemented a distributed serving architecture featuring:

- Two fixed production replicas.
- Candidate autoscaling between one and three replicas.
- Batch inference support.
- Deterministic BLAKE2b customer-level routing.
- Sticky customer assignments across redeployments.
- `X-Beta: 1` candidate override.
- Fractional CPU allocation.

**Verification results**

| Verification | Result |
|---|---|
| Candidate allocation | Passed |
| Customer-level stickiness | Passed |
| Redeployment consistency | Passed |
| Canary weight monotonicity | Passed |
| Beta header override | Passed |

All five verification checks passed.

### Part D — Release Validation

Implemented a command-line smoke-testing utility that validates:

- Service health.
- Expected model version.
- ETA sanity.
- Prediction behaviour for different distances.
- Invalid-input rejection.
- p95 latency threshold.

The recorded release validation passed all 6 checks.

Documented abort policies cover latency degradation, HTTP 5xx errors, overall model regression, and slice-level regression.

## Overall Results

| Evaluation | Result |
|---|---|
| Part A API tests | 16 passed |
| Part B promotion-gate tests | 7 passed |
| Part C Ray Serve checks | 5 passed |
| Part D smoke checks | 6 passed |
| Candidate overall MAE improvement | 48.94% |
| Best recorded API throughput | 1,829.8 RPS |

## Documentation

For the detailed implementation, experiments, evaluation methodology, results, limitations, and release state machine, see:

- [`REPORT.md`](REPORT.md) — Consolidated assignment report.
- [`reports/`](reports/) — Part-wise technical reports.
- [`partC_ray_serve/C3_explanations.md`](partC_ray_serve/C3_explanations.md) — Ray Serve design decisions.
- [`partD_release_gate/abort_criteria.md`](partD_release_gate/abort_criteria.md) — Release-abort policies.

## Reproducibility and Limitations

- Model bundles and MLflow tracking databases are generated locally and are not included in the repository.
- Run `eta_data.py` to regenerate the required model artifacts and datasets.
- Recorded performance results are from a local Apple Silicon environment and are not production capacity guarantees.
- The documented abort policies are proposed release controls; continuous automated abort monitoring was not implemented.
- The Part D smoke test was validated against the Part A REST API, not the Ray Serve ingress.

## Author

**Abhijeet Kumar, DA24B001**  
B.Tech — Artificial Intelligence and Data Analytics  
Indian Institute of Technology Madras

---

*Built as part of the Module 4 AIOps assignment, exploring reliable machine learning serving, model governance, controlled deployment, and release engineering.*
