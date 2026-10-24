"""Build-time import smoke test. No application factory or network calls."""
from importlib import import_module
from pathlib import Path
import sys


root = Path(__file__).resolve().parents[1]
dependencies = root / ".production-deps"
sys.path[:0] = [str(root), str(dependencies)]
for name in ("gunicorn", "flask", "dotenv", "requests", "psycopg", "replit.object_storage"):
    module = import_module(name)
    if not Path(module.__file__).resolve().is_relative_to(dependencies.resolve()):
        raise RuntimeError(f"{name} was loaded outside the production dependency bundle.")
import_module("src.production")  # Import only; do not open databases or storage.
print("Production dependencies and application imports verified in isolated Python.")
