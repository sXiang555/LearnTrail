#!/usr/bin/env python3
"""Check public working-tree files without printing credential values.

This check does not scan Git history or connect to external infrastructure.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote
import xml.etree.ElementTree as ET

NS = {"m": "http://maven.apache.org/POM/4.0.0"}
SENSITIVE_KEYS = {
    "password", "secret", "privatekey", "merchantprivatekey",
    "secretid", "secretkey", "apiv3key", "urlkey",
    "accessid", "accesssecret", "accesskey", "accesskeyid", "accesskeysecret",
}
ALI_ACCESS_KEY_ID = re.compile(r"\bLTAI[A-Za-z0-9]{12,}\b")
FORBIDDEN_KEY_FILES = {".jks", ".p12", ".pfx", ".keystore"}
YAML_VALUE = re.compile(r"^\s*([\w.-]+):\s*(.*?)\s*$")
PLACEHOLDER = re.compile(r"\$\{[A-Z][A-Z0-9_]*(?::)?\}")
JAVA_SECRET = re.compile(
    r'\b(?:merchantPrivateKey|privateKey|apiV3Key|secretKey|secretId)'
    r'\s*=\s*"([^"]{12,})"'
)


def credential_issues(text, suffix):
    """Return line numbers and key names, never matched values."""
    issues = []
    for number, line in enumerate(text.splitlines(), 1):
        if ALI_ACCESS_KEY_ID.search(line):
            issues.append((number, "Alibaba Cloud AccessKey ID"))
        if re.match(r"^\s*-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----", line):
            issues.append((number, "private key block"))
        if suffix in {".yml", ".yaml"}:
            match = YAML_VALUE.match(line)
            if not match:
                continue
            key = match[1].replace("-", "").replace("_", "").lower()
            if key not in SENSITIVE_KEYS and not key.endswith("password"):
                continue
            value = match[2].split(" #", 1)[0].strip().strip("\"'")
            if value not in {"", "null", "~"} and not PLACEHOLDER.fullmatch(value):
                issues.append((number, match[1]))
        if suffix == ".java" and JAVA_SECRET.search(line):
            issues.append((number, "hardcoded Java credential"))
    return issues


def repository_files(root):
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, check=True,
    )
    return sorted({
        root / name.decode("utf-8")
        for name in result.stdout.split(b"\0")
        if name and (root / name.decode("utf-8")).is_file()
    })


def local_link_exists(root, source, destination):
    """Article sources may reference assets from the site's public root."""
    if destination.startswith("/") and source.is_relative_to(root / "docs/content"):
        public_root = (root / "frontend/learntrail-web/public").resolve()
        target = (public_root / destination.lstrip("/")).resolve()
        return target.is_relative_to(public_root) and target.exists()
    return (source.parent / destination).exists()


def check(root):
    issues = []
    files = repository_files(root)
    text_files = {}
    for path in files:
        relative = path.relative_to(root)
        if path.suffix.lower() in FORBIDDEN_KEY_FILES:
            issues.append(str(relative) + ": private keystore belongs outside public sources")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        text_files[path] = text
        if any(part.startswith("tj-") for part in relative.parts):
            issues.append(str(relative) + ": legacy module path")
        if path.suffix == ".java" and re.search(r"(?m)^\s*(?:package|import)\s+(?:static\s+)?com\.tianji\.", text):
            issues.append(str(relative) + ": legacy Java package or import")
        if "src/main/resources" in relative.as_posix() and "com.tianji" in text:
            issues.append(str(relative) + ": legacy resource class reference")
        for line, key in credential_issues(text, path.suffix):
            issues.append(str(relative) + ":" + str(line) + ": literal credential: " + key)
        if path.name == "bootstrap.yml" and re.search(r"^\s*title:.*天机", text, re.M):
            issues.append(str(relative) + ": API title still uses the previous display brand")

    poms = [path for path in files if path.name == "pom.xml"]
    for path in poms:
        try:
            model = ET.fromstring(text_files[path])
        except ET.ParseError:
            issues.append(str(path.relative_to(root)) + ": invalid POM XML")
            continue
        name = model.findtext("m:name", default="", namespaces=NS)
        if model.findtext("m:artifactId", default="", namespaces=NS).startswith("tj-"):
            issues.append(str(path.relative_to(root)) + ": legacy Maven artifact")
        if not name.startswith("LearnTrail :: "):
            issues.append(str(path.relative_to(root)) + ": missing LearnTrail module name")
        parent = model.find("m:parent", NS)
        if parent is not None and parent.findtext("m:groupId", namespaces=NS) == "com.learntrail":
            relative = parent.findtext("m:relativePath", default="../pom.xml", namespaces=NS)
            local_parent = (path.parent / relative).resolve()
            if not local_parent.is_file():
                issues.append(str(path.relative_to(root)) + ": parent POM is missing")
            else:
                parent_model = ET.parse(local_parent).getroot()
                expected = parent.findtext("m:artifactId", namespaces=NS)
                actual = parent_model.findtext("m:artifactId", namespaces=NS)
                if expected != actual:
                    issues.append(str(path.relative_to(root)) + ": parent artifact does not match")
        for module in model.findall("m:modules/m:module", NS):
            if not (path.parent / module.text / "pom.xml").is_file():
                issues.append(str(path.relative_to(root)) + ": missing module " + module.text)

    example = root / ".env.example"
    declared = set(re.findall(r"^(LEARNTRAIL_[A-Z0-9_]+)=", example.read_text(), re.M)) if example.exists() else set()
    required = set()
    for path, text in text_files.items():
        if path.name == "bootstrap.yml":
            required.update(re.findall(r"\$\{(LEARNTRAIL_[A-Z0-9_]+)", text))
    for variable in sorted(required - declared):
        issues.append(".env.example: undocumented variable " + variable)

    for path, text in text_files.items():
        if path.suffix != ".md":
            continue
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if target.startswith(("#", "http:", "https:", "mailto:")):
                continue
            destination = unquote(target.split("#", 1)[0])
            if destination and not local_link_exists(root, path, destination):
                issues.append(str(path.relative_to(root)) + ": broken relative link " + destination)

    root_model = ET.parse(root / "pom.xml").getroot()
    if root_model.findtext("m:artifactId", namespaces=NS) != "lt-parent":
        issues.append("pom.xml: unexpected parent artifact")
    if root_model.findtext("m:groupId", namespaces=NS) != "com.learntrail":
        issues.append("pom.xml: unexpected groupId")
    return issues, len(files), len(poms)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        issues, count, modules = check(root)
    except (OSError, subprocess.CalledProcessError, ET.ParseError) as error:
        print("Repository check could not complete: " + type(error).__name__, file=sys.stderr)
        return 2
    if issues:
        for issue in issues:
            print("ERROR " + issue, file=sys.stderr)
        return 1
    print("LearnTrail checks passed: " + str(count) + " public files, " + str(modules) + " POMs.")
    print("Scope: working tree only; Git history and deployed configuration are not audited.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
