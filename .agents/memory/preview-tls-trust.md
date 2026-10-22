---
name: Preview TLS trust
description: Trust-store differences when Python checks the proxied development app.
---

Use the system certificate trust store for Python HTTP checks against the
Replit development preview. Do not disable TLS verification to make a check pass.

**Why:** In this environment the preview proxy's certificate chain is trusted by
the system bundle but not by the installed PyPI certifi bundle. Requests' default
verification can fail even when the preview is healthy and curl succeeds.

**How to apply:** For development verification scripts, use the system CA bundle
at `/etc/ssl/certs/ca-certificates.crt`. This does not establish that external
Meta API traffic has the same trust-store issue; check that separately if needed.
