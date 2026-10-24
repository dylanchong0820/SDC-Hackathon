# Quote Bot AI

The existing Python/Flask app now serves both the WhatsApp Cloud API webhook and
the staff website. Keep the Python stack and the original local archive.

## Running

- Workflow: `Start application` → `python src/main.py` (uses `exec` so restarts do not leave stale listeners).
- Listen on `0.0.0.0:5000`; website `/`, webhook `/webhook`, health `/health`.
- Runtime dependencies: `requirements.txt`. Optional browser-test tooling:
  `requirements-dev.txt`.
- Quote AI PDF downloads use headless Chromium from `PATH`; generated files are
  temporary and are not written to `whatsapp_data/`.
- Tests: `python -m unittest discover -s tests -v`.
- Browser smoke test: `python tests/browser_smoke.py` (Playwright and a Chromium
  browser are required; see `CHROMIUM_PATH` in that script).

## Replit Secrets

- `SESSION_SECRET`: long random secret for signed staff sessions.
- `STAFF_PASSWORD`: shared staff login password, at least 12 characters.
- `META_ACCESS_TOKEN`: the existing Meta WhatsApp Cloud API access token with
  WhatsApp messaging/management permissions.
- `WEBHOOK_VERIFY_TOKEN`: matches the Verify token entered in Meta.
- `META_APP_SECRET`: Meta App Dashboard → App settings → Basic → App secret.
  Required to validate `X-Hub-Signature-256` on incoming POSTs. This is NOT the
  verify token. Unsigned or incorrectly signed webhook events are rejected.

Never put credentials into Git, chat, the website's settings, or browser storage.
The website only accepts the staff password at sign-in. Changing that password
invalidates older sessions after the app restarts.

## Shared employee access

Multiple employees must be able to use the inbox simultaneously with the same
staff password. Each device/browser has an independent session and CSRF token;
signing in or out on one must not disconnect other employees. All employees
share the business inbox and permissions. Separate tabs in the same browser
profile share that browser's session.

## Connecting Meta

1. Add the secrets and restart the workflow.
2. Sign in at `/`. Settings accepts the **phone number ID**, not the displayed
   phone number. Find it in Meta App Dashboard → WhatsApp → API Setup.
   The receiver also learns this ID automatically from inbound webhook metadata.
3. Configure the public site's HTTPS origin + `/webhook` as the callback URL;
   enter the same verify token as `WEBHOOK_VERIFY_TOKEN`. Subscribe the WhatsApp
   Business Account to `messages` events and the app as needed in Meta.
4. A customer sends a message to the business number. The website polls updates.
   Meta's test-number recipient restrictions still apply if using a test number.

The UI configuration indicators mean a value exists, not that Meta accepted or
verified it. The app never imports historical messages from Meta: that API is
send-only, and only future subscribed events reach this receiver. Staff can
search saved message text and import a one-to-one WhatsApp UTF-8 `.txt` export
(maximum 8 MB). Imports are text-only; exported media files are not added.
Imported rows are deduplicated, appended to the readable text archive, and
never advance the live inbound timestamp or activate a reply window.

## Data and behavior

- Original archive: `whatsapp_data/messages.txt`, append-only readable text.
- Original attachment location: `whatsapp_data/attachments/<type>/`.
- New SQLite index/job queue: `whatsapp_data/inbox.sqlite3`, beside the original
  archive. Existing original-format text logs are indexed once without rewriting.
- Incoming image/document/audio/video/sticker messages are queued immediately,
  saved asynchronously, and retried up to three times. Failed downloads can be
  retried from the website. PDFs and arbitrary WhatsApp-supported documents retain
  their file extensions and names. Unique filenames avoid same-second overwrites.
- Selected scanned PDFs use local Tesseract OCR on at most the first five pages,
  bounded by 90 seconds. Local page-count warnings appear in source review and
  saved comparisons; unknown page counts are disclosed rather than assumed safe.
  Coverage warnings are part of the reviewed text and remain independent of AI output.
  Selected legacy XLS files are converted from a temporary
  copy with `ssconvert`; original WhatsApp attachments are never rewritten.
  `tesseract` and `gnumeric` are declared in `.replit`.
- Quotation previews require staff authentication and CSRF. Preview performs only
  local extraction (two-minute batch budget, two concurrent previews per process).
  Comparison requests require a session-bound, signed review token valid for ten
  minutes. Only confirmed successful sources can be queued, and source digests are
  checked again before sending content to RunPod. Preview text is not saved.
- Other types retain raw message content (contacts, location, reactions,
  interactive replies, orders, system/future types). Raw payloads are staff-only.
- Quote AI Chat sends saved completed comparison facts to the configured RunPod model on each request. It omits phone numbers, message IDs, raw attachment files, and unsaved inbox content. Each answer generates a four-column PDF in the response; PDFs and temporary chat transcripts are not stored in inbox history.
- Staff can stop queued or running comparisons. Cancellation prevents an in-flight result from being saved; the configured OpenAI-compatible RunPod endpoint does not expose a job ID or remote cancellation route, so RunPod may continue that request until it finishes or times out.
- Text sends: at most 4096 characters, only within the 24-hour customer-service
  window since an inbound customer message. Starting an empty conversation does
  not send anything or open that window. Approved-template sends are not part of
  this website. Meta acceptance is shown separately from delivered/read.
- Media routes require staff authentication, check archive path containment, and
  download documents/untrusted content rather than execute it inline.
- Direct Meta Graph requests use the existing secret and `requests`; the connected
  integration's documented Python SDK was unavailable from the package registry.
  Graph version defaults to `v23.0` (`META_GRAPH_VERSION` can override it).
- No customer data is seeded into the real app. Tests use isolated temporary
  directories, fake Meta clients and test-only credentials.
- Employees may explicitly clear a single chat's history or the entire inbox,
  with typed confirmation and optional attachment-file deletion. This affects
  all staff, not WhatsApp's own messages. Keeping attachments preserves files
  without inbox links; a later cleanup can remove them. Contacts/settings and
  the real reply window are kept. Do not delete live data during testing.
- SQLite connection lifecycles are serialized within each server process to
  prevent runtime open/close mutex stalls under concurrent staff/media access.
  Busy archives return HTTP 503 with Retry-After instead of waiting indefinitely.
  Never hold a database context around network requests or attachment downloads.

## Production cautions

The workflow still uses Flask's development server. A production WSGI server is a
separate queued project task; when adding it, use `src.server:create_app()` as the
factory. Download jobs use SQLite leases, allowing recovery after a crash.

The archive and SQLite are workspace-local to preserve the original receiver's
storage behavior. They are NOT an assurance of durable storage on a published
instance. Before production use, configure persistent storage/App Storage plus a
durable database and backup/retention policies. Do not deploy or commit private
archives. `.gitignore` and `.replitignore` exclude `whatsapp_data/`.

## Git scope

Only push/delete branches on `origin` (the owner's fork). Treat any `upstream`
remote as read-only. Do not alter the internal Replit backup remote.
