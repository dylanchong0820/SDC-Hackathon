# Custom analytics events

Replit injects the Umami tracker only after analytics is enabled in Publishing
settings and the app is published/republished. The application adds no tracking
script, website ID, analytics credentials or analytics environment variables.
Development and blocked/unavailable trackers are safe no-ops.

| Description | Event name | Properties |
| --- | --- | --- |
| Successful staff password sign-in | `staff_login` | None |
| Opening a workspace tool | `workspace_tool_opened` | `tool` |
| Meta send response received | `message_send_result` | `result`: accepted/failed |
| Comparison accepted for processing | `comparison_requested` | `priority`, `source_count`, `retry` |
| A comparison started in this browser reaches a terminal result | `comparison_finished` | `result`: completed/failed |
| Local comparison cancellation confirmed | `comparison_stopped` | None |
| Staff quotation choice saved | `quotation_selected` | None |
| AI answer and its PDF returned | `quote_ai_answered` | None |
| PDF, Excel or readable archive download initiated | `report_download_requested` | `format`: pdf/xlsx/txt |
| History deletion confirmed by server | `history_cleared` | `scope`: chat/inbox; `include_attachments` |
| Chat history import accepted | `history_imported` | None |

Message acceptance is not delivery confirmation. Download initiation is not
proof that a browser finished saving a file. Completion events are observed only
while the initiating browser continues polling: closing or leaving the comparison
screen may mean no completion event is recorded. Existing saved comparisons do
not generate completion events merely because staff view them.

Only the documented enum/boolean/count properties are allowed through the shared
wrapper. No message text, prompts, answers, customer/company identifiers,
quotation prices, attachment names, passwords, tokens, raw errors or record IDs
are sent in custom event payloads. Internal IDs used to deduplicate completion
polls stay in browser memory only. Replit's automatic pageviews are separate.

Test with `node tests/test_analytics.js`.
