# WhatsApp Desk frontend contract

The existing Flask server serves `src/templates/index.html`, `src/static/inbox.css`,
and `src/static/inbox.js`. Use vanilla JS/CSS with same-origin fetch, no build step.
No fake messages or demo contacts. The title/product name is **WhatsApp Desk**.

## Session and security
- `GET /api/session` returns `{authenticated, staff_configured, csrf_token}`.
- All POST requests send `X-CSRF-Token` from the session response and JSON.
- `POST /api/login` body `{password}` returns `{authenticated:true, csrf_token}`.
- `POST /api/logout` logs out. All inbox/media/settings routes require staff login.
- Errors are JSON `{error: "Human-readable explanation"}` with non-2xx status.
- Never ask for API/access/app secrets in the website. Only the staff password is entered at sign-in.
- If `staff_configured` is false show setup instructions to add `STAFF_PASSWORD` (12+ characters) and `SESSION_SECRET` in Replit Secrets; do not show a fake working inbox.

## Inbox
- `GET /api/status` returns `{webhook_token_configured, app_secret_configured, access_token_configured, phone_numbers: [{id, display_phone_number, verified_name}], default_phone_number_id, webhook_path:"/webhook", storage_path:"whatsapp_data", last_event_at}`. Booleans indicate configuration, NOT successful Meta verification.
- Timestamp format: message `timestamp`, conversation `last_message_at`, and `last_inbound_at` are Unix epoch seconds (integers). `last_event_at` is Unix epoch seconds encoded as a numeric string. These are not milliseconds.
- `GET /api/conversations?q=...` returns `{conversations:[{id, wa_id, name, phone_number_id, display_phone_number, last_message, last_message_at, unread_count, last_inbound_at, window_open}]}`.
- `GET /api/conversations/<id>/messages?before=<optional numeric sequence>` returns `{messages: [...], has_more}`. Default newest 100 in ascending order.
- `POST /api/conversations` body `{recipient, name?, phone_number_id?}` returns `{conversation}`. Recipient is international phone number. Creating a conversation does NOT send anything.
- `POST /api/conversations/<id>/read` marks local unread count read.
- `POST /api/messages` body `{conversation_id, text, client_id}` returns `{message}`. `client_id` is a browser-generated UUID reused on a network retry (prevents duplicate sends).
- Text limit 4096 characters. Sending is restricted to the 24-hour reply window after the most recent inbound customer message. Outside that window show that an approved template is required; template sending is not implemented. New empty chats cannot send free-form texts until customer writes first.
- Poll conversations/status/messages roughly every 5 seconds, preserving draft, selection and scroll position. Show polling/send/load errors; do not silently show stale data as live.

### Message shape
`{id, seq, conversation_id, direction:"inbound"|"outbound", type, body, caption, timestamp, status, filename, mime_type, attachment_state:"none"|"pending"|"downloading"|"saved"|"failed", attachment_error, media_url, raw, error, context_id}`.
`media_url` is null unless saved, otherwise authenticated same-origin `/api/media/<message-id>`.
Text => body. Images/stickers => safe image preview plus download link. Audio/voice => audio controls plus download. Videos => video controls. Documents/PDFs/arbitrary WhatsApp-supported files => filename/type/download, never inline untrusted HTML. `?download=1` forces download.
Other types (location/contacts/reaction/interactive/button/order/system/unknown) include a readable body and full raw object; offer an expandable JSON details view rather than discard them.
Show outgoing status accurately: `sending`, `accepted` (Meta accepted, not yet delivered), `sent`, `delivered`, `read`, `failed`. Display failure `error`.
`POST /api/messages/<id>/retry-media` queues another attachment download.

## Settings
- `GET /api/settings` returns `{phone_number_id, display_phone_number, graph_version}`.
- `POST /api/settings` body `{phone_number_id, display_phone_number?}` sets the default business phone number ID (digits, not the phone number). Existing incoming webhook metadata automatically learns business phone IDs. Never collect access tokens here.
- Settings must explain where to find phone number ID: Meta App Dashboard > WhatsApp > API Setup. Explain webhook URL is the site's origin plus `/webhook` and `WEBHOOK_VERIFY_TOKEN` must match Meta. `META_APP_SECRET` is required for signed webhook POSTs. Do not print any secret values.

Pages/states: `/` (sign-in or authenticated inbox), settings surface, new conversation surface, setup checklist, empty/error/loading/offline states, responsive mobile.
