#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <release-tag-or-commit>"
  echo "Example: $0 kai-v2026-05-21-initial"
  exit 2
fi

TARGET="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d ".git" ]; then
  echo "This directory is not a Git checkout: $REPO_ROOT"
  exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Tracked production files have local changes. Commit or discard them before deploying."
  git status --short
  exit 1
fi

mkdir -p .deploy
git rev-parse HEAD > .deploy/previous_revision

echo "Fetching origin and tags..."
git fetch origin --tags

echo "Checking out $TARGET..."
git checkout "$TARGET"

for required in ".env" "ssl/cert.pem" "ssl/key.pem" "guacamole_config/user-mapping.xml"; do
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

echo "Deployed $TARGET successfully."
