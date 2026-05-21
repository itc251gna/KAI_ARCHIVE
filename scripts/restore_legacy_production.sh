#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  restore_legacy_production.sh --reference-dir PATH [options]

Options:
  --app-dir PATH          Current production app directory. Default: /opt/kai-app
  --reference-dir PATH    Old production reference directory to restore. Required.
  --failed-dir PATH       Where to move the current/new app before restore.
                          Default: <app-dir>-failed-new-YYYYmmdd-HHMMSS
  --old-compose-file PATH Compose file to use after restoring the old app.
                          Default: auto-detect in restored app.
  --skip-current-stop     Do not stop the current/new compose stack before moving it.
  -h, --help              Show this help.
EOF
}

APP_DIR="/opt/kai-app"
REFERENCE_DIR=""
FAILED_DIR=""
OLD_COMPOSE_FILE=""
SKIP_CURRENT_STOP=0

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --app-dir)
      APP_DIR="${2:?Missing value for --app-dir}"
      shift 2
      ;;
    --reference-dir)
      REFERENCE_DIR="${2:?Missing value for --reference-dir}"
      shift 2
      ;;
    --failed-dir)
      FAILED_DIR="${2:?Missing value for --failed-dir}"
      shift 2
      ;;
    --old-compose-file)
      OLD_COMPOSE_FILE="${2:?Missing value for --old-compose-file}"
      shift 2
      ;;
    --skip-current-stop)
      SKIP_CURRENT_STOP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

timestamp() {
  date +"%Y%m%d-%H%M%S"
}

abs_path() {
  local path="$1"
  if [[ "$path" = /* ]]; then
    printf "%s\n" "$path"
  else
    printf "%s/%s\n" "$(pwd)" "$path"
  fi
}

find_compose_file() {
  local base="$1"
  for candidate in \
    "docker-compose.remote.yml" \
    "docker-compose.yml" \
    "compose.yml" \
    "docker-compose.local.yml"; do
    if [[ -f "$base/$candidate" ]]; then
      printf "%s\n" "$base/$candidate"
      return 0
    fi
  done
  return 1
}

if [[ -z "$REFERENCE_DIR" ]]; then
  usage
  exit 2
fi

APP_DIR="$(abs_path "$APP_DIR")"
REFERENCE_DIR="$(abs_path "$REFERENCE_DIR")"
if [[ -z "$FAILED_DIR" ]]; then
  FAILED_DIR="${APP_DIR}-failed-new-$(timestamp)"
else
  FAILED_DIR="$(abs_path "$FAILED_DIR")"
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "Current production app directory does not exist: $APP_DIR" >&2
  exit 1
fi

if [[ ! -d "$REFERENCE_DIR" ]]; then
  echo "Reference directory does not exist: $REFERENCE_DIR" >&2
  exit 1
fi

if [[ -e "$FAILED_DIR" ]]; then
  echo "Failed/new app target already exists: $FAILED_DIR" >&2
  exit 1
fi

if [[ "$SKIP_CURRENT_STOP" -eq 0 ]]; then
  if [[ -f "$APP_DIR/docker-compose.remote.yml" ]]; then
    echo "Stopping current/new production stack..."
    (
      cd "$APP_DIR"
      docker compose -f docker-compose.remote.yml down
    )
  else
    echo "No docker-compose.remote.yml in current app; skipping current stack stop."
  fi
else
  echo "Skipping current stack stop by request."
fi

echo "Moving current/new app out of production path..."
mv "$APP_DIR" "$FAILED_DIR"

echo "Restoring old production reference..."
mv "$REFERENCE_DIR" "$APP_DIR"

if [[ -z "$OLD_COMPOSE_FILE" ]]; then
  if OLD_COMPOSE_FILE="$(find_compose_file "$APP_DIR")"; then
    :
  else
    OLD_COMPOSE_FILE=""
  fi
else
  OLD_COMPOSE_FILE="$(abs_path "$OLD_COMPOSE_FILE")"
fi

if [[ -z "$OLD_COMPOSE_FILE" || ! -f "$OLD_COMPOSE_FILE" ]]; then
  echo "Old app restored, but no compose file was found to start it automatically."
  echo "Restored app: $APP_DIR"
  echo "New app moved to: $FAILED_DIR"
  exit 0
fi

echo "Starting restored old production stack..."
(
  cd "$(dirname "$OLD_COMPOSE_FILE")"
  docker compose -f "$OLD_COMPOSE_FILE" up -d --build
  docker compose -f "$OLD_COMPOSE_FILE" ps
)

cat <<EOF

Legacy production restored.

Restored app:
  $APP_DIR

New app moved to:
  $FAILED_DIR
EOF
