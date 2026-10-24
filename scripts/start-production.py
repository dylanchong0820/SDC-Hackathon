#!/usr/bin/env python
"""Start the installed Gunicorn module without relying on a console-script PATH."""
import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    dependencies = root / ".production-deps"
    if not (dependencies / "gunicorn").is_dir():
        raise RuntimeError(
            "Production dependencies are missing. Run bash scripts/build-production.sh first."
        )
    # The same project-relative path is used at build time and runtime.
    # Never depend on user home directories or the workspace's .pythonlibs.
    sys.path.insert(0, str(dependencies))
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
