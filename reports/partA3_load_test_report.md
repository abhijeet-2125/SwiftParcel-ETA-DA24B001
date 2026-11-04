# Part A3 — Load Testing and Performance Analysis

## 1. Experimental Setup

The SwiftParcel ETA prediction API was benchmarked using the provided `load_test.py` utility. The API was served using Uvicorn, and requests were sent to:

`POST /v1/models/eta:predict`

The experiments used the generated fresh dataset (`data/eta_sample.csv`) and measured request throughput, latency percentiles, and errors.

**Hardware and software environment**

| Specification | Value |
|---|---|
| CPU cores | 10 |
| Architecture | ARM64 (Apple Silicon) |
| RAM | 16 GB |
| Operating system | macOS 27.0.1 |
| Python | 3.14.8 |
| API framework | FastAPI |
| Server | Uvicorn |

Each concurrency configuration was tested for 4 seconds with batch size 1. Concurrency levels were 1, 2, 4, 8, and 16.

## 2. Baseline Performance

The initial API configuration used one Uvicorn worker.

| Concurrency | Requests | Throughput (RPS) | p50 (ms) | p95 (ms) | p99 (ms) | Errors |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1767 | 441.8 | 2.1 | 2.9 | 4.0 | 0 |
| 2 | 3023 | 755.8 | 2.5 | 3.6 | 4.7 | 0 |
| 4 | 2607 | 651.8 | 5.7 | 7.9 | 11.8 | 0 |
| 8 | 3209 | 802.2 | 9.0 | 14.7 | 19.3 | 0 |
| 16 | 2953 | 738.2 | 19.3 | 33.2 | 81.7 | 0 |

The highest observed throughput was 802.2 requests per second at concurrency 8. Throughput declined at concurrency 16 while latency increased, indicating emerging contention.

All tested configurations met the p95 latency SLO of 80 ms.

## 3. Batch-Size Experiment

To evaluate batching efficiency, the API was tested at concurrency 1 using batch sizes of 1 and 100.

| Metric | Batch 1 | Batch 100 |
|---|---:|---:|
| Concurrency | 1 | 1 |
| Requests | 1767 | 1487 |
| Throughput (requests/s) | 441.8 | 371.8 |
| p50 latency/request (ms) | 2.1 | 2.6 |
| p95 latency/request (ms) | 2.9 | 3.4 |
| p99 latency/request (ms) | 4.0 | 4.1 |
| Errors | 0 | 0 |

Using median request latency, the amortized latency per row is:

- **Batch 1:** 2.1 / 1 = 2.1 ms per row.
- **Batch 100:** 2.6 / 100 = 0.026 ms per row.

Batching 100 predictions increased median request latency only slightly while processing 100 times as many rows per request. This substantially reduced the amortized request latency per prediction. These figures represent amortized API latency, not isolated model-computation time.

## 4. Performance Optimization Experiments

Two changes were evaluated.

### Experiment 1: Synchronous endpoint

The prediction endpoint was changed from `async def` to a regular synchronous `def`. This allows FastAPI to execute the endpoint in its thread pool.

The existing 16 automated API tests were rerun, and all 16 passed.

However, the benchmark did not show a throughput improvement.

| Concurrency | Original RPS | Synchronous endpoint RPS |
|---:|---:|---:|
| 1 | 441.8 | 430.0 |
| 2 | 755.8 | 703.8 |
| 4 | 651.8 | 630.8 |
| 8 | 802.2 | 749.0 |
| 16 | 738.2 | 683.5 |

The synchronous endpoint experiment was therefore not retained as a demonstrated performance improvement.

### Experiment 2: Two Uvicorn workers

The server was restarted with two worker processes:

`uvicorn partA_rest_api.eta_app:app --host 127.0.0.1 --port 8000 --workers 2`

Each worker loads its own model instance. The same load-test settings were used.

| Concurrency | Requests | Throughput (RPS) | p50 (ms) | p95 (ms) | p99 (ms) | Errors |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2968 | 742.0 | 1.3 | 1.8 | 2.4 | 0 |
| 2 | 4266 | 1066.5 | 1.9 | 2.3 | 2.8 | 0 |
| 4 | 6839 | 1709.8 | 2.3 | 2.9 | 3.7 | 0 |
| 8 | 7319 | 1829.8 | 4.3 | 5.4 | 6.8 | 0 |
| 16 | 7087 | 1771.8 | 8.8 | 12.1 | 15.9 | 0 |

The two-worker configuration achieved a maximum observed throughput of 1829.8 RPS at concurrency 8, approximately 2.28 times the original peak throughput of 802.2 RPS.

At concurrency 16, throughput declined slightly and p95 latency increased from 5.4 ms to 12.1 ms. This suggests that the system was approaching saturation beyond concurrency 8.

The two-worker configuration is the best-performing configuration among the experiments conducted.

## 5. Saturation Point and Little's Law

The highest measured throughput was 1829.8 RPS at concurrency 8 using two Uvicorn workers. Increasing concurrency to 16 did not increase throughput; instead, throughput declined to 1771.8 RPS and latency increased.

Therefore, concurrency 8 is the observed throughput peak among the tested configurations. Under the required p95 SLO of 80 ms, the highest measured service capacity was 1829.8 requests per second. This is an experimentally observed capacity, not a guarantee of production capacity.

Little's Law describes the relationship between average in-flight requests (L), throughput (λ), and average time in the system (W):

**L = λ × W**

As concurrency increases, throughput initially rises because more requests can be processed simultaneously. Once the system approaches its processing capacity, additional concurrent requests mainly increase waiting time rather than throughput.

## 6. Why p95 and p99 Matter

Mean latency can hide slow requests because a small number of very slow responses may have little effect on the average. The p95 latency indicates the threshold within which 95% of requests complete, while p99 captures the slower tail experienced by the slowest 1% of requests. These percentiles are therefore useful for evaluating API responsiveness and identifying tail-latency problems.

## 7. Conclusion

The baseline SwiftParcel API achieved a peak measured throughput of 802.2 RPS. Changing the endpoint to synchronous execution did not improve the benchmark results, but deploying two Uvicorn workers increased peak throughput to 1829.8 RPS, approximately 2.28 times the original result.

All benchmark runs completed without request errors, and every tested configuration satisfied the p95 latency target of 80 ms. The two-worker configuration provided the strongest measured performance and is selected as the improved deployment configuration for this experiment.

The measurements were collected on a local Apple Silicon machine over short four-second benchmark windows. Results may vary with hardware, operating-system scheduling, process startup, and workload characteristics. Longer-duration and repeated tests would be required to establish stable production capacity.