# KAI App Replica Runbook

This folder is a working replica of the KAI application for controlled testing.

Source copied from:

Legacy local KAI App source folder.

## What Is Faithful

- `app.py`, templates, static assets, document templates, Nginx config, Dockerfile, requirements, SSL folder, and Guacamole config were copied from the local dev project.
- Production HTTP checks showed the sampled static assets match local dev by SHA-256.
- The active Docker Compose entrypoints are `docker-compose.local.yml` for workstation testing and `docker-compose.remote.yml` for production-like deployment.

## What Is Intentionally Not Copied

Operational data was not kept in this replica:

- `.git`
- `venv`
- `postgres_data`
- `backups`
- `__pycache__`
- `static/scans` contents

The folders needed at runtime exist or are created by Docker. This keeps the replica runnable without carrying old user medical files.

## Local Run

Use the local profile to avoid conflicts with the old local dev containers and port 443:

```powershell
cd <local-kai-app-replica-folder>
docker compose -f docker-compose.local.yml up -d --build
```

Open:

`https://localhost:8443`

Local-only runtime adjustments:

- `docker-compose.local.yml` uses unique container names and host ports (`8443` for HTTPS, `15432` for PostgreSQL).
- `docker-compose.local.yml` waits for PostgreSQL health before starting the app.
- `docker-compose.local.yml` runs the app with 1 Gunicorn worker so a first boot with an empty local DB does not race on `db.create_all()`.
- `nginx.local.conf` preserves the browser host and port with `Host $http_host`; this is required because Flask-WTF performs strict HTTPS referrer checks and the local URL includes `:8443`.
- `docker-compose.local.yml` mounts `ssl/local` as the local HTTPS certificate folder. This is separate from the copied production-like `ssl/cert.pem` and `ssl/key.pem`.
- The helper compose files pass `.env` as `env_file`; `.dockerignore` keeps `.env`, SSL material, Guacamole config, DB volumes, backups, and scans out of the app image build context.

Local certificate note:

- `ssl/local/cert.pem` and `ssl/local/key.pem` are localhost-only development files for `localhost`, `127.0.0.1`, `::1`, and `kai-app.local`.
- On this workstation, `ssl/local/cert.pem` was added to the Current User `Trusted Root Certification Authorities` store so the in-app browser can load `https://localhost:8443` without `ERR_CERT_AUTHORITY_INVALID`.
- `ssl/local/` is ignored by `.gitignore`; do not promote this certificate to production.

## Current Template Sources

Updated on 2026-05-20:

- Draft generation uses one Word template only: `static/templates/HER_KAI.docx`.
- The old draft split between `iptamenoi.docx` and `loipoi.docx` is no longer used by `app.py`, and those legacy templates are not part of the active repo.
- Exam purpose options are stored in the `exam_option` database table with `option_type='purpose'`.
- Exam category options are stored in the `exam_option` database table with `option_type='category'`.
- `exam_options.py` is the seed/default source used to populate missing DB entries on app boot.
- Admin users can edit the lists at `/manage_exam_options`.
- `static/templates/skopos.doc` and `static/templates/category.doc` are no longer runtime dependencies. They may remain as source/reference files, but the app can run if those two files are removed.
- Other files in `static/templates` are still functional application assets and were not removed from runtime use.
- `templates/results.html` receives the dynamic `categories` and `purposes` lists from `search_results()`.

Stop:

```powershell
docker compose -f docker-compose.local.yml down
```

The helper batch files do the same:

- `START_REPLICA_LOCAL.bat`
- `STOP_REPLICA_LOCAL.bat`

## Remote Run

For a production-like remote host, use:

```bash
docker compose -f docker-compose.remote.yml up -d --build
```

For the recommended tag-based production flow, use [docs/WORKFLOW.md](docs/WORKFLOW.md):

```bash
./scripts/deploy_production.sh kai-vYYYY-MM-DD-name
```

Remote assumptions:

- The host owns port 443.
- `ssl/cert.pem` and `ssl/key.pem` are the intended certificate pair for that host.
- `.env` contains the real runtime secrets.
- `guacamole_config/user-mapping.xml` is reviewed for the target HIS/RDP endpoint.
- Production data is restored intentionally, not copied accidentally from this replica.
- The remote profile intentionally mounts the original `nginx.conf` and uses the Dockerfile Gunicorn command.

## Verification Snapshot

Verified on 2026-05-20:

- `py -3 -m py_compile app.py`: passed.
- `docker compose -f docker-compose.local.yml config --quiet`: passed.
- `docker compose -f docker-compose.remote.yml config --quiet`: passed.
- `HER_KAI.docx` loaded successfully with `DocxTemplate`.
- The `exam_option` table was created and seeded.
- The DB provides 10 active exam purpose options.
- The DB provides 20 active exam category options.
- `templates/results.html` rendered with dynamic purpose/category options, including `ΚΑΤΗΓΟΡΙΑ 31ΜΕΕΔ`.
- `templates/manage_exam_options.html` rendered with add/update/delete/reseed actions.
- Route-level smoke test for `/manage_exam_options`, add option, update option, and delete option passed; the temporary test option was removed.
- Local containers started successfully; PostgreSQL reported healthy.
- The rebuilt app image does not contain `.env`, `ssl`, `guacamole_config`, or `runtime`; required secrets are available as runtime environment variables.
- The in-app browser loaded `https://localhost:8443/login?next=%2F` successfully after trusting the local certificate.
- Real HTTPS smoke via `https://localhost:8443`:
  - GET `/login`: 200 with CSRF token.
  - POST `/login`: 302 to `/`.
  - GET `/`: 200.
  - GET `/pending_exams`: 200.
  - GET `/guacamole/`: 200.

## Important Safety Notes

- Do not run state-changing production tests without an agreed test record.
- Do not commit `.env`, SSL private keys, Guacamole credentials, database volumes, backups, or uploaded scans.
- Some GET routes create audit entries, including manual/appointments/download routes.
- Backup operations are managed from `/manage_backups`; create and verify actions use POST/CSRF and write `backup_record` rows with status, size, SHA-256, and verification details.
- `/force_backup` is retained only as a legacy compatibility route that redirects admins back to `/manage_backups`.

## Known Technical Risks To Keep In Mind

- `db.create_all()` runs on import. It has a retry/rollback guard for transient multi-worker schema races, but real schema migrations still need care.
- APScheduler starts in `app.py`; with multiple Gunicorn workers, multiple scheduler instances are possible.
- There are no migrations in the current project.
- FHIR `fullUrl` values are hardcoded to `http://localhost` in the current app code.
