# Additional third-party production dependencies

These libraries are external open-source software, not team-created code.
Their original license notices remain in the installed packages.

| Library | Version | Purpose | License |
| --- | --- | --- | --- |
| [Gunicorn](https://github.com/benoitc/gunicorn) | 26.2.0 | Production Python WSGI server | MIT |
| [Psycopg](https://www.psycopg.org/) | 3.3.6 | PostgreSQL database adapter | LGPL-3.0-only |
| [Replit Object Storage Python SDK](https://pypi.org/project/replit-object-storage/) | 1.0.2 | Private App Storage uploads/downloads | ISC |

Psycopg's binary package includes native dependencies with their own notices;
retain the distributed license files when packaging it. This list supplements,
and does not replace, the project's existing third-party declarations.

The production environment also uses external PostgreSQL, Chromium, Poppler,
Antiword, Tesseract, Gnumeric and Noto CJK fonts through system packages.
Preserve their packaged notices. See `AI_MODEL_ATTRIBUTION.md` separately for the
AI model's upstream license and attribution.
