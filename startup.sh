#!/bin/sh
# Build and run one packaged LearnTrail service from any working directory.
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PROJECT_NAME=""
PROJECT_PATH=""
CONTAINER_NAME=""
PORT=8080
DEBUG_PORT=0
JAVA_OPTS="${JAVA_OPTS:--Xms256m -Xmx512m}"
ENV_FILE=""
KEYSTORE_FILE=""
NETWORK=""
DRY_RUN=0

usage() {
    cat <<'USAGE'
LearnTrail service launcher
Usage: sh startup.sh -n ARTIFACT -d MODULE [-c CONTAINER] [-p PORT]
                     [-o JVM_OPTIONS] [-a DEBUG_PORT] [-e ENV_FILE]
                     [-k KEYSTORE_FILE] [-w NETWORK] [--dry-run]

Example:
  sh startup.sh -n tj-learning -d tj-learning -p 8090 -e .env.local --dry-run

Requires a packaged module JAR and prepared external services.
Existing containers are never removed automatically.
USAGE
}

fail() {
    printf 'LearnTrail: %s\n' "$*" >&2
    exit 2
}

port_is_valid() {
    case "$1" in ""|*[!0-9]*) return 1 ;; esac
    [ "${#1}" -le 5 ] && [ "$1" -gt 0 ] && [ "$1" -le 65535 ]
}

run() {
    printf '+'
    for arg do printf ' [%s]' "$arg"; done
    printf '\n'
    if [ "$DRY_RUN" -eq 0 ]; then "$@"; fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -c|-n|-d|-p|-o|-a|-e|-k|-w)
            [ "$#" -ge 2 ] || fail "Missing value for $1"
            case "$1" in
                -c) CONTAINER_NAME=$2 ;; -n) PROJECT_NAME=$2 ;;
                -d) PROJECT_PATH=$2 ;; -p) PORT=$2 ;;
                -o) JAVA_OPTS=$2 ;; -a) DEBUG_PORT=$2 ;;
                -e) ENV_FILE=$2 ;; -k) KEYSTORE_FILE=$2 ;;
                -w) NETWORK=$2 ;;
            esac
            shift 2 ;;
        *) fail "Unknown argument: $1" ;;
    esac
done

case "$PROJECT_NAME" in
    ""|*[!a-z0-9_.-]*|-*|.*|_*) fail "Supply a valid lowercase artifact name with -n" ;;
esac
case "$PROJECT_PATH" in
    ""|/*|..|../*|*/../*|*/..) fail "Module must be a relative path inside this repository" ;;
esac
[ -d "$PROJECT_ROOT/$PROJECT_PATH" ] || fail "Module directory not found: $PROJECT_PATH"
MODULE_ROOT=$(CDPATH= cd -- "$PROJECT_ROOT/$PROJECT_PATH" && pwd -P)
case "$MODULE_ROOT/" in
    "$PROJECT_ROOT/"*) ;;
    *) fail "Module resolves outside this repository" ;;
esac

port_is_valid "$PORT" || fail "Port must be between 1 and 65535"
if [ "$DEBUG_PORT" != "0" ]; then
    port_is_valid "$DEBUG_PORT" || fail "Debug port must be between 1 and 65535"
    [ "$PORT" != "$DEBUG_PORT" ] || fail "Application and debug host ports must differ"
    JAVA_OPTS="$JAVA_OPTS -agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=*:5005"
fi

CONTAINER_NAME="${CONTAINER_NAME:-learntrail-${PROJECT_NAME#tj-}}"
case "$CONTAINER_NAME" in
    *[!a-zA-Z0-9_.-]*|-*|.*|_*) fail "Invalid container name" ;;
esac
JAR_FILE="$PROJECT_PATH/target/$PROJECT_NAME.jar"
[ -f "$PROJECT_ROOT/$JAR_FILE" ] || fail "Package the module first; missing JAR: $JAR_FILE"
[ -f "$PROJECT_ROOT/Dockerfile" ] || fail "Repository Dockerfile not found"

if [ -n "$ENV_FILE" ]; then
    [ -r "$ENV_FILE" ] && [ -f "$ENV_FILE" ] || fail "Environment file is not readable: $ENV_FILE"
fi
if [ -n "$KEYSTORE_FILE" ]; then
    [ -r "$KEYSTORE_FILE" ] && [ -f "$KEYSTORE_FILE" ] || fail "Keystore file is not readable"
    KEYSTORE_FILE=$(CDPATH= cd -- "$(dirname -- "$KEYSTORE_FILE")" && pwd -P)/$(basename -- "$KEYSTORE_FILE")
    case "$KEYSTORE_FILE" in *,*) fail "Keystore paths containing commas are unsupported" ;; esac
fi

if [ "$DRY_RUN" -eq 0 ]; then
    command -v docker >/dev/null 2>&1 || fail "Docker is not installed"
    docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable"
    if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
        fail "Container already exists: $CONTAINER_NAME; choose another name or manage it explicitly"
    fi
    if [ -n "$NETWORK" ]; then
        docker network inspect "$NETWORK" >/dev/null 2>&1 || fail "Docker network does not exist: $NETWORK"
    fi
fi

IMAGE_NAME="learntrail/$PROJECT_NAME:local"
run docker build --file "$PROJECT_ROOT/Dockerfile" \
    --build-arg "JAR_FILE=$JAR_FILE" --tag "$IMAGE_NAME" "$PROJECT_ROOT"

set -- docker run -d --name "$CONTAINER_NAME" -p "$PORT:$PORT"
if [ -n "$NETWORK" ]; then set -- "$@" --network "$NETWORK"; fi
if [ -n "$ENV_FILE" ]; then set -- "$@" --env-file "$ENV_FILE"; fi
if [ -n "$KEYSTORE_FILE" ]; then
    set -- "$@" --mount "type=bind,source=$KEYSTORE_FILE,target=/run/secrets/learntrail-auth.jks,readonly" \
        -e "LEARNTRAIL_JWT_KEYSTORE_LOCATION=file:/run/secrets/learntrail-auth.jks"
fi
if [ "$DEBUG_PORT" != "0" ]; then
    set -- "$@" -p "127.0.0.1:$DEBUG_PORT:5005"
fi
set -- "$@" -e "JAVA_OPTS=$JAVA_OPTS" "$IMAGE_NAME" "--server.port=$PORT"
run "$@"
