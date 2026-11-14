"""Offline checks for the service launcher and credential scanner."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verify_repository", ROOT / "scripts/verify_repository.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class LocalNetworkOptionsTest(unittest.TestCase):
    def test_vm_and_local_registration_bypass_proxy_without_overriding_public_proxy(self):
        spec = importlib.util.spec_from_file_location("run_local", ROOT / "scripts/run_local.py")
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        options = launcher.local_network_options("192.168.150.101", "192.168.150.1")
        values = dict(option[2:].split("=", 1) for option in options)
        self.assertEqual({"http.nonProxyHosts", "socksNonProxyHosts"}, set(values))
        for value in values.values():
            self.assertIn("192.168.150.101", value.split("|"))
            self.assertIn("192.168.150.1", value.split("|"))
            self.assertIn("127.*", value.split("|"))
            self.assertNotIn("/", value)
        with self.assertRaises(ValueError):
            launcher.local_network_options("192.168.0.0/16", "192.168.150.1")


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="learntrail test ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        shutil.copyfile(ROOT / "startup.sh", self.root / "startup.sh")
        shutil.copyfile(ROOT / "Dockerfile", self.root / "Dockerfile")
        jar = self.root / "tj-learning/target/tj-learning.jar"
        jar.parent.mkdir(parents=True)
        jar.write_bytes(b"fixture-only")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "docker-calls.jsonl"
        docker = self.bin / "docker"
        docker.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['MOCK_DOCKER_LOG'], 'a') as output:\n"
            "    output.write(json.dumps(args) + '\\n')\n"
            "if args[:2] == ['container', 'inspect']:\n"
            "    sys.exit(0 if os.getenv('MOCK_CONTAINER_EXISTS') == '1' else 1)\n"
            "if args and args[0] == 'build' and os.getenv('MOCK_BUILD_FAIL') == '1':\n"
            "    sys.exit(1)\n"
            "sys.exit(0)\n"
        )
        docker.chmod(0o755)

    def launch(self, *extra, flags=None):
        env = os.environ.copy()
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["MOCK_DOCKER_LOG"] = str(self.log)
        env.pop("JAVA_OPTS", None)
        env.update(flags or {})
        return subprocess.run(
            ["sh", str(self.root / "startup.sh"), "-n", "tj-learning", "-d", "tj-learning",
             "-p", "8090", *extra],
            cwd=self.root.parent, env=env, capture_output=True, text=True,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_help_does_not_touch_docker(self):
        result = self.launch("--help")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("LearnTrail", result.stdout)
        self.assertEqual([], self.calls())

    def test_dry_run_does_not_touch_docker(self):
        result = self.launch("--dry-run")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--server.port=8090", result.stdout)
        self.assertEqual([], self.calls())

    def test_launch_from_another_directory_and_preserve_paths_with_spaces(self):
        env_file = self.root / "local env"
        env_file.write_text("EXAMPLE=value\n")
        result = self.launch("-e", str(env_file))
        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.calls()
        build = next(args for args in calls if args[0] == "build")
        run = next(args for args in calls if args[0] == "run")
        self.assertIn(str(self.root), build)
        self.assertIn("JAR_FILE=tj-learning/target/tj-learning.jar", build)
        self.assertEqual(str(env_file), run[run.index("--env-file") + 1])
        self.assertIn("8090:8090", run)
        self.assertEqual("--server.port=8090", run[-1])
        self.assertFalse(any(args[0] in {"rm", "rmi"} for args in calls))

    def test_existing_container_is_not_removed(self):
        result = self.launch(flags={"MOCK_CONTAINER_EXISTS": "1"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("already exists", result.stderr)
        self.assertFalse(any(args[0] in {"build", "run", "rm", "rmi"} for args in self.calls()))

    def test_failed_build_never_starts_container(self):
        result = self.launch(flags={"MOCK_BUILD_FAIL": "1"})
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(any(args[0] == "run" for args in self.calls()))

    def test_invalid_ports_fail_before_docker(self):
        for port in ("0", "65536", "abc", "-1", "9999999999999999999999"):
            with self.subTest(port=port):
                result = self.launch("-p", port)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual([], self.calls())

    def test_module_cannot_escape_repository(self):
        result = self.launch("-d", "../outside")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], self.calls())
        (self.root / "outside-link").symlink_to(self.root.parent, target_is_directory=True)
        result = self.launch("-d", "outside-link")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], self.calls())

    def test_missing_jar_fails_before_docker(self):
        (self.root / "tj-learning/target/tj-learning.jar").unlink()
        result = self.launch()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Package the module first", result.stderr)
        self.assertEqual([], self.calls())

    def test_debug_preserves_memory_options_and_binds_localhost(self):
        result = self.launch("-o", "-Xmx768m -Dexample=value", "-a", "5006")
        self.assertEqual(0, result.returncode, result.stderr)
        run = next(args for args in self.calls() if args[0] == "run")
        options = next(arg for arg in run if arg.startswith("JAVA_OPTS="))
        self.assertIn("-Xmx768m -Dexample=value", options)
        self.assertIn("-agentlib:jdwp=", options)
        self.assertIn("127.0.0.1:5006:5005", run)

    def test_keystore_is_explicitly_mounted_read_only(self):
        key = self.root / "local auth.jks"
        key.write_bytes(b"fixture-only")
        result = self.launch("-k", str(key))
        self.assertEqual(0, result.returncode, result.stderr)
        run = next(args for args in self.calls() if args[0] == "run")
        self.assertIn("readonly", run[run.index("--mount") + 1])
        self.assertIn("LEARNTRAIL_JWT_KEYSTORE_LOCATION=file:/run/secrets/learntrail-auth.jks", run)

    def test_missing_env_file_fails_before_docker(self):
        result = self.launch("-e", str(self.root / "missing.env"))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], self.calls())

    def test_invalid_container_name_is_rejected(self):
        result = self.launch("-c", "invalid;command")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], self.calls())


class DockerEntrypointTest(unittest.TestCase):
    def test_jvm_options_precede_jar_and_application_arguments_follow_it(self):
        line = next(line for line in (ROOT / "Dockerfile").read_text().splitlines()
                    if line.startswith("ENTRYPOINT "))
        command = json.loads(line.removeprefix("ENTRYPOINT "))
        with tempfile.TemporaryDirectory(prefix="learntrail entrypoint ") as temporary:
            directory = Path(temporary)
            output = directory / "java-args.json"
            java = directory / "java"
            java.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['JAVA_ARGS_LOG'], 'w') as output:\n"
                "    json.dump(sys.argv[1:], output)\n"
            )
            java.chmod(0o755)
            env = os.environ.copy()
            env.update(PATH=str(directory) + os.pathsep + env.get("PATH", ""),
                       JAVA_OPTS="-Xmx512m -Dexample=value", JAVA_ARGS_LOG=str(output))
            result = subprocess.run(command + ["--server.port=8090"], env=env,
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(["-Xmx512m", "-Dexample=value", "-jar", "/app/app.jar",
                              "--server.port=8090"], json.loads(output.read_text()))


class DocumentationLinksTest(unittest.TestCase):
    def test_article_site_assets_resolve_without_changing_normal_markdown_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "docs/content/article.md"
            source.parent.mkdir(parents=True)
            public = root / "frontend/learntrail-web/public"
            (public / "images").mkdir(parents=True)
            (public / "images/diagram.svg").write_text("<svg/>")
            (root / "docs/guide.md").write_text("Guide")
            self.assertTrue(verify.local_link_exists(root, source, "/images/diagram.svg"))
            self.assertTrue(verify.local_link_exists(root, source, "../guide.md"))
            self.assertFalse(verify.local_link_exists(root, source, "/images/missing.svg"))
            self.assertFalse(verify.local_link_exists(
                root, root / "README.md", "/images/diagram.svg"))

    def test_article_site_assets_cannot_escape_public_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "docs/content/article.md"
            public = root / "frontend/learntrail-web/public"
            public.mkdir(parents=True)
            outside = root / "frontend/learntrail-web/outside.svg"
            outside.write_text("<svg/>")
            (public / "linked.svg").symlink_to(outside)
            self.assertFalse(verify.local_link_exists(root, source, "/../outside.svg"))
            self.assertFalse(verify.local_link_exists(root, source, "/linked.svg"))


class CredentialScannerTest(unittest.TestCase):
    def test_cloud_access_fields_are_reported_without_values(self):
        for key in ("accessId", "accessSecret", "access-key-id", "access_key_secret"):
            with self.subTest(key=key):
                self.assertEqual([(1, key)], verify.credential_issues(
                    key + ": example-private-material\n", ".yml"))
                self.assertEqual([], verify.credential_issues(
                    key + ": ${ALI_SMS_ACCESS_KEY_ID:}\n", ".yml"))

    def test_access_key_format_is_detected_outside_yaml(self):
        value = "LT" + "AI" + "a" * 20
        issues = verify.credential_issues('key="' + value + '"', ".properties")
        self.assertEqual([(1, "Alibaba Cloud AccessKey ID")], issues)
        self.assertNotIn(value, repr(issues))

    def test_literal_secret_is_reported_without_value(self):
        issues = verify.credential_issues("  secretKey: do-not-display-this\n", ".yml")
        self.assertEqual([(1, "secretKey")], issues)

    def test_required_environment_placeholder_is_allowed(self):
        self.assertEqual([], verify.credential_issues(
            '  privateKey: "$' + '{LEARNTRAIL_WECHAT_PRIVATE_KEY}"\n', ".yml"))

    def test_secret_default_cannot_hide_inside_placeholder(self):
        issues = verify.credential_issues(
            'password: "$' + '{PASSWORD:embedded-secret}"\n', ".yml")
        self.assertEqual([(1, "password")], issues)

    def test_java_environment_lookup_is_allowed(self):
        self.assertEqual([], verify.credential_issues(
            'config.merchantPrivateKey = System.getenv("LEARNTRAIL_ALIPAY_PRIVATE_KEY");', ".java"))
        self.assertTrue(verify.credential_issues(
            'config.merchantPrivateKey = "example-private-material";', ".java"))


if __name__ == "__main__":
    unittest.main()
