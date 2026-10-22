---
name: Receiver constraints
description: User-required archive behavior and Git remote write boundaries.
---

Preserve readable text message logs and downloaded attachments as product
features; a searchable database is an additional index, not a replacement for
the original file archive.

**Why:** The user explicitly asked to save incoming WhatsApp messages as their
original code did while adding an employer-facing website.

**How to apply:** Keep archive behavior when extending or changing storage, and
explain deployment durability limits rather than quietly discarding local files.

Treat upstream as read-only. Repository branch merges/deletions and pushes must
target origin only; leave the internal Replit backup remote alone.

**Why:** The user explicitly limited repository changes to the owner's fork.

**How to apply:** Check remote ownership before every remote write or branch deletion.
