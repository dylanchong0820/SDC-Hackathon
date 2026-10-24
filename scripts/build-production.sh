#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Ship a deterministic build artifact, not a user-site/workspace-only directory.
# .production-deps is Git-ignored but MUST NOT be excluded by .replitignore.
# Clear generated dependencies only; no customer files or databases are touched.
rm -rf -- "$PWD/.production-deps"
python -m pip install --disable-pip-version-check --no-input --upgrade \
  --target "$PWD/.production-deps" -r requirements.txt
python -m compileall -q src
# Prove imports work without workspace packages, user-site discovery or PYTHONPATH.
# Never apply database DDL or connect to production services during the build.
python -I -S scripts/verify-production.py
python -I -S scripts/start-production.py --version
