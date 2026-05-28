# KAI App

KAI App is a Dockerized Flask/PostgreSQL application for medical examination workflows, document generation, audit logging, and HIS access through Apache Guacamole.

## Active Environments

- Local replica: `docker-compose.local.yml`
- Production-like deployment: `docker-compose.remote.yml`

The default `docker-compose.yml` entrypoint was intentionally removed to avoid ambiguity.

## Local Run

```powershell
docker compose -f docker-compose.local.yml up -d --build
```

Open:

```text
https://localhost:8443
```

## Verify

```powershell
.\scripts\verify_local.ps1
```

Full local verification:

```powershell
.\scripts\verify_local.ps1 -StartStack -SmokeTest
```

## Release Workflow

The approved workflow is documented in [docs/WORKFLOW.md](docs/WORKFLOW.md).

Short version:

```powershell
.\scripts\create_release.ps1 -Tag kai-vYYYY-MM-DD-name -CommitMessage "Update KAI app" -Push
```

Production deploys only tested tags:

```bash
./scripts/deploy_production.sh kai-vYYYY-MM-DD-name
```

The production deploy script enforces a clean production checkout and only accepts explicit tags or commit SHAs that are contained in `origin/main`. It refuses mutable targets such as `main`, `origin/main`, or `HEAD`.

For the first replacement of an existing legacy production folder, use:

```bash
./scripts/replace_legacy_production.sh kai-vYYYY-MM-DD-name --app-dir /opt/kai-app
```

## Runtime Secrets

Do not commit production secrets or operational data. Keep these local to each environment:

- `.env`
- `ssl/`
- `postgres_data/`
- `backups/`
- `static/scans/`
- `guacamole_config/user-mapping.xml`

Use `.env.example` and `guacamole_config/user-mapping.example.xml` as templates.
