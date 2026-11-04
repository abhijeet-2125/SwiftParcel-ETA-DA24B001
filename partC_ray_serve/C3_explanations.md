
# Part C3 — Design Decisions and Justifications

## 1. Why Python's Built-in `hash()` Is Not Suitable

Python's built-in `hash()` is not guaranteed to produce the same result across different Python processes or application restarts. Python uses hash randomization, so the same string can receive different hash values in separate processes. If customer routing depends on this value, a customer may be assigned to production in one process and candidate in another.

SwiftParcel therefore uses a deterministic cryptographic hash (`hashlib.blake2b`) to generate a stable bucket for each customer ID. The bucket is independent of the process, replica, and deployment lifecycle. This ensures consistent customer assignment even after restarting or redeploying the Ray Serve application.

## 2. Why Customer-Level Stickiness Matters

**ETA consistency:** A customer should not switch between model versions on successive requests. Switching models could produce inconsistent delivery estimates for the same customer and make the application experience unpredictable.

**Reliable experiment analysis:** Customer-level stickiness ensures that repeated observations from one customer remain associated with the same experiment arm. This reduces contamination between production and candidate groups and makes comparisons of model performance more meaningful.

SwiftParcel achieves this by hashing the customer ID rather than individual requests. Increasing the candidate allocation from 20% to 50% also preserves existing candidate assignments because the hash threshold only increases.

## 3. Ray Serve Autoscaling Configuration

The candidate deployment uses a minimum of one replica and a maximum of three replicas. The minimum keeps the candidate model available, while the maximum limits resource consumption on the two-CPU machine. The `target_ongoing_requests` value of 5 encourages scaling when replicas experience concurrent request load. An upscale delay of 5 seconds allows demand to persist before adding capacity, while a downscale delay of 30 seconds avoids removing replicas too quickly during short traffic fluctuations.

The production deployment uses two fixed replicas to provide parallel request handling without autoscaling overhead. Fractional CPU allocation allows the production model, candidate model, and router to coexist within the available two-CPU Ray cluster.
