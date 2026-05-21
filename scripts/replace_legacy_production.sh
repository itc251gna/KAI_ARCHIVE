#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  replace_legacy_production.sh <release-tag-or-commit> [options]

Options:
  --app-dir PATH          Existing production app directory. Default: /opt/kai-app
  --reference-dir PATH    Where to move the old production app.
                          Default: <app-dir>-old-reference-YYYYmmdd-HHMMSS
  --repo-url URL          Git repository to clone.
                          Default: git@github.com:itc251gna/KAI_ARCHIVE.git
  --ssh-key PATH          SSH private key to use for the clone/fetch.
  --old-compose-file PATH Compose file used by the old app. Auto-detected by default.
  --skip-old-stop         Do not try to stop the old compose stack before moving it.
  -h, --help              Show this help.

This script is for the first replacement of the legacy production folder.
It keeps the old folder as a reference, clones the tested Git release into
the same app path, copies only production-only config/certs, and starts the
new remote compose stack.

It intentionally does not copy old production data by default:
  postgres_data/
  backups/
  static/scans/
EOF
}

TARGET="${1:-}"
if [[ -z "$TARGET" || "$TARGET" == "-h" || "$TARGET" == "--help" ]]; then
  usage
  exit 0
fi
shift

APP_DIR="/opt/kai-app"
REFERENCE_DIR=""
REPO_URL="git@github.com:itc251gna/KAI_ARCHIVE.git"
SSH_KEY=""
OLD_COMPOSE_FILE=""
SKIP_OLD_STOP=0

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
    --repo-url)
      REPO_URL="${2:?Missing value for --repo-url}"
      shift 2
      ;;
    --ssh-key)
      SSH_KEY="${2:?Missing value for --ssh-key}"
      shift 2
      ;;
    --old-compose-file)
      OLD_COMPOSE_FILE="${2:?Missing value for --old-compose-file}"
      shift 2
      ;;
    --skip-old-stop)
      SKIP_OLD_STOP=1
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

find_old_compose_file() {
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

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

APP_DIR="$(abs_path "$APP_DIR")"
if [[ -z "$REFERENCE_DIR" ]]; then
  REFERENCE_DIR="${APP_DIR}-old-reference-$(timestamp)"
else
  REFERENCE_DIR="$(abs_path "$REFERENCE_DIR")"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ "$BOOTSTRAP_ROOT" == "$APP_DIR" ]]; then
  echo "Do not run this script from the legacy app directory itself." >&2
  echo "Clone this repo to a temporary path first, then run the script from there." >&2
  exit 1
fi

require_command git
require_command docker

if [[ ! -d "$APP_DIR" ]]; then
  echo "Existing production app directory does not exist: $APP_DIR" >&2
  exit 1
fi

if [[ -e "$REFERENCE_DIR" ]]; then
  echo "Reference directory already exists: $REFERENCE_DIR" >&2
  exit 1
fi

GIT_SSH_COMMAND_VALUE=""
if [[ -n "$SSH_KEY" ]]; then
  SSH_KEY="$(abs_path "$SSH_KEY")"
  if [[ ! -f "$SSH_KEY" ]]; then
    echo "SSH key does not exist: $SSH_KEY" >&2
    exit 1
  fi
  GIT_SSH_COMMAND_VALUE="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

if [[ -z "$OLD_COMPOSE_FILE" ]]; then
  if OLD_COMPOSE_FILE="$(find_old_compose_file "$APP_DIR")"; then
    :
  else
    OLD_COMPOSE_FILE=""
  fi
else
  OLD_COMPOSE_FILE="$(abs_path "$OLD_COMPOSE_FILE")"
  if [[ ! -f "$OLD_COMPOSE_FILE" ]]; then
    echo "Old compose file does not exist: $OLD_COMPOSE_FILE" >&2
    exit 1
  fi
fi

LEGACY_COMPOSE_REFERENCE_FILE=""
if [[ -n "$OLD_COMPOSE_FILE" && "$OLD_COMPOSE_FILE" == "$APP_DIR/"* ]]; then
  LEGACY_COMPOSE_REFERENCE_FILE="${REFERENCE_DIR}${OLD_COMPOSE_FILE#"$APP_DIR"}"
