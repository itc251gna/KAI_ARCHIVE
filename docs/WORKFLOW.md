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

## Deploy Production

Deploy one tested tag:

```bash
cd /opt/kai-app
./scripts/deploy_production.sh kai-v2026-05-21-feature
```

The script:

- refuses to deploy over manual tracked-file edits
- fetches Git tags
- checks out the requested tag
- verifies required production-only files
- validates Docker Compose
- rebuilds and starts the production stack

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

Do not edit tracked files manually on production. Make changes locally, test them locally, commit/tag/push them, then deploy that tag.
