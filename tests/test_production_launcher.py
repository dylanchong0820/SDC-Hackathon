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
                [sys.executable, "-I", "-S", str(LAUNCHER), "--version"],
                cwd=directory, env=environment, capture_output=True, text=True, timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"\(version \d+\.\d+")

    def test_bundle_works_after_relocation_without_workspace_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "relocated-project"
            (project / "scripts").mkdir(parents=True)
            shutil.copyfile(LAUNCHER, project / "scripts/start-production.py")
            shutil.copyfile(ROOT / "scripts/verify-production.py",
                            project / "scripts/verify-production.py")
            shutil.copytree(ROOT / "src", project / "src",
                            ignore=shutil.ignore_patterns("__pycache__"))
            # Only the explicitly bundled packages are exposed to this process.
            (project / ".production-deps").symlink_to(ROOT / ".production-deps",
                                                     target_is_directory=True)
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(project / "scripts/start-production.py"),
                 "--version"],
                cwd=directory, capture_output=True, text=True, timeout=15,
            )
            imports = subprocess.run(
                [sys.executable, "-I", "-S", str(project / "scripts/verify-production.py")],
                cwd=directory, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"\(version \d+\.\d+")
        self.assertEqual(imports.returncode, 0, imports.stderr)

    def test_missing_bundle_fails_explicitly_instead_of_using_workspace_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "scripts").mkdir()
            shutil.copyfile(LAUNCHER, project / "scripts/start-production.py")
            result = subprocess.run(
                [sys.executable, str(project / "scripts/start-production.py"), "--version"],
                capture_output=True, text=True, timeout=15,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Production dependencies are missing", result.stderr)

    def test_production_bundle_is_not_excluded_from_publishing(self):
        ignored = {
            line.split("#", 1)[0].strip().lstrip("/")
            for line in (ROOT / ".replitignore").read_text().splitlines()
        }
        self.assertNotIn(".production-deps/", ignored)
        self.assertNotIn(".production-deps", ignored)

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
