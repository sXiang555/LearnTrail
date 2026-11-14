#!/usr/bin/env python3
"""Run local Java services against existing infrastructure; Ctrl+C stops them."""

import argparse
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
SERVICES = {
    "user": ("lt-user", 8082),
    "auth": ("lt-auth/lt-auth-service", 8081),
    "gateway": ("lt-gateway", 10010),
    "course": ("lt-course", 8086),
    "search": ("lt-search", 8083),
    "learning": ("lt-learning", 8090),
    "trade": ("lt-trade", 8088),
    "promotion": ("lt-promotion", 8092),
    "remark": ("lt-remark", 8091),
    "message": ("lt-message/lt-message-service", 8085),
    "exam": ("lt-exam", 8089),
    "pay": ("lt-pay/lt-pay-service", 8087),
    "media": ("lt-media", 8084),
    "data": ("lt-data", 8093),
}


def read_env(path):
    values = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("Invalid environment assignment at line " + str(number))
        value = value.strip()
        if value.startswith(("'", '"')):
            parts = shlex.split(value, comments=True)
            if len(parts) != 1:
                raise ValueError("Invalid quoted value at line " + str(number))
            value = parts[0]
        if value.startswith("-----BEGIN"):
            value = value.replace("\\n", "\n")
        values[key] = value
    return values


def java11(home):
    candidates = [home, os.environ.get("JAVA_HOME")]
    if sys.platform == "darwin":
        result = subprocess.run(["/usr/libexec/java_home", "-v", "11"], capture_output=True, text=True)
        if result.returncode == 0:
            candidates.append(result.stdout.strip())
    for candidate in candidates:
        if not candidate:
            continue
        java = Path(candidate).expanduser() / "bin/java"
        if java.is_file():
            result = subprocess.run([str(java), "-version"], capture_output=True, text=True)
            if re.search(r'version "11[.\"]', result.stderr + result.stdout):
                return java.resolve()
        if home:
            break
    raise ValueError("JDK 11 is required; specify --java-home or JAVA_HOME")


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def local_network_options(infra_host, register_ip):
    # Java nonProxyHosts accepts host patterns, not CIDR networks.
    hosts = list(dict.fromkeys(["localhost", "127.*", "[::1]", infra_host, register_ip]))
    if any(not host or any(c.isspace() or c in "|/" for c in host) for host in hosts):
        raise ValueError("Local infrastructure and registration hosts must be individual hosts")
    bypass = "|".join(hosts)
    return ["-Dhttp.nonProxyHosts=" + bypass, "-DsocksNonProxyHosts=" + bypass]


