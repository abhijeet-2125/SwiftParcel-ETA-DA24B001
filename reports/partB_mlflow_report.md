# Part B — MLflow Model Registry, Promotion Gate, and Model Serving

## 1. Objective

In Part B, I integrated MLflow into the SwiftParcel ETA prediction workflow. The objective was to make model experimentation and deployment more controlled by:

- Logging both ETA models with a consistent PyFunc interface.
- Tracking and registering the models in an SQLite-backed MLflow store.
- Assigning production and candidate aliases.
- Implementing an automated promotion gate based on prediction quality, slice-level performance, and inference latency.
- Serving the two registered models independently and comparing their live prediction behaviour.

The two model bundles used in this experiment were `eta_v1.joblib` and `eta_v2.joblib`.

## 2. B1 — MLflow model logging and registry

### 2.1 Tracking store and experiment

I configured MLflow to use a local SQLite tracking store:

- Tracking URI: `sqlite:///mlflow.db`
- Experiment: `SwiftParcel-ETA`
- Registered model name: `eta-model`

This keeps experiment and registry metadata in a local database rather than relying on a remote tracking server.

### 2.2 Custom PyFunc wrapper

I implemented a custom `ETApyfunc` class in `partB_mlflow/eta_pyfunc.py`. The wrapper loads the serialized model bundle and extracts:

- The underlying estimator from `bundle["model"]`.
- The expected input feature order from `bundle["feature_columns"]`.
- The model identifier from `bundle["version"]`.

At prediction time, the wrapper converts array-like input to a pandas DataFrame when necessary, selects the expected feature columns in the correct order, and calls the underlying estimator.

The wrapper returns a DataFrame with the required output columns:

| Output column | Description |
|---|---|
| `eta_minutes` | ETA prediction rounded to one decimal place |
| `model_version` | Version identifier stored in the model bundle (`v1` or `v2`) |

This gives both models a common inference interface, even though their underlying estimators differ.

### 2.3 Model logging and registration

Both models were logged through MLflow's PyFunc flavour. During logging, I supplied:

- A model signature describing the input and output schema.
- A five-row input example using the seven ETA input features.
- Explicit, pinned package requirements for the model environment.
- The serialized model bundle as an artifact.

The models were then registered under the name `eta-model`.

### 2.4 Registry table

The final registry state was:

| Registered model | Registry version | Alias | Bundle version |
|---|---:|---|---|
| `eta-model` | 1 | `prod` | `v1` |
| `eta-model` | 2 | `candidate` | `v2` |

I verified that both aliases could be loaded using their MLflow model URIs. I also checked predictions from each alias and confirmed that the returned DataFrames contained both `eta_minutes` and `model_version`.

### 2.5 B1 verification

The final verification showed:

- `models:/eta-model@prod` loaded registry version 1 and returned `model_version = v1`.
- `models:/eta-model@candidate` loaded registry version 2 and returned `model_version = v2`.
- Both outputs contained the required columns.
- ETA values were rounded to one decimal place.

MLflow emitted non-fatal warnings recommending type hints for the PyFunc `predict` method and noting that Python integer input columns may not represent missing values. These warnings did not prevent model logging, registration, loading, or prediction.

## 3. B2 — Automated model promotion gate

### 3.1 Promotion criteria

I implemented the pure function `decide()` in `partB_mlflow/eta_mlflow.py`. It compares production and candidate metrics and returns a dictionary containing individual criterion results and the final `promote` decision.

The default thresholds are:

| Criterion | Default threshold |
|---|---:|
| Minimum relative overall MAE improvement | 3% |
| Maximum candidate single-row p95 latency | 10 ms |
| Maximum relative regression on each evaluated slice | 5% |

The gate checks four conditions:

1. **Overall MAE improvement:** the candidate must improve overall MAE by at least the configured relative gain.
2. **Inference latency:** candidate single-row p95 latency must not exceed the configured maximum.
3. **Rush-hour slice:** candidate MAE must not regress by more than the allowed percentage on rush-hour records.
4. **Rain slice:** candidate MAE must not regress by more than the allowed percentage on records where `rain == 1`.

