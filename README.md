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

## Backup Management

Admins use `/manage_backups` for encrypted KAI backups. A backup includes:

- PostgreSQL dump or local SQLite copy.
- Uploaded/generated files under `static/scans/`.
- Active document templates under `static/templates/`.
- `manifest.json` with app revision, actor, auth method, database mode, and archive entries.

Each backup is recorded in the `backup_record` table with status, size, SHA-256 hash, and verification result. Use the page actions to create, verify, and download backups. The legacy `/force_backup` route is kept only as a compatibility redirect into the same backup workflow.

Production app images must include `pg_dump`; the Dockerfile installs `postgresql-client` for this reason. Environment controls:

```dotenv
BACKUP_PASSWORD=change-me
BACKUP_RETENTION_COUNT=30
BACKUP_INCLUDE_DATABASE=1
```

## Central SSO

For the intranet SSO gateway rollout, KAI can accept trusted SSO headers while keeping the local PostgreSQL username/password login as fallback:

```text
TRUST_SSO_HEADERS=1
SSO_TRUSTED_PROXY_CIDRS=127.0.0.1/32,172.16.0.0/12
SSO_KAI_USER_GROUP=/apps/kai/users
SSO_KAI_ADMIN_GROUP=/apps/kai/admins
SSO_GLOBAL_ADMIN_GROUP=/apps/global/admins
CENTRAL_AUTH_REALM=intranet
CENTRAL_AUTH_ADMIN_URL=https://auth.251gh.local/admin/
CENTRAL_AUTH_USERS_URL=https://auth.251gh.local/admin/master/console/#/intranet/users
CENTRAL_AUTH_GROUPS_URL=https://auth.251gh.local/admin/master/console/#/intranet/groups
ALLOW_LOCAL_USER_ADMIN_FROM_SSO=0
```

SSO users are transient Flask-Login users and are not inserted into PostgreSQL. `/apps/kai/users` gets normal KAI access; `/apps/kai/admins` and `/apps/global/admins` get admin behavior through `is_admin_user()`.

The `/manage_users` page is central-Auth aware. In production SSO sessions it links administrators to Keycloak and shows the KAI rights groups. The old local user table remains visible only as an emergency/local-login fallback and is read-only for SSO admins unless `ALLOW_LOCAL_USER_ADMIN_FROM_SSO=1` is deliberately set.
