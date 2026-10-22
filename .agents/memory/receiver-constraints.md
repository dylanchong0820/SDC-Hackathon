---
name: Receiver constraints
description: User-required archive behavior and Git remote write boundaries.
---

Preserve readable text message logs and downloaded attachments as product
features, except when an employee explicitly confirms history deletion.
A searchable database is an additional index, not a replacement for the archive.

**Why:** The user explicitly asked to save incoming WhatsApp messages as their
original code did while adding an employer-facing website. They later explicitly
requested employee-controlled deletion for individual chats and the whole inbox,
with an option to include attachments.

**How to apply:** Keep archive behavior when extending or changing storage, and
explain deployment durability limits rather than quietly discarding local files.

Manually imported chat transcripts are historical reference, not proof of a
new WhatsApp event. Do not let an import advance `last_inbound_at` or open the
24-hour reply window; retain message-ID tombstones so deleting history cannot
be undone by importing the same export again.

**Why:** A local export is not signed by Meta and can contain dates edited by
the person importing it. Using it as a live inbound event could incorrectly
enable a free-form WhatsApp reply.

**How to apply:** Any future export format or import route must preserve this
separation and test duplicate imports, post-deletion imports, and reply-window
state without calling the Meta send API.

Treat upstream as read-only. Repository branch merges/deletions and pushes must
target origin only; leave the internal Replit backup remote alone.

**Why:** The user explicitly limited repository changes to the owner's fork.

**How to apply:** Check remote ownership before every remote write or branch deletion.