Rush-hour records are defined as deliveries with hours 7–9 or 16–19. The rainy slice consists of records with `rain == 1`. The candidate is promoted only when all four checks pass.

The relative overall improvement is calculated as:

\[
\text{Relative MAE gain} =
\frac{\text{MAE}_{prod}-\text{MAE}_{cand}}
{\text{MAE}_{prod}}
\]

For each slice, relative regression is calculated as:

\[
\text{Relative regression} =
\frac{\text{MAE}_{cand,slice}-\text{MAE}_{prod,slice}}
{\text{MAE}_{prod,slice}}
\]

A negative slice regression indicates that the candidate has lower error than production on that slice.

### 3.2 Automated evaluation procedure

The `promotion_gate()` function:

1. Connects to the SQLite-backed MLflow tracking store.
2. Loads the `prod` and `candidate` aliases from `eta-model`.
3. Generates 2,000 fresh evaluation rows using `make_data(2000, seed=99)`.
4. Evaluates both models on the same data.
5. Calculates overall, rush-hour, and rainy-condition MAE.
6. Performs one warm-up prediction, then measures 200 individual candidate predictions.
7. Calculates the candidate's p95 single-row inference latency.
8. Passes the metrics to `decide()` and prints the result.

The production p95 field is not used by the decision function; the latency requirement applies to the candidate model.

### 3.3 Unit tests

I implemented seven tests in `partB_mlflow/test_gate.py`.

| Test | What it verifies | Result |
|---|---|---|
| Candidate passes all criteria | A candidate satisfying every condition is promoted | Passed |
| Insufficient overall improvement | A candidate below the required MAE gain is rejected | Passed |
| Latency just above limit | Candidate p95 above 10 ms is rejected | Passed |
| Rush-hour regression just above limit | Rush-hour regression above 5% is rejected | Passed |
| Rain regression just above limit | Rain-slice regression above 5% is rejected | Passed |
| Exact threshold boundaries | Values exactly at the configured limits pass | Passed |
| Custom improvement threshold | A stricter custom MAE gain threshold is applied | Passed |

Test command:

```bash
python -m pytest partB_mlflow/test_gate.py -v
```

Result: **7 passed**.

### 3.4 Promotion gate results

The final gate was run after registering the models under the required name `eta-model`.

Evaluation dataset: 2,000 rows, generated with seed 99.

| Metric | Production (`v1`) | Candidate (`v2`) |
|---|---:|---:|
| Overall MAE | 11.9553 | 6.1049 |
| Rush-hour MAE | 19.6799 | 8.5795 |
| Rain MAE | 11.6227 | 6.2853 |

The candidate's relative overall MAE improvement was approximately **48.94%**.

The candidate's rush-hour MAE was approximately **56.40% lower**, and its rainy-slice MAE was approximately **45.92% lower**, than production.

The two required gate configurations produced the following results:

| Gate configuration | Required overall MAE gain | Candidate p95 latency | Decision |
|---|---:|---:|---|
| Default | 3% | 0.927 ms | **Promote** |
| Strict | 60% | 2.173 ms | **Do not promote** |

For the default gate, all four criteria passed. For the strict gate, the latency and both slice checks passed, but the candidate's 48.94% overall improvement did not reach the 60% requirement. Therefore, the final decision was correctly rejected.

The two latency measurements differ because they were measured in separate runs. Both were below the 10 ms threshold.

## 4. B3 — Serving and comparing production and candidate

### 4.1 Two independent MLflow servers

I served the two registry aliases using separate MLflow scoring servers:

| Server | Model URI | Port |
|---|---|---:|
| Production | `models:/eta-model@prod` | 5101 |
| Candidate | `models:/eta-model@candidate` | 5102 |

Both servers started successfully. I checked the `/ping` endpoint on each server and received HTTP 200 responses.

### 4.2 Comparison procedure

I created `partB_mlflow/compare_servers.py` to compare the two deployed endpoints.

The script:

1. Reads `data/eta_fresh.csv`.
2. Selects the first 200 rows and only the seven model input features.
3. Sends each row individually to the production `/invocations` endpoint.
4. Sends the exact same 200 rows individually to the candidate `/invocations` endpoint.
5. Records each response's prediction and request latency.
6. Calculates the mean absolute difference between corresponding predictions.
7. Calculates the percentage of requests with an absolute prediction difference greater than 10 minutes.
8. Calculates each server's p95 request latency.
9. Records the model versions returned by the servers.

