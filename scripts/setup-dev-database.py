#!/usr/bin/env python
"""Apply the checked-in schema to DEVELOPMENT only; never a deploy/build hook."""
import os
from pathlib import Path

import psycopg


if os.getenv("REPLIT_DEPLOYMENT") == "1":
    raise SystemExit("Use Replit Publish to apply the production schema.")
with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as db:
    db.execute((Path(__file__).resolve().parents[1] / "schema/postgres.sql").read_text())
print("Development schema applied.")
