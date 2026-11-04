
# Part D Report — Release Smoke Testing and Deployment Safety

## 1. Objective

The objective of Part D was to build a release-checking mechanism for the SwiftParcel ETA prediction service.

A model can perform well during development, but before allowing customers to use it, I need to make sure that the deployed service is healthy, returns sensible predictions, rejects invalid requests, and responds within an acceptable time.

For this, I implemented an automated smoke-test script and defined four conditions that would make me stop a release if the new version started affecting service quality.

## 2. D1 — Automated Release Smoke Test

### 2.1 Implementation

The smoke-test script is implemented in:

`partD_release_gate/smoke_eta.py`

The script accepts three command-line arguments:

| Argument | Purpose |
|---|---|
| `--url` | URL of the running ETA service |
| `--expect-version` | Model version expected from the service |
| `--max-p95-ms` | Maximum acceptable p95 response latency |

Instead of manually checking individual endpoints, I wanted one command that could quickly tell me whether the service was ready to be released.

The script performs six checks:

1. **Health check:** Confirms that the `/healthz` endpoint responds with HTTP 200.
2. **Model version check:** Verifies that the service is actually using the expected model version.
3. **ETA sanity check:** Checks that the predicted ETA is present, positive, finite, and within a reasonable upper bound.
4. **Distance sanity check:** Compares predictions for otherwise identical 5 km and 25 km deliveries. The longer trip should not produce a shorter ETA.
5. **Invalid input check:** Sends an invalid negative distance and verifies that the API rejects it with HTTP 422.
6. **Latency check:** Sends 100 individual prediction requests and checks whether p95 latency stays below the configured limit.

The script records the result of every check and exits with code `0` if all checks pass, or `1` if any check fails. This makes it suitable for use in an automated deployment pipeline.

### 2.2 Test Execution

I ran the smoke test against the Part A production API running locally on port 8000.

Command used:

```bash
python -m partD_release_gate.smoke_eta \
  --url http://127.0.0.1:8000 \
  --expect-version v1 \
  --max-p95-ms 80
```

### 2.3 Observed Results

| Check | Observed result | Status |
|---|---:|---|
| Health endpoint | HTTP 200 | PASS |
| Expected model version | v1 | PASS |
| ETA sanity | 49.5 minutes | PASS |
| Distance sanity | 5 km: 30.1 min; 25 km: 81.7 min | PASS |
| Invalid input rejection | HTTP 422 | PASS |
| p95 latency | 5.79 ms | PASS |

<text>
</text>

The recorded run completed with:

- Total checks: 6
- Passed: 6
- Failed: 0
- p95 latency: 5.79 ms
- Configured latency limit: 80 ms
- Request errors during latency testing: 0

**Final result: RELEASE GATE — PASS**

The output was saved in:

`partD_release_gate/results/smoke_test_v1.log`

### 2.4 Observations

The service passed all six checks. It returned the expected `v1` model version, produced sensible ETA values, and correctly rejected invalid input.

The measured p95 latency was 5.79 ms, comfortably below the 80 ms limit. There were no failed requests during the 100-request latency test.

One important point is that this test was performed against the Part A production API. It confirms the behavior of that running endpoint, not the Ray Serve deployment from Part C.

## 3. D2 — Release Abort Criteria

### 3.1 Why Abort Criteria Are Needed

Passing a smoke test does not guarantee that a model will continue performing well under real traffic.

For example, a candidate model might have good overall accuracy but perform poorly during rush hours. Similarly, a service might work correctly during initial testing but become slow or unstable after traffic increases.

To handle these situations, I defined four conditions that would make me stop a rollout and return traffic to the last stable production version.

### 3.2 Proposed Abort Conditions

| No. | What I would monitor | When I would stop the release | Observation window |
|---|---|---|---|
| 1 | API response time | p95 latency exceeds 80 ms | 5 consecutive minutes |
| 2 | API failures | HTTP 5xx error rate exceeds 1% | Rolling 5-minute window with at least 500 requests |
| 3 | Overall ETA accuracy | Candidate MAE becomes more than 5% worse than production | At least 500 labelled deliveries |
| 4 | Rush-hour or rainy-condition accuracy | Candidate MAE becomes more than 10% worse than production in either affected slice | At least 100 observations in the affected slice |

### 3.3 What I Would Do If a Threshold Is Breached

If any of these conditions occurs, I would stop increasing candidate traffic and route users back to the stable production version.

I would then examine the service logs, error counts, latency measurements, and model predictions to understand the cause. The candidate would not be rolled out again until the issue was investigated and the release checks passed.

### 3.4 Why I Chose These Conditions

I chose these four conditions because I do not want to judge a model release only by its average prediction accuracy.

- **Latency matters:** Customers should not have to wait longer for an ETA because a new model was deployed.
- **Reliability matters:** The service should not start returning server errors when traffic increases.
- **Overall accuracy matters:** A candidate model should not make ETA predictions worse than the current production model.
- **Performance across different conditions matters:** Rush-hour traffic and rainy weather can make deliveries more difficult. A good average score should not hide poor performance in these situations.

These are proposed operational safeguards for SwiftParcel. They are not experimental results or thresholds measured during the smoke test.

## 4. Limitations

There are a few limitations to the current implementation:

1. The smoke test was executed against the local Part A API, not against a production cloud deployment.
2. The latency measurement used 100 sequential, single-row requests. It does not represent high-concurrency production traffic.
3. The ETA sanity checks are basic safeguards. They do not prove that every prediction is accurate.
4. The four abort conditions are documented release policies. Automated continuous monitoring and automatic rollback based on these conditions have not yet been implemented.
5. The smoke test checks the expected model version but does not independently verify the complete model artifact or its training provenance.

## 5. Files and Evidence

| File | Purpose |
|---|---|
| `partD_release_gate/smoke_eta.py` | Automated release smoke-test implementation |
| `partD_release_gate/results/smoke_test_v1.log` | Captured output of the successful smoke test |
| `partD_release_gate/abort_criteria.md` | Proposed release-abort conditions |
| `reports/partD_release_report.md` | Part D report |

## 6. Conclusion

Part D adds a final safety check before releasing a SwiftParcel ETA model.

The smoke-test script brings together service health, model-version validation, prediction sanity, invalid-input handling, and latency measurement in a single command. In the recorded test, all six checks passed, with a p95 latency of 5.79 ms against an 80 ms limit.

Along with this, I defined four practical release-abort conditions covering latency, server errors, overall model accuracy, and performance under difficult delivery conditions.

The main takeaway is that a model should not be considered ready just because its predictions look good. The service delivering those predictions must also remain reliable, responsive, and consistent for customers.
