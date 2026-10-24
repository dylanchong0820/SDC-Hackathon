---
name: Quote AI PDF rendering
description: Renderer choice for temporary downloadable quotation PDFs.
---

Generate quotation PDFs from a self-contained, escaped HTML document using headless Chromium. Keep temporary HTML/PDF files in a temporary directory and return the PDF bytes without archiving them. Never interpolate model or user text as raw HTML.

**Why:** The managed Python package installer cannot install ReportLab in this workspace, and hand-written PDFs using built-in or CID fonts rendered multilingual names and table spacing incorrectly. Chromium produced readable mixed Chinese/Latin supplier names and paginated tables.

**How to apply:** For related PDF exports, use Chromium from `PATH`, keep the document inline and network-free, sanitize the child-process environment, and handle renderer absence or timeout explicitly.

Verify multilingual text survives PDF extraction, not just that a valid PDF file
is produced.

**Why:** Packaged Nix fonts were available to the shell but hidden from the
credential-isolated renderer, producing a valid PDF with a missing Chinese
character.

**How to apply:** Preserve safe font lookup/registration information in the
renderer without passing application credentials. Keep mixed Chinese/Latin
extraction checks when changing renderer or system packages.