def stop_children(children):
    for _, process, _ in children:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 12
    for _, process, _ in children:
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def select_nacos_config_prefix(opener, infra_host, service):
    """Prefer the canonical Data ID while supporting an existing installation."""
    canonical = "lt-" + service
    for prefix in (canonical, service + "-service"):
        for suffix in ("", ".yaml", "-local.yaml"):
            url = ("http://" + infra_host + ":8848/nacos/v1/cs/configs?dataId="
                   + prefix + suffix + "&group=DEFAULT_GROUP")
            try:
                with opener.open(url, timeout=5) as response:
                    if response.status == 200 and response.read().strip():
                        return prefix
            except HTTPError as error:
                if error.code != 404:
                    raise
    return canonical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", nargs="+", choices=SERVICES, default=list(SERVICES))
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.local")
    parser.add_argument("--java-home")
    parser.add_argument("--infra-host", default="192.168.150.101")
    parser.add_argument("--register-ip", help="Local IP reachable by the VM; auto-detected by default")
    parser.add_argument("--enable-wechat", action="store_true", help="Requires valid merchant credentials")
    args = parser.parse_args()
    children = []
    try:
        java = java11(args.java_home)
        env = read_env(args.env_file.expanduser())
        env.update(os.environ)  # Explicit process environment takes precedence.
        location = env.get("LEARNTRAIL_JWT_KEYSTORE_LOCATION", "")
        if "auth" in args.services:
            if not location.startswith("file:"):
                raise ValueError("LEARNTRAIL_JWT_KEYSTORE_LOCATION must use an external file: path")
            key_path = Path(location[5:]).expanduser()
            if not key_path.is_absolute():
                key_path = ROOT / key_path
            if not key_path.is_file():
                raise ValueError("JWT keystore file does not exist")
            env["LEARNTRAIL_JWT_KEYSTORE_LOCATION"] = "file:" + str(key_path.resolve())
        opener = build_opener(ProxyHandler({}))
        with opener.open("http://" + args.infra_host + ":8848/nacos/v1/console/health/readiness", timeout=5) as response:
            if response.status != 200 or response.read().strip() != b"OK":
                raise ValueError("Nacos is not ready")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((args.infra_host, 8848))
            register_ip = args.register_ip or probe.getsockname()[0]
        services = list(dict.fromkeys(args.services))
        config_prefixes = {
            name: select_nacos_config_prefix(opener, args.infra_host, name)
            for name in services
        }
        for name in services:
            module, port = SERVICES[name]
            jar = ROOT / module / "target" / (Path(module).name + ".jar")
            if not jar.is_file():
                raise ValueError("Missing JAR for " + name + "; run mvn -DskipTests package first")
            if port_open(port):
                raise ValueError("Port " + str(port) + " is occupied; no services were started")
        runtime = ROOT / ".local" / "run"
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        runtime.chmod(0o700)
        print("JDK: " + str(java), flush=True)
        print("Service registration IP: " + register_ip, flush=True)
        print("WeChat: " + ("enabled" if args.enable_wechat else "disabled; real payments are not verified"), flush=True)
        for name in services:
            module, port = SERVICES[name]
            folder = runtime / name
            folder.mkdir(mode=0o700, exist_ok=True)
            folder.chmod(0o700)
            jar = ROOT / module / "target" / (Path(module).name + ".jar")
            command = [str(java), "-Xms32m", "-Xmx256m", *local_network_options(args.infra_host, register_ip), "-jar", str(jar),
                       "--spring.profiles.active=local",
                       "--spring.cloud.nacos.server-addr=" + args.infra_host + ":8848",
                       "--spring.cloud.nacos.config.prefix=" + config_prefixes[name],
                       "--spring.cloud.nacos.discovery.ip=" + register_ip,
                       "--logging.level.com.learntrail=info", "--logging.level.com.alibaba.nacos=warn",
                       "--logging.level.io.seata=warn", "--logging.file.path=" + str(folder),
                       "--tj.xxl-job.executor.ip=" + register_ip,
                       "--tj.xxl-job.executor.port=" + str(port + 11000),
                       "--tj.xxl-job.executor.log-path=" + str(folder / "job")]
            if name == "pay":
                command += ["--tj.pay.wx.enabled=" + str(args.enable_wechat).lower(),
                            "--tj.xxl-job.admin.address="]
            log_path = folder / "console.log"
            fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as output:
                process = subprocess.Popen(command, cwd=folder, env=env, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
            children.append((name, process, log_path))
            print("Starting " + name + " pid=" + str(process.pid) + " log=" + str(log_path), flush=True)
            time.sleep(0.4)
        pending = set(services)
        deadline = time.monotonic() + 90
        while True:
            for name, process, log_path in children:
                if process.poll() is not None:
                    raise RuntimeError(name + " exited; inspect " + str(log_path))
                if name in pending:
                    log = log_path.read_text(encoding="utf-8", errors="replace")
                    if re.search(r"Started \w+Application in ", log):
                        pending.remove(name)
                        print("Started " + name, flush=True)
            if pending and time.monotonic() > deadline:
                raise RuntimeError("Startup timed out: " + ", ".join(sorted(pending)))
            if not pending:
                print("All selected services started. Keep this terminal open; Ctrl+C stops them.", flush=True)
                break
            time.sleep(1)
        while True:
            for name, process, log_path in children:
                if process.poll() is not None:
                    raise RuntimeError(name + " exited; inspect " + str(log_path))
            time.sleep(2)
    except KeyboardInterrupt:
        print("Stopping services started by this runner.", flush=True)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        # Never print environment values or complete exceptions from external services.
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print("Local startup failed: " + message, file=sys.stderr)
        return 1
    finally:
        stop_children(children)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    raise SystemExit(main())
