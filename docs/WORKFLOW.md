# KAI App Release Workflow

This repo follows a local-tested, tag-based production flow.

## Roles

- Local replica: development and validation workspace.
- GitHub: source of truth for approved code.
- Production server: deploys only explicit Git tags or commit IDs.

Production-only files stay on the production server and are not committed:

- `.env`
- `ssl/`
- `postgres_data/`
- `backups/`
- `static/scans/`
- `guacamole_config/user-mapping.xml`

## Local Development

From the local replica folder:

```powershell
docker compose -f docker-compose.local.yml up -d --build
```

Open:

```text
https://localhost:8443
```

Before a release, run:

```powershell
.\scripts\verify_local.ps1
```

For a fuller local check that starts the stack and tests HTTPS endpoints:

```powershell
.\scripts\verify_local.ps1 -StartStack -SmokeTest
```

## Create A Release

If changes are already committed:

```powershell
.\scripts\create_release.ps1 -Tag kai-v2026-05-21-feature -Push
```

If changes should be committed as part of the release:

```powershell
.\scripts\create_release.ps1 -Tag kai-v2026-05-21-feature -CommitMessage "Update KAI app feature" -Push
```

The tag format is:

```text
kai-vYYYY-MM-DD
kai-vYYYY-MM-DD-short-name
```

## First Production Setup

On the Ubuntu production server:

```bash
git clone git@github.com:itc251gna/KAI_ARCHIVE.git /opt/kai-app
cd /opt/kai-app
cp .env.example .env
cp guacamole_config/user-mapping.example.xml guacamole_config/user-mapping.xml
mkdir -p ssl postgres_data backups static/scans
```

Then fill the real production values:

- `.env`
- `ssl/cert.pem`
- `ssl/key.pem`
- `guacamole_config/user-mapping.xml`

## Replace Existing Legacy Production

Use this only for the first migration from the old production folder to this
Git-based replica. It preserves the old production app as a reference folder,
does not copy old runtime data, and starts the tested release in the same
production path so the existing links keep working.

Bootstrap the script from a temporary checkout on the Ubuntu server:

```bash
rm -rf /tmp/kai-cutover
git clone git@github.com:itc251gna/KAI_ARCHIVE.git /tmp/kai-cutover
cd /tmp/kai-cutover
git checkout kai-vYYYY-MM-DD-name
```

If the server needs a specific GitHub deploy key, create it on the server,
add the public key to the GitHub repository deploy keys, and use it for the
temporary clone:

```bash
ssh-keygen -t ed25519 -C "kai-prod" -f ~/.ssh/kai_archive_prod -N ""
cat ~/.ssh/kai_archive_prod.pub

rm -rf /tmp/kai-cutover
GIT_SSH_COMMAND="ssh -i ~/.ssh/kai_archive_prod -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
  git clone git@github.com:itc251gna/KAI_ARCHIVE.git /tmp/kai-cutover
cd /tmp/kai-cutover
git checkout kai-vYYYY-MM-DD-name
```

Then run:

```bash
./scripts/replace_legacy_production.sh kai-vYYYY-MM-DD-name \
  --app-dir /opt/kai-app \
  --ssh-key ~/.ssh/kai_archive_prod
```

Without a custom key:

```bash
./scripts/replace_legacy_production.sh kai-vYYYY-MM-DD-name \
  --app-dir /opt/kai-app
```

The script moves the old app to a path like:

```text
/opt/kai-app-old-reference-YYYYmmdd-HHMMSS
```

It copies only:

- `.env`
- `ssl/`
- `guacamole_config/user-mapping.xml`

It creates fresh runtime directories:

- `postgres_data/`
- `backups/`
- `static/scans/`

It does not copy old production user data.

If the new production app must be removed and the old reference restored:

```bash
cd /opt/kai-app
./scripts/restore_legacy_production.sh \
  --reference-dir /opt/kai-app-old-reference-YYYYmmdd-HHMMSS
```

## Deploy Production

Deploy one tested tag or explicit commit that is already on `origin/main`:

```bash
cd /home/kmh251/deployment/kai_app
./scripts/deploy_production.sh kai-v2026-05-21-feature
```

or:

```bash
./scripts/deploy_production.sh <commit-sha-from-origin-main>
```

The script:

- refuses to deploy unless the production checkout is clean, including untracked non-ignored files
- refuses ambiguous targets such as `main`, `origin/main`, or `HEAD`
- fetches `origin/main` and tags
- resolves the requested tag/commit to an immutable commit SHA
- verifies that the target commit is contained in `origin/main`
- checks out the resolved commit detached, not a mutable branch name
- verifies required production-only files
- validates Docker Compose
- rebuilds and starts the production stack
- records the deployed target and revision under `.deploy/`

## Rollback

Rollback to the previous deployed Git revision:

```bash
cd /opt/kai-app
./scripts/rollback_production.sh
```

Rollback to a specific tag:

```bash
./scripts/rollback_production.sh kai-v2026-05-21-initial
```

## Production Rule

Do not edit application files manually on production. Make changes locally, test them locally, commit to `main`, push to GitHub, then deploy an explicit tag or commit SHA that exists on `origin/main`.

Production deploy must never be made from random workspace changes, local-only commits, mutable branch names, or an unclean checkout. If the production folder is dirty, fix that first by committing the change in local development and redeploying, or by deliberately stashing/removing local diagnostic files.
