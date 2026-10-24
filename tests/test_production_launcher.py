"""Publishing must not rely on the development-only gunicorn console script."""
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts/start-production.py"


class ProductionLauncherTests(unittest.TestCase):
    def test_runs_without_console_launcher_or_user_site_discovery(self):
        environment = dict(os.environ)
        environment["PATH"] = str(Path(sys.executable).resolve().parent)
        environment["PYTHONNOUSERSITE"] = "1"
        environment.pop("PYTHONPATH", None)
        self.assertIsNone(shutil.which("gunicorn", path=environment["PATH"]))
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(LAUNCHER), "--version"],
                cwd=directory, env=environment, capture_output=True, text=True, timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"\(version \d+\.\d+")

    def test_default_launch_keeps_existing_production_factory_and_config(self):
        entry = runpy.run_path(str(LAUNCHER))
        observed = {}
        stub = types.ModuleType("gunicorn.app.wsgiapp")
        def run():
            observed.update(argv=list(sys.argv), cwd=Path.cwd(), path=list(sys.path))
        stub.run = run
        original_directory = Path.cwd()
        try:
            with patch.dict(sys.modules, {"gunicorn.app.wsgiapp": stub}), \
                    patch.object(sys, "argv", [str(LAUNCHER)]), \
                    patch.object(sys, "path", list(sys.path)):
                entry["main"]()
        finally:
            os.chdir(original_directory)
        self.assertEqual(observed["cwd"], ROOT)
        self.assertEqual(observed["argv"][1:], [
            "--config", str(ROOT / "gunicorn.conf.py"), "src.production:create_production_app()"
        ])


if __name__ == "__main__":
    unittest.main()
