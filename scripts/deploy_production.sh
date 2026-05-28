#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <release-tag-or-commit>"
  echo "Example: $0 kai-v2026-05-21-initial"
  exit 2
fi

TARGET="$1"
case "$TARGET" in
  main|origin/main|HEAD|@)
    echo "Refusing ambiguous production target: $TARGET" >&2
    echo "Deploy an explicit release tag or commit SHA that is already on origin/main." >&2
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d ".git" ]; then
  echo "This directory is not a Git checkout: $REPO_ROOT"
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Production checkout is not clean. Commit, stash, remove, or ignore local files before deploying."
  git status --short
  exit 1
fi

mkdir -p .deploy
git rev-parse HEAD > .deploy/previous_revision

echo "Fetching origin/main and tags..."
git fetch --prune origin +refs/heads/main:refs/remotes/origin/main --tags

if ! TARGET_COMMIT="$(git rev-parse --verify "${TARGET}^{commit}" 2>/dev/null)"; then
  echo "Target does not resolve to a commit: $TARGET" >&2
  exit 1
fi

if ! git merge-base --is-ancestor "$TARGET_COMMIT" origin/main; then
  echo "Refusing to deploy $TARGET ($TARGET_COMMIT)." >&2
  echo "The target commit is not contained in origin/main." >&2
  exit 1
fi

echo "Checking out $TARGET ($TARGET_COMMIT) from origin/main..."
git checkout --detach "$TARGET_COMMIT"

if [ "$(git rev-parse HEAD)" != "$TARGET_COMMIT" ]; then
  echo "Checkout verification failed; HEAD is not the requested target." >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Production checkout became dirty after checkout; refusing to deploy." >&2
  git status --short
  exit 1
fi

for required in ".env" "guacamole_config/user-mapping.xml"; do
  if [ ! -f "$required" ]; then
    echo "Missing required production file: $required"
    exit 1
  fi
done

mkdir -p postgres_data backups static/scans

echo "Validating remote compose..."
docker compose -f docker-compose.remote.yml config --quiet

echo "Building and starting production stack..."
docker compose -f docker-compose.remote.yml up -d --build

echo "Production stack status:"
docker compose -f docker-compose.remote.yml ps

printf "%s\n" "$TARGET_COMMIT" > .deploy/deployed_revision
printf "%s\n" "$TARGET" > .deploy/deployed_target

echo "Deployed $TARGET ($TARGET_COMMIT) successfully."
