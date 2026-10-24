---
name: Comparison timing trust
description: Limits of comparison elapsed-time measurements and remote queue attribution.
---

Treat RunPod inference timing as end-to-end request wait, not GPU execution time
or proof of a busy endpoint. Local queue wait is distinct from RunPod's queue.
Use elapsed time for overlapping requests, not the sum of request durations.

**Why:** The OpenAI-compatible endpoint does not provide a remote job ID or
reliable queue/execution split. Summing concurrent request durations would
overstate how long staff actually waited and misidentify the slowest step.

**How to apply:** Keep those limits explicit when adding progress metrics or
diagnostic advice. Do not infer remote queue depth or GPU utilization solely from
elapsed request time.
