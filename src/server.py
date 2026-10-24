"""WhatsApp receiver and staff website. Run with: python src/main.py."""
from datetime import timedelta
from functools import wraps
import base64
import hashlib
import hmac
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
import time

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.serving import WSGIRequestHandler
from werkzeug.utils import secure_filename

if __package__:
    from .history_import import HistoryImportError, parse_whatsapp_export
    from .quotation_export import MIME_TYPE as QUOTATION_XLSX_MIME, build_quotation_workbook
    from .quote_analysis import QuoteAnalysisError, QuoteAnalysisWorker
    from .quote_chat import (
        QuoteChatContextTooLarge, QuoteChatInputError, QuoteChatOutputError,
        parse_model_output, prepare_model_messages,
    )
    from .quote_chat_pdf import QuoteChatPdfError, build_quote_chat_pdf
    from .storage import Store, StorageBusyError
    from .whatsapp import GraphClient, GraphError, MediaWorker, process_event
else:
    from history_import import HistoryImportError, parse_whatsapp_export
    from quotation_export import MIME_TYPE as QUOTATION_XLSX_MIME, build_quotation_workbook
    from quote_analysis import QuoteAnalysisError, QuoteAnalysisWorker
    from quote_chat import (
        QuoteChatContextTooLarge, QuoteChatInputError, QuoteChatOutputError,
        parse_model_output, prepare_model_messages,
    )
    from quote_chat_pdf import QuoteChatPdfError, build_quote_chat_pdf
    from storage import Store, StorageBusyError
    from whatsapp import GraphClient, GraphError, MediaWorker, process_event

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SESSION_SECRET"),
        STAFF_PASSWORD=os.getenv("STAFF_PASSWORD", ""),
        WEBHOOK_VERIFY_TOKEN=os.getenv("WEBHOOK_VERIFY_TOKEN", ""),
        META_APP_SECRET=os.getenv("META_APP_SECRET", ""),
        META_ACCESS_TOKEN=os.getenv("META_ACCESS_TOKEN", ""),
        META_GRAPH_VERSION=os.getenv("META_GRAPH_VERSION", "v23.0"),
        RUNPOD_API_KEY=os.getenv("RUNPOD_API_KEY", ""),
        RUNPOD_ENDPOINT_URL=os.getenv(
            "RUNPOD_ENDPOINT_URL", "https://by40gb92whagr1.api.runpod.ai"),
        DATA_DIR=PROJECT_ROOT / "whatsapp_data",
        MEDIA_WORKER=True,
        MAX_CONTENT_LENGTH=9 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    if config:
        app.config.update(config)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    store = Store(app.config["DATA_DIR"])
    graph = app.config.get("GRAPH_CLIENT") or GraphClient(
        app.config["META_ACCESS_TOKEN"], app.config["META_GRAPH_VERSION"])
    app.extensions["store"] = store
    app.extensions["graph"] = graph
    quote_worker = app.config.get("QUOTE_WORKER")
    if quote_worker is None:
        quote_worker = QuoteAnalysisWorker(
            store, app.config["RUNPOD_ENDPOINT_URL"], app.config["RUNPOD_API_KEY"])
    app.extensions["quote_worker"] = quote_worker
    if os.getenv("WHATSAPP_PHONE_NUMBER_ID") and not store.setting("phone_number_id"):
        store.learn_phone(os.environ["WHATSAPP_PHONE_NUMBER_ID"])
    if app.config["MEDIA_WORKER"] and not app.config.get("TESTING"):
        worker = MediaWorker(store, graph)
        app.extensions["media_worker"] = worker
        worker.start()

    def configured():
        return bool(app.secret_key and len(app.config["STAFF_PASSWORD"]) >= 12)

    def staff_revision():
        if not configured():
            return ""
        return hmac.new(str(app.secret_key).encode(), app.config["STAFF_PASSWORD"].encode(),
                        hashlib.sha256).hexdigest()

    def authenticated():
        return configured() and hmac.compare_digest(
            str(session.get("staff", "")), staff_revision())

    def csrf_token():
        if not app.secret_key:
            return ""
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    def require_staff(handler):
        @wraps(handler)
        def wrapper(*args, **kwargs):
            if not authenticated():
                return jsonify(error="Please sign in to the staff inbox."), 401
            return handler(*args, **kwargs)
        return wrapper

    def json_body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="Expected a JSON object.")
        return data

    @app.before_request
    def protect_mutations():
        if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            expected = session.get("csrf", "")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not hmac.compare_digest(expected, supplied):
                return jsonify(error="Your security token expired. Refresh the page and try again."), 403
        # Secure cookies on the HTTPS proxy, while keeping local isolated tests possible.
        app.config["SESSION_COOKIE_SECURE"] = request.is_secure

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
            "media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'"
        )
        if request.path.startswith(("/api/", "/webhook")) or request.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith("/api/") or request.path == "/webhook":
            return jsonify(error=error.description), error.code
        return error

    @app.errorhandler(StorageBusyError)
    def storage_busy(error):
        response = jsonify(error=str(error))
        response.status_code = 503
        response.headers["Retry-After"] = "5"
        return response

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.get("/api/session")
    def get_session():
        return jsonify(authenticated=authenticated(), staff_configured=configured(),
                       csrf_token=csrf_token())

    @app.post("/api/login")
    def login():
        if not configured():
            return jsonify(error="Configure STAFF_PASSWORD (at least 12 characters) and SESSION_SECRET in Replit Secrets."), 503
        identity = hmac.new(str(app.secret_key).encode(),
                            (request.remote_addr or "unknown").encode(), hashlib.sha256).hexdigest()
        if not store.login_allowed(identity):
            return jsonify(error="Too many sign-in attempts. Please wait 15 minutes."), 429
        data = json_body()
        password = data.get("password")
        if not isinstance(password, str) or not hmac.compare_digest(
                password.encode(), app.config["STAFF_PASSWORD"].encode()):
            store.login_failed(identity)
            return jsonify(error="Incorrect staff password."), 401
        session.clear()
        session.permanent = True
        session["staff"] = staff_revision()
        return jsonify(authenticated=True, csrf_token=csrf_token())

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(authenticated=False, csrf_token=csrf_token())

    @app.get("/api/status")
    @require_staff
    def status():
        return jsonify(
            webhook_token_configured=bool(app.config["WEBHOOK_VERIFY_TOKEN"]),
            app_secret_configured=bool(app.config["META_APP_SECRET"]),
            access_token_configured=bool(app.config["META_ACCESS_TOKEN"]),
            phone_numbers=store.phones(),
            default_phone_number_id=store.setting("phone_number_id"),
            webhook_path="/webhook", storage_path="whatsapp_data",
            last_event_at=store.setting("last_event_at") or None,
        )

    @app.get("/api/conversations")
    @require_staff
    def conversations():
        return jsonify(conversations=store.conversations(request.args.get("q", "")[:200]))

    @app.post("/api/conversations")
    @require_staff
    def new_conversation():
        data = json_body()
        recipient = re.sub(r"[\s()+-]", "", str(data.get("recipient") or ""))
        phone_id = str(data.get("phone_number_id") or store.setting("phone_number_id"))
        name = str(data.get("name") or "")[:120]
        if not re.fullmatch(r"\d{7,15}", recipient):
            return jsonify(error="Enter the recipient's international phone number, including country code."), 400
        if not re.fullmatch(r"\d+", phone_id):
            return jsonify(error="Configure your business phone number ID in Settings first."), 400
        return jsonify(conversation=store.get_or_create_conversation(phone_id, recipient, name)), 201

    @app.get("/api/conversations/<int:conversation_id>/messages")
    @require_staff
    def messages(conversation_id):
        if not store.conversation(conversation_id):
            return jsonify(error="Conversation not found."), 404
        before = request.args.get("before", type=int)
        before_timestamp = request.args.get("before_timestamp", type=int)
        before_seq = request.args.get("before_seq", type=int)
        around_timestamp = request.args.get("around_timestamp", type=int)
        around_seq = request.args.get("around_seq", type=int)
        return jsonify(store.messages(conversation_id, before, before_timestamp, before_seq,
                                      around_timestamp, around_seq))

    @app.get("/api/messages/search")
    @require_staff
    def search_messages():
        query = request.args.get("q", "").strip()[:120]
        if len(query) < 2:
            return jsonify(results=[])
        return jsonify(results=store.search_messages(query))

    @app.post("/api/history/import")
    @require_staff
    def import_history():
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            return jsonify(error="Choose a WhatsApp .txt chat export to import."), 400
        if Path(secure_filename(uploaded.filename)).suffix.lower() != ".txt":
            return jsonify(error="For now, import WhatsApp's plain-text .txt export (without media)."), 400
        content = uploaded.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            return jsonify(error="This export is over the 8 MB import limit."), 413
        if not content.strip():
            return jsonify(error="The selected chat export is empty."), 400

        wa_id = re.sub(r"\D", "", request.form.get("client_phone", ""))[:15]
        if not re.fullmatch(r"\d{7,15}", wa_id):
            return jsonify(error="Enter the client's international phone number (7–15 digits)."), 400
        phone_number_id = store.setting("phone_number_id")
        if not re.fullmatch(r"\d+", phone_number_id):
            return jsonify(error="Configure the business phone number ID in Settings first."), 409
        display_name = request.form.get("client_name", "").strip()[:120]
        try:
            parsed = parse_whatsapp_export(
                content,
                date_order=request.form.get("date_order", "DMY"),
                timezone_name=request.form.get("timezone", "Asia/Kuala_Lumpur"),
                business_name=request.form.get("business_name", "")[:120],
            )
        except HistoryImportError as error:
            return jsonify(error=str(error)), 400
        if not display_name:
            display_name = parsed["contact_sender"][:120]

        try:
            result = store.import_history(
                phone_number_id, wa_id, display_name, parsed["messages"])
        except ValueError as error:
            return jsonify(error=str(error)), 409
        return jsonify(**result, contact_sender=parsed["contact_sender"],
                       skipped_lines=parsed["skipped_lines"]), 201

    @app.post("/api/conversations/<int:conversation_id>/read")
    @require_staff
    def read_conversation(conversation_id):
        if not store.conversation(conversation_id):
            return jsonify(error="Conversation not found."), 404
        store.mark_read(conversation_id)
        return jsonify(ok=True)

    @app.post("/api/messages")
    @require_staff
    def send_message():
        data = json_body()
        conversation_id = data.get("conversation_id")
        if not isinstance(conversation_id, int):
            return jsonify(error="Select a conversation first."), 400
        conversation = store.conversation(conversation_id)
        body = data.get("text")
        client_id = data.get("client_id")
        if not conversation:
            return jsonify(error="Conversation not found."), 404
        if not isinstance(body, str) or not body.strip() or len(body) > 4096:
            return jsonify(error="Enter a text message between 1 and 4096 characters."), 400
        if not isinstance(client_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", client_id):
            return jsonify(error="A unique client_id is required for safe message sending."), 400
        if not conversation["window_open"]:
            return jsonify(error="The 24-hour reply window is closed. The customer must message your business first, or you must send an approved template from WhatsApp Manager."), 409
        if not re.fullmatch(r"\d+", conversation["phone_number_id"]):
            return jsonify(error="This archived conversation has no connected business phone number."), 409
        if not app.config["META_ACCESS_TOKEN"]:
            return jsonify(error="META_ACCESS_TOKEN is not configured in Replit Secrets."), 503
        try:
            row, created = store.new_outgoing(conversation, body, client_id)
        except ValueError as error:
            return jsonify(error=str(error)), 409
        if not created:
            return jsonify(message=store.public_message(row))
        try:
            platform_id = graph.send_text(conversation["phone_number_id"], conversation["wa_id"], body)
            row = store.finish_outgoing(row["id"], platform_id=platform_id)
        except GraphError as error:
            row = store.finish_outgoing(row["id"], error=str(error))
        return jsonify(message=store.public_message(row)), 201

    @app.delete("/api/history")
    @app.delete("/api/conversations/<int:conversation_id>/history")
    @require_staff
    def clear_history(conversation_id=None):
        data = json_body()
        expected = "DELETE ALL" if conversation_id is None else "DELETE"
        if data.get("confirmation") != expected:
            return jsonify(error=f"Type {expected} to confirm permanent history deletion."), 400
        if not isinstance(data.get("delete_attachments"), bool):
            return jsonify(error="Choose whether to also delete saved attachments."), 400
        try:
            result = store.clear_history(conversation_id, data["delete_attachments"])
        except LookupError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 409
        except OSError:
            return jsonify(error="Storage cleanup could not finish. Some files may already have been removed. Please retry."), 503
        return jsonify(ok=True, **result)

    @app.get("/api/media/<path:message_id>")
    @require_staff
    def media(message_id):
        row = store.message(message_id)
        path = store.media_path(row)
        if not path:
            return jsonify(error="Attachment not saved yet, or the file is no longer available."), 404
        mime = row["mime_type"] or "application/octet-stream"
        safe_inline = mime in {"image/jpeg", "image/png", "image/webp", "image/gif",
                               "audio/ogg", "audio/mpeg", "audio/mp4", "audio/aac",
                               "audio/amr", "video/mp4", "video/3gpp"}
        return send_file(path, mimetype=mime,
                         as_attachment=request.args.get("download") == "1" or not safe_inline,
                         download_name=row["filename"] or path.name, conditional=True)

    @app.post("/api/quote-chat")
    @require_staff
    def quote_chat():
        data = json_body()
        try:
            messages, comparison_count, quote_count = prepare_model_messages(
                data.get("messages"), store.quotation_register())
        except QuoteChatInputError as error:
            return jsonify(error=str(error)), 400
        except QuoteChatContextTooLarge as error:
            return jsonify(error=str(error)), 413
        try:
            model_output = app.extensions["quote_worker"].complete_chat(messages)
        except QuoteAnalysisError as error:
            return jsonify(error=str(error)), 502
        try:
            reply, items = parse_model_output(model_output)
        except QuoteChatOutputError as error:
            return jsonify(error=str(error)), 502
        try:
            pdf = build_quote_chat_pdf(reply, items, comparison_count, quote_count)
        except QuoteChatPdfError as error:
            return jsonify(error=str(error)), 503
        return jsonify(
            reply=reply,
            items=items,
            pdf_base64=base64.b64encode(pdf).decode("ascii"),
            pdf_filename=f"quote-ai-output-{int(time.time() * 1000)}.pdf",
            comparison_count=comparison_count,
            quote_count=quote_count,
        )

    @app.post("/api/messages/<path:message_id>/retry-media")
    @require_staff
    def retry_media(message_id):
        if not store.retry_media(message_id):
            return jsonify(error="Only failed attachment downloads can be retried."), 409
        return jsonify(ok=True)

    @app.get("/api/quote-candidates")
    @require_staff
    def quote_candidates():
        raw_conversation_id = request.args.get("conversation_id")
        conversation_id = request.args.get("conversation_id", type=int)
        if raw_conversation_id is not None:
            if conversation_id is None or conversation_id < 1:
                return jsonify(error="Conversation ID is invalid."), 400
            if not store.conversation(conversation_id):
                return jsonify(error="Conversation not found."), 404
        return jsonify(candidates=store.quote_candidates(conversation_id))

    @app.get("/api/quote-evaluations")
    @require_staff
    def quote_evaluations():
        return jsonify(evaluations=store.quote_evaluations())

    @app.post("/api/quote-evaluations")
    @require_staff
    def create_quote_evaluation():
        data = json_body()
        try:
            evaluation = store.create_quote_evaluation(
                data.get("message_ids"), data.get("priority"))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        try:
            app.extensions["quote_worker"].submit(evaluation["id"])
        except Exception:
            store.update_quote_evaluation(
                evaluation["id"], "failed",
                error="The analysis could not be queued. The original messages and files are unchanged.")
            return jsonify(error="The analysis could not be queued. Please try again."), 503
        return jsonify(evaluation=store.quote_evaluation(evaluation["id"])), 202

    @app.get("/api/quote-evaluations/<int:evaluation_id>")
    @require_staff
    def get_quote_evaluation(evaluation_id):
        evaluation = store.quote_evaluation(evaluation_id)
        if not evaluation:
            return jsonify(error="Quote comparison not found. It may have been removed with chat history."), 404
        return jsonify(evaluation=evaluation)

    @app.post("/api/quote-evaluations/<int:evaluation_id>/cancel")
    @require_staff
    def cancel_quote_evaluation(evaluation_id):
        evaluation = store.cancel_quote_evaluation(evaluation_id)
        if not evaluation:
            return jsonify(error="Quote comparison not found. It may have been removed with chat history."), 404
        if evaluation["status"] != "cancelled":
            return jsonify(error="This comparison has already finished.", evaluation=evaluation), 409
        worker = app.extensions["quote_worker"]
        signal_cancel = getattr(worker, "signal_cancel", None)
        if callable(signal_cancel):
            signal_cancel(evaluation_id)
        return jsonify(evaluation=evaluation)

    @app.post("/api/quote-evaluations/<int:evaluation_id>/selection")
    @require_staff
    def select_quote(evaluation_id):
        data = json_body()
        message_id = data.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            return jsonify(error="Choose a quotation from this comparison."), 400
        try:
            evaluation = store.select_quote(evaluation_id, message_id)
        except ValueError as error:
            return jsonify(error=str(error)), 409
        if not evaluation:
            return jsonify(error="Quote comparison not found. It may have been removed with chat history."), 404
        return jsonify(evaluation=evaluation)

    @app.get("/api/quotation-register")
    @require_staff
    def quotation_register():
        rows = store.quotation_register()
        response = jsonify(rows=rows, total=len(rows))
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/api/quotation-register.xlsx")
    @require_staff
    def export_quotation_register():
        workbook = build_quotation_workbook(store.quotation_register())
        response = send_file(
            BytesIO(workbook), mimetype=QUOTATION_XLSX_MIME, as_attachment=True,
            download_name="quotation-register.xlsx", max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/api/settings")
    @require_staff
    def settings():
        phone_id = store.setting("phone_number_id")
        phone = next((p for p in store.phones() if p["id"] == phone_id), {})
        return jsonify(phone_number_id=phone_id,
                       display_phone_number=phone.get("display_phone_number", ""),
                       graph_version=app.config["META_GRAPH_VERSION"])

    @app.post("/api/settings")
    @require_staff
    def save_settings():
        data = json_body()
        phone_id = str(data.get("phone_number_id") or "").strip()
        display = str(data.get("display_phone_number") or "").strip()[:40]
        if not re.fullmatch(r"\d{5,30}", phone_id):
            return jsonify(error="Enter the numeric Phone number ID from Meta's WhatsApp API Setup page, not the phone number itself."), 400
        store.learn_phone(phone_id, display)
        store.set_setting("phone_number_id", phone_id)
        return jsonify(ok=True, phone_number_id=phone_id, display_phone_number=display,
                       graph_version=app.config["META_GRAPH_VERSION"])

    @app.get("/webhook")
    def verify_webhook():
        expected = app.config["WEBHOOK_VERIFY_TOKEN"]
        if not expected:
            return "Webhook verification secret is not configured.", 503
        challenge = request.args.get("hub.challenge", "")
        valid = (request.args.get("hub.mode") == "subscribe" and challenge
                 and len(challenge) <= 256
                 and hmac.compare_digest(request.args.get("hub.verify_token", "").encode(),
                                         expected.encode()))
        return (challenge, 200) if valid else ("Verification failed", 403)

    @app.post("/webhook")
    def receive_message():
        app_secret = app.config["META_APP_SECRET"]
        if not app_secret:
            return jsonify(error="META_APP_SECRET is required to authenticate webhook events."), 503
        raw = request.get_data()
        expected = "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not hmac.compare_digest(expected, signature):
            return jsonify(error="Invalid webhook signature."), 403
        payload = request.get_json(silent=True)
        try:
            process_event(store, payload)
        except (ValueError, TypeError, AttributeError, KeyError):
            return jsonify(error="Malformed WhatsApp webhook payload."), 400
        return "EVENT_RECEIVED", 200

    return app


class PrivateRequestHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        # Verification tokens arrive in GET query strings. Do not expose them
        # in the console/access logs.
        self.log("info", "%s %s %s", self.command, self.path.split("?", 1)[0], code)


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")),
                     request_handler=PrivateRequestHandler)
