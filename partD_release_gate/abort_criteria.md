
# Part D2 — When Should We Stop a Release?

A new ETA model may perform well during testing, but that does not guarantee that it will behave equally well when real customers start using it. During a rollout, I would continuously monitor the service and stop the release if I notice any of the following problems.

| No. | What I would monitor | When I would stop the release | How long I would observe it | What I would do |
|---|---|---|---|---|
| 1 | API response time | If p95 latency goes above 80 ms | Continuously for 5 minutes | Stop increasing candidate traffic and move users back to the stable production version. |
| 2 | API failures | If more than 1% of requests result in server errors (HTTP 5xx) | A rolling 5-minute window with at least 500 requests | Stop the rollout and investigate why the service is failing. |
| 3 | Overall ETA accuracy | If the candidate model's MAE becomes more than 5% worse than production | At least 500 labelled delivery observations | Reject the candidate model and continue using the production model. |
| 4 | Accuracy during difficult conditions | If the candidate's MAE is more than 10% worse during rush hours or rainy conditions | At least 100 observations in the affected condition | Stop the rollout and investigate the regression before trying again. |

## What happens after an abort?

If any of these conditions is met, I would:

1. Immediately stop increasing traffic to the candidate model.
2. Send traffic back to the last stable production version.
3. Check the logs and monitoring data to understand what went wrong.
4. Identify whether the problem came from model predictions, application errors, or infrastructure.
5. Run the release checks again before attempting another rollout.

## Why these four checks?

I chose these checks because a model release should not be judged only by prediction accuracy.

- **Latency:** Customers should not have to wait longer just because we deployed a new model.
- **Error rate:** The prediction service must remain available and handle requests reliably.
- **Overall accuracy:** The new model should actually improve ETA predictions rather than make them worse.
- **Rush-hour and rainy-condition accuracy:** A model might perform well on average but still struggle under specific delivery conditions. I want to catch those problems before they affect too many customers.

These thresholds are the release limits I propose for SwiftParcel. They are operational safeguards, not measurements obtained from the experiments.