elif [[ -n "$OLD_COMPOSE_FILE" ]]; then
  LEGACY_COMPOSE_REFERENCE_FILE="$OLD_COMPOSE_FILE"
fi

echo "Replacement target:"
echo "  release:       $TARGET"
echo "  old app dir:   $APP_DIR"
echo "  reference dir: $REFERENCE_DIR"
echo "  repo url:      $REPO_URL"
if [[ -n "$OLD_COMPOSE_FILE" ]]; then
  echo "  old compose:   $OLD_COMPOSE_FILE"
else
  echo "  old compose:   not found; old stack will not be stopped automatically"
fi

if [[ "$SKIP_OLD_STOP" -eq 0 && -n "$OLD_COMPOSE_FILE" ]]; then
  echo "Stopping old production stack..."
  (
    cd "$(dirname "$OLD_COMPOSE_FILE")"
    docker compose -f "$OLD_COMPOSE_FILE" down
  )
elif [[ "$SKIP_OLD_STOP" -eq 1 ]]; then
  echo "Skipping old stack stop by request."
fi

echo "Moving old production app to reference directory..."
mv "$APP_DIR" "$REFERENCE_DIR"

echo "Cloning tested release into production app path..."
if [[ -n "$GIT_SSH_COMMAND_VALUE" ]]; then
  GIT_SSH_COMMAND="$GIT_SSH_COMMAND_VALUE" git clone "$REPO_URL" "$APP_DIR"
else
  git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"
if [[ -n "$GIT_SSH_COMMAND_VALUE" ]]; then
  git config core.sshCommand "$GIT_SSH_COMMAND_VALUE"
fi

echo "Fetching tags and checking out $TARGET..."
git fetch origin --tags
git checkout "$TARGET"

echo "Copying production-only config and certificates from reference..."
if [[ -f "$REFERENCE_DIR/.env" ]]; then
  cp "$REFERENCE_DIR/.env" "$APP_DIR/.env"
fi

if [[ -d "$REFERENCE_DIR/ssl" ]]; then
  cp -a "$REFERENCE_DIR/ssl" "$APP_DIR/ssl"
fi

mkdir -p "$APP_DIR/guacamole_config"
if [[ -f "$REFERENCE_DIR/guacamole_config/user-mapping.xml" ]]; then
  cp "$REFERENCE_DIR/guacamole_config/user-mapping.xml" "$APP_DIR/guacamole_config/user-mapping.xml"
fi

mkdir -p postgres_data backups static/scans .deploy
printf "%s\n" "$REFERENCE_DIR" > .deploy/legacy_reference_dir
printf "%s\n" "$TARGET" > .deploy/installed_revision
if [[ -n "$OLD_COMPOSE_FILE" ]]; then
  printf "%s\n" "$LEGACY_COMPOSE_REFERENCE_FILE" > .deploy/legacy_compose_file
fi

for required in ".env" "ssl/cert.pem" "ssl/key.pem" "guacamole_config/user-mapping.xml"; do
  if [[ ! -f "$required" ]]; then
    echo "Missing required production file after cutover copy: $required" >&2
    echo "The old app is preserved at: $REFERENCE_DIR" >&2
    echo "Fix the missing file in $APP_DIR, then run scripts/deploy_production.sh $TARGET" >&2
    exit 1
  fi
done

echo "Validating new production compose..."
docker compose -f docker-compose.remote.yml config --quiet

echo "Building and starting new production stack..."
docker compose -f docker-compose.remote.yml up -d --build

echo "New production stack status:"
docker compose -f docker-compose.remote.yml ps

cat <<EOF

Replacement complete.

Old production app is preserved at:
  $REFERENCE_DIR

If the new app must be removed and the old reference restored, run:
  cd $APP_DIR
  ./scripts/restore_legacy_production.sh --reference-dir "$REFERENCE_DIR"
EOF
