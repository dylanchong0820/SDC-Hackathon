#!/usr/bin/env python
"""Start the installed Gunicorn module without relying on a console-script PATH."""
import os
from pathlib import Path
import site
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    # Match build-production.sh's --target, including when user-site discovery
    # is disabled in the publishing runtime. No secrets or environment changes.
    sys.path.insert(0, site.getusersitepackages())
    sys.path.insert(0, str(root))
    os.chdir(root)
    if len(sys.argv) == 1:
        sys.argv.extend([
            "--config", str(root / "gunicorn.conf.py"),
            "src.production:create_production_app()",
        ])
    from gunicorn.app.wsgiapp import run
    run()


if __name__ == "__main__":
    main()
