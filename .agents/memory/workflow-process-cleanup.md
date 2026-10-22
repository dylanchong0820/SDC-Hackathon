---
name: Workflow process cleanup
description: Avoid orphaned child servers and occupied preview ports on workflow restart.
---

For a shell workflow that launches a long-running Python webserver, use `exec`
in the command so the server replaces the shell process and receives workflow
termination signals directly.

**Why:** A workflow restart previously left a Python child alive on port 5000,
so the replacement process failed with “Address already in use.”

**How to apply:** After configuring a Python webserver workflow, verify that a
restart leaves exactly one server process, the expected port answers, and the
development preview returns HTTP 200.
