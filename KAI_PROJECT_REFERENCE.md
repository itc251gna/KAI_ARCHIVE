# KAI App V ChatGPT Project Reference

Last updated: 2026-05-20

## Working Boundary

- Active working copy: local KAI App replica folder.
- Protected original local dev copy: legacy KAI App source folder.
- Protected production target: configured production HTTPS host.
- Do not edit the protected original local dev copy or production unless explicitly instructed.

## Local Runtime

- Local HTTPS URL: `https://localhost:8443`
- Local compose file: `docker-compose.local.yml`
- App service: `kai-vchatgpt-app`
- PostgreSQL service: `kai-vchatgpt-db`
- Nginx service: `kai-vchatgpt-nginx`
- Local DB host port: `15432`
- Local HTTPS certificate folder: `ssl/local`

## Current Document Template Rules

- Draft output now uses only `static/templates/HER_KAI.docx`.
- `app.py` no longer selects between `iptamenoi.docx` and `loipoi.docx`.
- Old draft template copies are excluded from the active repo; use Git history or local backup folders only for reference.
- `static/templates/certificate.docx` remains separate and was not changed.

## Exam Option Sources

- Exam purpose (`skopos`) options are stored in the `exam_option` database table with `option_type='purpose'`.
- Exam category (`katigoria`) options are stored in the `exam_option` database table with `option_type='category'`.
- `exam_options.py` contains the default seed lists and is used to populate missing DB entries on app boot.
- Admin users can manage the lists at `/manage_exam_options`.
- `static/templates/skopos.doc` and `static/templates/category.doc` are no longer runtime dependencies. They may remain as reference/source files, but the app can run if those two files are removed.
- Other files in `static/templates` are still functional application assets and should not be removed unless the specific workflow is changed.
- `templates/results.html` renders the dynamic lists passed by `search_results()`.

## Verified Option Counts

- Active purposes from DB: 10
- Active categories from DB: 20
- Verified clean category examples:
  - `ΚΑΤΗΓΟΡΙΑ ΕΙΔΙΚΩΝ ΥΠΗΡΕΣΙΩΝ ΕΕΚ`
  - `ΚΑΤΗΓΟΡΙΑ ΕΑ (ΣΑΕ-RADAR)`
  - `ΚΑΤΗΓΟΡΙΑ 31ΜΕΕΔ`
  - `ΑΛΕΞΙΠΤΩΤΙΣΤΗΣ`

## Verification Snapshot

Verified on 2026-05-20 after the template-source change:

- `py -3 -m py_compile app.py`: passed.
- `docker compose -f docker-compose.local.yml up -d --build kai-app`: rebuilt and restarted the app service.
- `DocxTemplate` opened `/app/static/templates/HER_KAI.docx` successfully.
- The `exam_option` table was created and seeded with 30 rows.
- `templates/results.html` rendered with dynamic purpose/category options from DB.
- `templates/manage_exam_options.html` rendered with add/update/delete/reseed actions.
- Route-level smoke test for `/manage_exam_options`, add option, update option, and delete option passed; the temporary test option was removed.
- `curl.exe -k -I https://localhost:8443/login`: returned `HTTP/1.1 200 OK`.
- In-app browser refreshed `https://localhost:8443/login?next=%2F` and showed the KAI login page.
