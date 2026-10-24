---
name: Currency comparison trust
description: Why saved MYR conversions remain fixed and why rate-feed date checks must not be relaxed.
---

Keep supplier amounts and currencies intact and preserve successful dated MYR conversion snapshots. Do not silently revalue a saved comparison just because market rates have changed.

**Why:** Employees must be able to understand the prices on which an earlier team choice was based. Reopening the same comparison must not silently change its financial meaning.

**How to apply:** New comparisons can use newer reference rates. A future refresh feature should be an explicit action that preserves the earlier snapshot and team choice, not an automatic rewrite. Missing-rate retries are different from replacing an already valid snapshot.

Validate the effective date of each exchange rate rather than trusting a feed's “latest” label.

**Why:** During live verification on 2026-10-24, Frankfurter's broad feed returned future-dated common-currency rows, while its ECB-compatible feed supplied valid earlier rates. Rejecting those future rows was intentional; weakening date validation would have concealed the problem.

**How to apply:** Keep independently validated coverage for common currencies and clearly disclose unavailable conversions. If simplifying the feeds later, verify their current behavior first; never accept future or excessively stale rates merely to make conversion succeed.
