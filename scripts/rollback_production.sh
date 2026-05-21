#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  if [ ! -f ".deploy/previous_revision" ]; then
    echo "No rollback target provided and .deploy/previous_revision does not exist."
    echo "Usage: $0 <release-tag-or-commit>"
    exit 2
  fi
  TARGET="$(cat .deploy/previous_revision)"
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Tracked production files have local changes. Commit or discard them before rollback."
  git status --short
  exit 1
fi

echo "Rolling back to $TARGET..."
git checkout "$TARGET"

docker compose -f docker-compose.remote.yml config --quiet
docker compose -f docker-compose.remote.yml up -d --build
docker compose -f docker-compose.remote.yml ps

echo "Rollback to $TARGET complete."
