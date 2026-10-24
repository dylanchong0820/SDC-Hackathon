#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Use the same supported target directory as the verified post-merge setup.
# This step installs dependencies only. Never apply database DDL during publish.
site_packages="$(python -c 'import site; print(site.getusersitepackages())')"
python -m pip install --disable-pip-version-check --no-input --upgrade \
  --target "$site_packages" -r requirements.txt
python -m compileall -q src
