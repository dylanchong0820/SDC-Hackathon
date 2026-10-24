---
name: Trustworthy partial comparisons
description: Why partial comparison output must not be treated as a completed recommendation.
---

Expose only validated per-source facts while a comparison is running, not unfinished model JSON or an interim winner.

**Why:** Quotations can finish at different times or fail independently. An early price is not evidence that its supplier is best across all selected sources, and an unfinished structured response may not parse or identify its source correctly.

**How to apply:** Preserve completed facts after another source fails, but withhold final ranking, currency-comparison claims, and staff selection until the comparison reaches its final result.

Recover failed sources in a separate, linked comparison rather than replacing the original result or carrying its staff choice forward.

**Why:** A recovered quotation can change the recommendation. The team's earlier choice applied to the earlier evidence and must not silently become approval of a changed comparison.

**How to apply:** Keep the original result and choice intact, require a separate choice for the retry, and distinguish changed rankings from the first final ranking after a stopped or failed attempt.

Preserve retry provenance and its fixed original ranking baseline when refreshing derived currency results.

**Why:** A valid delivery-only extraction has no price, so automatic currency checks can still rerank a completed retry. Replacing its entire saved result erases the retry history even though its extraction facts remain valid.

**How to apply:** Currency refreshes must retain retry metadata and update change notices against the original baseline, not treat the last refresh as a new comparison.
