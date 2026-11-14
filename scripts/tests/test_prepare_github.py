"""Public exports preserve build inputs and never carry private development data."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("prepare_github", ROOT / "scripts/prepare_github.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class PublicPathTest(unittest.TestCase):
    def test_export_excludes_private_history_notes_templates_and_runtime(self):
        for name in [".git/config", ".local/videos/private.mp4", ".env.local", "AGENTS.md",
                     "docs/platform-plan.md", "docs/deployment-20261113.md", "templates/entity.java.ftl",
                     "learntrail-agent/AGENTS.md", "learntrail-agent/.env", "learntrail-agent/.venv/private",
                     "lt-auth/src/auth.jks", "lt-user/target/app.jar", "frontend/learntrail-web/node_modules/a.js",
                     "../README.md", "/README.md"]:
            with self.subTest(name=name):
                self.assertFalse(release.public_path(name))

    def test_public_components_and_regression_resources_are_preserved(self):
        for name in ["README.md", ".env.example", "docs/backend-architecture.md",
                     "learntrail-agent/.env.example", "learntrail-agent/evaluation/cases-v3.jsonl",
                     "lt-learning/src/main/java/Service.java", "database/migrations/V001.sql",
                     "frontend/learntrail-web/package-lock.json", "deploy/nginx/learntrail.conf"]:
            with self.subTest(name=name):
                self.assertTrue(release.public_path(name))


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.write("pom.xml", '<project xmlns="http://maven.apache.org/POM/4.0.0"/>')
        self.write("README.md", "LearnTrail")
        self.write("scripts/verify_repository.py", (ROOT / "scripts/verify_repository.py").read_text())
        self.destination = Path(self.temp.name) / "public"

    def write(self, name, value):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)

    def test_copy_has_no_original_git_or_development_files(self):
        self.write(".env.local", "PRIVATE=not-for-export")
        self.write("templates/entity.java.ftl", "development template")
        self.write("docs/platform-plan.md", "private handoff")
        kept, excluded = release.export(self.root, self.destination)
        self.assertIn("README.md", kept)
        self.assertIn("docs/platform-plan.md", excluded)
        self.assertEqual("LearnTrail", (self.destination / "README.md").read_text())
        self.assertFalse((self.destination / ".git").exists())
        self.assertFalse((self.destination / ".env.local").exists())
        self.assertFalse((self.destination / "templates").exists())

    def test_existing_destination_is_not_overwritten(self):
        self.destination.mkdir()
        marker = self.destination / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(ValueError):
            release.export(self.root, self.destination)
        self.assertEqual("keep", marker.read_text())

    def test_committed_snapshot_ignores_later_working_changes(self):
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "fixture"], cwd=self.root, check=True)
        self.write("README.md", "unfinished concurrent edit")
        self.write("learntrail-agent/unfinished.py", "unfinished")
        commit, files = release.snapshot(self.root, "HEAD")
        self.assertEqual(40, len(commit))
        release.export(self.root, self.destination, files)
        self.assertEqual("LearnTrail", (self.destination / "README.md").read_text())
        self.assertFalse((self.destination / "learntrail-agent/unfinished.py").exists())

    def test_module_closure_is_checked_before_export(self):
        self.write("pom.xml", '<project xmlns="http://maven.apache.org/POM/4.0.0"><modules><module>lt-missing</module></modules></project>')
        with self.assertRaisesRegex(ValueError, "Missing Maven module"):
            release.export(self.root, self.destination)
        self.assertFalse(self.destination.exists())

    def test_public_symlink_cannot_copy_external_data(self):
        outside = Path(self.temp.name) / "private.txt"
        outside.write_text("private")
        link = self.root / "lt-user/src/private.txt"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            release.export(self.root, self.destination)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
