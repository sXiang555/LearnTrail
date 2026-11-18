#!/bin/sh
# Build only the pinned official MinIO source; output/cache stay in ignored .local/.
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
task_tools="$task_root/.local/tools/go1.24.8"
task_output="$task_root/.local/minio-image"
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) task_platform=darwin-arm64 ;;
  Darwin-x86_64) task_platform=darwin-amd64 ;;
  Linux-aarch64) task_platform=linux-arm64 ;;
  Linux-x86_64) task_platform=linux-amd64 ;;
  *) printf '%s\n' 'Unsupported build host' >&2; exit 1 ;;
esac
if [ ! -x "$task_tools/go/bin/go" ]; then
  python3 - "$task_tools" "$task_platform" <<'PY'
import hashlib, json, pathlib, shutil, sys, tarfile, tempfile, urllib.request
destination, platform = pathlib.Path(sys.argv[1]), sys.argv[2]
name = 'go1.24.8.' + platform + '.tar.gz'
with urllib.request.urlopen('https://go.dev/dl/?mode=json&include=all', timeout=60) as response:
    versions = json.load(response)
release = next(v for v in versions if v['version'] == 'go1.24.8')
expected = next(f['sha256'] for f in release['files'] if f['filename'] == name)
destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
with tempfile.TemporaryDirectory(prefix='go-download-', dir=destination.parent) as directory:
    folder = pathlib.Path(directory)
    archive = folder / name
    digest = hashlib.sha256()
    with urllib.request.urlopen('https://go.dev/dl/' + name, timeout=60) as response, archive.open('wb') as output:
        while block := response.read(1024 * 1024):
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != expected:
        raise SystemExit('Go archive checksum mismatch')
    with tarfile.open(archive) as package:
        for member in package.getmembers():
            path = pathlib.PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'go':
                raise SystemExit('Unexpected archive path')
        package.extractall(folder, filter='data')
    destination.mkdir(mode=0o700)
    shutil.move(str(folder / 'go'), destination / 'go')
print('Verified official Go 1.24.8 toolchain')
PY
fi
mkdir -p "$task_output" "$task_root/.local/go/cache" "$task_root/.local/go/modules"
chmod 700 "$task_output" "$task_root/.local/go"
export GOCACHE="$task_root/.local/go/cache" GOMODCACHE="$task_root/.local/go/modules"
export GOPATH="$task_root/.local/go" GOTOOLCHAIN=local GOMAXPROCS=2
task_go="$task_tools/go/bin/go"
task_release=RELEASE.2025-10-15T17-29-55Z
"$task_go" mod download -json "github.com/minio/minio@$task_release" > "$task_output/source.json"
task_source=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["Dir"])' "$task_output/source.json")
(
  cd "$task_source"
  CGO_ENABLED=0 GOOS=linux GOARCH=arm64 "$task_go" build -p 2 -trimpath -buildvcs=false \
    -ldflags '-s -w' -o "$task_output/minio" .
)
cp "$task_root/deploy/minio/Dockerfile" "$task_output/Dockerfile"
shasum -a 256 "$task_output/minio"
printf '%s\n' 'Built official MinIO source for linux/arm64; no server was started.'
