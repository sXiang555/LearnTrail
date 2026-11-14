#!/usr/bin/env python3
"""Export the current LearnTrail source tree without private files or old history."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    "README.md", "CONTRIBUTING.md", "pom.xml", "Dockerfile",
    "startup.sh", ".gitignore", ".dockerignore", ".env.example",
}
PUBLIC_DOCS = {
    "docs/backend-architecture.md", "docs/agent-architecture.md", "docs/quickstart.md",
}
PUBLIC_SCRIPTS = {
    "scripts/run_local.py", "scripts/verify_repository.py", "scripts/prepare_github.py",
    "scripts/seed_research_demo.py", "scripts/smoke_creator.py",
    "scripts/tests/test_tooling.py", "scripts/tests/test_research_demo.py",
    "scripts/tests/test_prepare_github.py",
}
PRIVATE_PARTS = {
    ".git", ".local", ".venv", "node_modules", "target", "__pycache__",
    ".pytest_cache", ".idea", "dist", "build", "logs", "job",
}
PRIVATE_SUFFIXES = {".jks", ".p12", ".pfx", ".keystore", ".key", ".pem", ".dump", ".log", ".pyc"}


def public_path(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or set(path.parts) & PRIVATE_PARTS:
        return False
    if path.name == "AGENTS.md" or path.suffix.lower() in PRIVATE_SUFFIXES:
        return False
    if path.name.startswith(".env") and path.name != ".env.example":
        return False
    if name in ROOT_FILES or name in PUBLIC_DOCS or name in PUBLIC_SCRIPTS:
        return True
    return (
        path.parts[0].startswith("lt-")
        or name.startswith("frontend/learntrail-web/")
        or name.startswith("learntrail-agent/")
        or name.startswith(".github/")
        or name.startswith("database/migrations/")
        or name == "database/demo/V20261113_01__rag_agent_catalog.json"
        or name.startswith("docs/content/")
        or name.startswith("docs/screenshots/")
        or name.startswith("deploy/nginx/")
    )


def snapshot(root: Path, revision: str) -> tuple[str, dict[str, bytes]]:
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", revision + "^{commit}"],
        cwd=root, text=True,
    ).strip()
    archive = subprocess.check_output(["git", "archive", "--format=tar", commit], cwd=root)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source:
            if member.isdir():
                continue
            if public_path(member.name) and not member.isfile():
                raise ValueError("Public export cannot include symlinks: " + member.name)
            files[member.name] = source.extractfile(member).read() if member.isfile() and public_path(member.name) else b""
    return commit, files


def inventory(root: Path, files: dict[str, bytes] | None = None) -> tuple[list[str], list[str]]:
    if files is not None:
        return sorted(name for name in files if public_path(name)), sorted(name for name in files if not public_path(name))
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, check=True,
    )
    names = sorted({v.decode() for v in result.stdout.split(b"\0") if v})
    kept, excluded = [], []
    for name in names:
        source = root / name
        if not source.is_file():
            continue
        if public_path(name):
            if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
                raise ValueError("Public export cannot include symlinks: " + name)
            kept.append(name)
        else:
            excluded.append(name)
    return kept, excluded


def check_modules(root: Path, names: list[str], files: dict[str, bytes] | None = None) -> None:
    namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
    for pom in (name for name in names if name.endswith("pom.xml")):
        model = ET.fromstring(files[pom]) if files is not None else ET.parse(root / pom).getroot()
        for module in model.findall("m:modules/m:module", namespace):
            child = str(PurePosixPath(pom).parent / module.text / "pom.xml")
            if child not in names:
                raise ValueError("Missing Maven module in public export: " + child)


def export(root: Path, destination: Path, files: dict[str, bytes] | None = None) -> tuple[list[str], list[str]]:
    root = root.resolve()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ValueError("Destination already exists; choose a new export directory")
    if root.is_relative_to(destination):
        raise ValueError("Export destination cannot contain the source repository")
    names, excluded = inventory(root, files)
    check_modules(root, names, files)
    spec = importlib.util.spec_from_file_location("release_checker", root / "scripts/verify_repository.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    for name in names:
        source = root / name
        try:
            content = files[name].decode("utf-8") if files is not None else source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if checker.credential_issues(content, source.suffix):
            raise ValueError("Credential check failed in " + name + "; values suppressed")
    destination.mkdir(parents=True)
    for name in names:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if files is not None:
            target.write_bytes(files[name])
            if name.endswith(".sh"):
                target.chmod(0o755)
        else:
            shutil.copy2(root / name, target)
    return names, excluded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / ".local/github/LearnTrail")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ref", default="HEAD", help="Export this committed snapshot; working changes remain untouched")
    parser.add_argument("--init-git", action="store_true", help="Initialize a new main branch without copying history or making commits")
    args = parser.parse_args()
    try:
        commit, files = snapshot(ROOT, args.ref)
        if args.dry_run:
            kept, excluded = inventory(ROOT, files)
            check_modules(ROOT, kept, files)
        else:
            kept, excluded = export(ROOT, args.destination, files)
            if args.init_git:
                subprocess.run(["git", "init", "-b", "main", str(args.destination.resolve())], check=True,
                               stdout=subprocess.DEVNULL)
            manifest = ROOT / ".local/run/github-export-manifest.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({"source_commit": commit,"destination": str(args.destination.resolve()),
                "files": kept, "excluded": excluded}, ensure_ascii=False, indent=2))
            manifest.chmod(0o600)
        print("LearnTrail public files:", len(kept), "excluded development files:", len(excluded))
        print("Source commit:", commit)
        if not args.dry_run:
            print("Prepared:", args.destination.resolve())
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print("Export not completed:", str(error) if isinstance(error, ValueError) else type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
