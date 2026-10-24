#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

site_packages="$(python -c 'import site; print(site.getusersitepackages())')"
python -m pip install \
  --disable-pip-version-check \
  --no-input \
  --upgrade \
  --target "$site_packages" \
  -r requirements.txt

python -m compileall -q src

if [[ "${STORAGE_BACKEND:-sqlite}" == "postgres" && "${REPLIT_DEPLOYMENT:-}" != "1" ]]; then
  python scripts/setup-dev-database.py
fi
