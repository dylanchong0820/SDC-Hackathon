---
name: Python package installation on Replit
description: Environment-specific behavior of Python dependency installation in this workspace.
---

This workspace's Python runtime imports packages from `.pythonlibs`. A normal `pip --user` install is not supported, but the configured post-merge setup can synchronize `requirements.txt` by targeting `site.getusersitepackages()` explicitly.

**Why:** The post-merge hook needs a repeatable way to install project requirements after code merges; targeting the runtime's existing `.pythonlibs` location succeeded where a user-site install did not.

**How to apply:** For new dependencies, follow the package-management skill. For post-merge synchronization of already-declared requirements, use the configured setup hook and its target path; do not use an ad hoc `pip --user` install.
