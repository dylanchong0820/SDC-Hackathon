---
name: Trustworthy partial comparisons
description: Why partial comparison output must not be treated as a completed recommendation.
---

Expose only validated per-source facts while a comparison is running, not unfinished model JSON or an interim winner.

**Why:** Quotations can finish at different times or fail independently. An early price is not evidence that its supplier is best across all selected sources, and an unfinished structured response may not parse or identify its source correctly.

**How to apply:** Preserve completed facts after another source fails, but withhold final ranking, currency-comparison claims, and staff selection until the comparison reaches its final result.
