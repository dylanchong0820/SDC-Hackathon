---
name: Python package installation on Replit
description: Environment-specific behavior of Python dependency installation in this workspace.
---

This workspace's Python runtime uses `.pythonlibs` and disables user-site installs. The managed language-package installer attempted a `pip --user` install and failed; the indexed Nix lookup also did not include the requested Python 3.11 reader packages.

**Why:** Document readers could not be installed through the supported package paths during the quotation-analysis work. The implementation uses standard-library Office text readers and the system `pdftotext` and `antiword` utilities already available in this workspace.

**How to apply:** Before adding Python dependencies, verify that the managed installer works in the current environment. Do not bypass it with shell `pip`; use an indexed system package when available or a safe standard-library option, and confirm any external utility exists in the target deployment.