The target column `eta_minutes` from the CSV is not sent as an input feature.

### 4.3 Serving comparison results

| Metric | Production (`v1`) | Candidate (`v2`) |
|---|---:|---:|
| Requests | 200 | 200 |
| p95 request latency | 3.509 ms | 3.659 ms |
| Model version observed | `v1` | `v2` |

Prediction comparison:

| Comparison metric | Result |
|---|---:|
| Mean absolute prediction difference | 9.4845 minutes |
| Requests differing by more than 10 minutes | 35.0% |

Both servers returned the expected model version for their respective aliases. The candidate server had a slightly higher p95 latency in this run: 3.659 ms compared with 3.509 ms for production.

The mean absolute prediction difference of 9.4845 minutes shows that the two models do not produce identical ETAs. In 35% of the tested requests, their predictions differed by more than 10 minutes. This prediction-difference analysis complements the offline MAE comparison: it measures how much the candidate changes individual outputs relative to the currently served production model.

The results were saved to:

```text
partB_mlflow/results/server_comparison.json
```

## 5. Model alias updates and running services

An MLflow alias is a mutable registry reference. For example, `eta-model@prod` can be reassigned from one registered model version to another.

However, changing an alias does not automatically replace the model object already loaded into a running scoring-server process. A server that loaded a model at startup generally continues serving that loaded model until it is restarted or explicitly reloaded.

Two deployment approaches can update a model without dropping incoming requests:

### Approach 1 — Rolling deployment

1. Start a new server using the updated model version, while the old server continues handling traffic.
2. Check that the new server is healthy and ready.
3. Redirect new traffic to the new server.
4. Allow in-flight requests on the old server to finish.
5. Shut down the old server after it has drained.

### Approach 2 — In-process model replacement

1. Load the new model into memory while the old model remains available.
2. Validate the newly loaded model.
3. Atomically switch the active model reference.
4. Allow requests already using the old model to complete.
5. Release the old model when it is no longer being used.

Both approaches require deployment or application-level traffic and lifecycle management; changing the MLflow alias alone is not a zero-downtime deployment mechanism.

## 6. Findings and conclusion

Part B established an MLflow-based model management and deployment workflow for SwiftParcel.

The main findings were:

- Both model bundles were logged and registered under the required `eta-model` registry name.
- The `prod` and `candidate` aliases correctly resolved to registry versions 1 and 2.
- The custom PyFunc wrapper provided a consistent output schema containing one-decimal ETA predictions and model-version identifiers.
- The promotion gate evaluated overall error, rush-hour error, rainy-condition error, and candidate inference latency.
- All seven unit tests passed, including tests immediately above and exactly at decision boundaries.
- On the 2,000-row evaluation dataset, the candidate improved overall MAE by approximately 48.94%.
- The default 3% promotion gate approved the candidate, while the stricter 60% gate rejected it.
- Both serving endpoints responded successfully and served the expected model versions.
- The 200-request comparison measured a 9.4845-minute mean absolute prediction difference, with 35% of requests differing by more than 10 minutes.

Overall, this part demonstrated how model registration, alias-based versioning, automated promotion criteria, and independent model serving can make model releases more measurable and controlled. The promotion gate provides an explicit decision rather than relying only on a single aggregate accuracy metric.

## 7. Limitations and notes

- The serving comparison used the first 200 rows of `data/eta_fresh.csv`; it is a finite experiment, not a guarantee of production-wide behaviour.
- The p95 values are measurements from this machine and these local server processes. They should not be interpreted as cloud or production infrastructure benchmarks.
- The promotion gate measures candidate single-row latency sequentially on the local machine.
- The two serving servers were tested independently and sequentially, not under a shared high-concurrency production traffic load.
- The MLflow PyFunc logging process emitted non-fatal warnings about type hints, integer input schema handling, and CloudPickle serialization. Model logging, registration, loading, and prediction nevertheless completed successfully.
