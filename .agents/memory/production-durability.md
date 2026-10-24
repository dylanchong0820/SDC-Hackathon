---
name: Real-client publishing and archive ownership
description: Why production preparation requires durable archives and isolated attachment ownership.
---

The user chose preparing for real client use before publishing, rather than an
evaluation demo. Preserve the existing archive during storage changes and use
commercially permitted AI models; do not quietly downgrade this to demo-only use.

**Why:** Workspace-local messages/files and a research-only image model were
identified as blockers when the user first requested publishing.

**How to apply:** Future publishing, storage migrations and model substitutions
must preserve these requirements. Keep migration originals until verified and
make their separate retention/deletion implications explicit.

Development-data copies must not give two independent databases deletion
ownership of the same attachment objects.

**Why:** Copying database rows does not independently copy App Storage files;
without separate ownership, deleting test history could destroy live attachments.

**How to apply:** Rehome attachment contents when copying metadata across
environments. Do not bypass environment isolation in cleanup/import tools.
