# Production operation

## Storage and privacy

The published service uses PostgreSQL for messages, settings, comparisons,
deletion tombstones, media job leases, and verbatim readable archive entries.
Attachments are private objects in the project's App Storage bucket. Attachment
database references are opaque object keys, not workspace filesystem paths.

`whatsapp_data/cloud-cache/` contains disposable downloaded copies and a regenerated
`messages.txt`. Losing or replacing this directory must not lose authoritative
data. Staff can download the readable archive in Settings. Media remains behind
the application's staff login; no public bucket links are created.

The original `whatsapp_data/inbox.sqlite3`, `messages.txt` and attachment files
are preserved during the one-time development import. They are private migration
originals, not the live backend, and remain excluded from Git and publishing.
Do not delete them until the imported counts/files have been verified. Normal
history deletion affects the live backend, not these offline originals; review
and dispose of migration originals separately under the team's retention policy.

Deletion without attachments removes messages/log entries while retaining files.
Deletion including attachments queues remote deletion durably. If object storage
is temporarily unavailable, staff see the outstanding count and the media worker
retries. Keep the service running until pending deletion counts return to zero.

## Schema, initial data and publishing

- `schema/postgres.sql` is the development schema source of truth.
- Development-only setup: `python scripts/setup-dev-database.py`.
- Stop the SQLite-backed server before the **one-time development-only** import:
  `python scripts/import-local-archive.py --confirm-development`.
- Select `STORAGE_BACKEND=postgres` after the import and restart the service.
- Never run either setup/import command against production. Replit Publish
  manages the production schema. The production startup and build contain no DDL.
- Publishing schema changes does **not** by itself transfer development records.
  If existing inbox history must appear on the first published instance, use the
  Publish UI's development-data copy option and review its scope. Do not overwrite
  an established production database on later publishes.
- Keep this project's App Storage bucket attached to the published service.
  Metadata alone cannot restore attachment contents from another bucket.
- Development and production use different private object prefixes. When the
  Publish UI copies development records into production, startup copies the
  referenced objects into the production prefix before serving staff. It never
  deletes the source environment's files. Allow time for this first-start copy.

## Runtime

Use **Reserved VM**, one Gunicorn process and eight threads. The app has local
comparison workers, so do not use multiple Gunicorn processes, autoscaling
instances, `--preload`, or scale-to-zero for the website without redesigning job
ownership/recovery. RunPod may independently retain its scale-to-zero policy.

Build: `bash scripts/build-production.sh`

Run: `python -I -S scripts/start-production.py`

The build creates `.production-deps/` inside the project and the launcher imports
from that exact project-relative folder. It is excluded from Git, but deliberately
not excluded from publishing. Do not move production dependencies back into
`.pythonlibs/`, a user-site directory or another publish-excluded path.

The build checks imports and the Gunicorn launcher with isolated Python (`-I -S`),
without workspace packages, user-site discovery or `PYTHONPATH`. It preserves the
Gunicorn configuration and production application factory. Use
`python -I -S scripts/start-production.py --version` for a dependency/launcher
check that does not start the application. Builds never connect to databases,
initialize storage, run migrations or start AI jobs.

The production factory refuses local-only storage, incomplete credentials and
missing bucket access. Configure the project's secrets in Publishing:
`SESSION_SECRET`, `STAFF_PASSWORD`, `META_APP_SECRET`, `META_ACCESS_TOKEN`,
`WEBHOOK_VERIFY_TOKEN`, `RUNPOD_API_KEY`, plus the managed production `DATABASE_URL`.
The staff password must be at least 12 characters. Preserve `SESSION_SECRET`
across restarts unless intentionally signing out all staff.

Set the non-secret `WHATSAPP_PHONE_NUMBER_ID` to seed the sending number when
starting with an empty database. Sessions remain staff-password protected. The
site itself must be publicly reachable for Meta webhook delivery; Replit
deployment privacy/password gates would block Meta before the app can validate
its signature. Configure Meta's callback as the verified published URL followed
by `/webhook`; do not keep the workspace preview as the live callback.

## Recovery and verification

After publishing, verify staff login, conversation/history access, a saved media
download, the archive export, a synthetic comparison, and Meta webhook delivery.
Startup marks interrupted local comparisons failed, preserving partial results
for an explicit staff retry; it does not claim exactly-once AI inference.

Durability is not an independent backup. Agree a retention/backup policy for
database and private attachments, keep exports private, and test a restore before
relying on backup copies. Never check customer archives or database exports into
Git. Deleting live history does not erase separately retained backups.

See `AI_MODEL_ATTRIBUTION.md` for the commercially permitted external image model.
