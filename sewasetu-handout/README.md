# Sewa Setu — Old Age Pension Portal

Handover copy. Vendor maintenance contract ended 31/01/2026.

## Run with Neon (Windows)

1. Install and start Docker Desktop. Wait until its engine is running.
2. In PowerShell, from this directory, create your local environment file:

```powershell
Copy-Item .env.example .env
```

In `.env`, paste your Neon **pooled connection string** into `DATABASE_URL`.
The legacy variable name `POSTGRES_URL` is also accepted. Also fill
`SEWASETU_ADMIN_USERNAME`, `SEWASETU_ADMIN_PASSWORD`, and `SEWASETU_SECRET_KEY`.
Use a unique, long admin password and generate a Flask secret key with
`python -c "import secrets; print(secrets.token_urlsafe(48))"`. Keep `.env`
private; it is ignored by Git. Never put a real connection string or password
in `.env.example`, source files, or chat.

3. Build and start the app, scheduler, and SMS gateway. The local PostgreSQL
service is not started in Neon mode:

```powershell
docker compose up --build -d
docker compose ps
```

4. Create the portal tables in the selected Neon database:

```powershell
docker compose exec app python /app/scripts/init_database.py
```

The initializer runs `CREATE TABLE IF NOT EXISTS` from `app/schema.sql`; it does
not import the provided citizen seed or modify existing rows. For an existing
Neon schema, check it against `app/schema.sql` before starting the app.

- Portal: http://localhost:8000
- Database readiness check: http://localhost:8000/health
- SMS gateway console (simulated telecom): http://localhost:8000/__gateway/ —
  "sent" OTPs appear here once delivered. The gateway runs on the internal
  network and is served through the portal, so only one port is exposed.

The nightly scheduler uses the same Neon connection. Neon must be reachable
from the machine running Docker; use the Neon pooled URL and keep its password
private. Docker checks `/live` without connecting to Neon, so routine container
health checks do not keep a scale-to-zero Neon compute awake. Use `/health`
when you want to check database readiness.

## Aadhaar identity integration scaffold (disabled)

The portal does not currently collect or verify Aadhaar. The database schema
includes an inactive `identity_verifications` table for a future integration
with an authorized provider. It is designed to hold only the provider name, a
provider-approved pseudonymous subject reference, the linked application, and
the verification timestamp. A reference can be linked to only one application,
and a provider reference cannot be reused for another application.

Do not add Aadhaar numbers, Aadhaar OTPs, or provider authentication payloads
to the application or database. Real verification and duplicate blocking must
not be enabled until the authorized provider, official sandbox documentation,
and provider-approved duplicate-check reference are confirmed. The existing
mobile-OTP flow remains unchanged and is not Aadhaar verification. Applying
`app/scripts/init_database.py` creates the new table without modifying citizen
records.

## Optional AI assistant

The portal includes an English-language assistant for general pension-scheme
and application-process questions. It uses an OpenAI-compatible chat-completions
endpoint and is disabled until configured. Add the provider's HTTPS endpoint,
API key, and model to `.env`:

```dotenv
SEWASETU_AI_API_URL=https://your-provider.example/v1/chat/completions
SEWASETU_AI_API_KEY=your-private-provider-key
SEWASETU_AI_MODEL=your-model-name
```

Use the endpoint, key, and model values supplied by your AI provider. Never
commit or share the API key. Rebuild/restart the app after changing `.env`:
`docker compose up --build -d`. User questions are sent to that provider to
generate answers; do not enter personal details, OTPs, passwords, or bank
information. The assistant cannot access applications or make official
eligibility decisions, and it directs questions outside the published scheme
guidance to the nearest Block Development Office.

## Optional local PostgreSQL

To use the bundled local database, leave both URL variables blank in `.env`,
set `POSTGRES_PASSWORD` to a local password, and run:

```powershell
docker compose --profile local up --build -d
```

The local database initializes from the supplied schema and seed on first boot.
Neon mode does **not** load `seed/seed.sql`; do not import that production
dataset into Neon without authorization and an approved migration plan.

View logs and stop the services:

```powershell
docker compose logs -f
docker compose down
```

The local PostgreSQL data directory is a persistent Docker volume. The schema
and seed are initialized only when that volume is first created; restarting the
stack does not reload or alter existing citizen records. Do not remove the
`pgdata` volume if you need to preserve its records.

## Features

- Mobile OTP verification, multi-step pension application, and age-proof upload.
- Applicant status login, application status, and acknowledgment PDF.
- Staff dashboard, paginated application list, application review, and
  approve/reject actions with protected age-proof downloads.
- Nightly statutory deemed-approval job with SMS dispatch result logging.
- CAPTCHA verification, server-side form validation, CSRF protection, private
  citizen records, and environment-configured credentials.
- Simulated SMS gateway, production log analysis, database readiness check, and
  regression tests.

Set `SEWASETU_UNICODE_FONT` if running outside the provided container and the
default Noto Sans Devanagari font is installed in a different location. The
container installs the font required to render Devanagari names in PDFs.

## Tests and log analysis

Install `app/requirements.txt` and `pytest` in a Python 3.11 environment, then
run:

```powershell
python -m pytest tests -q
python scripts/analyze_logs.py logs/sewasetu-logs-jan-jun-2026.tar.gz
```

The fellowship brief and supplied production evidence are in `BRIEF.md`,
`dc-briefing.mp3`, and `logs/`. Do not modify the seed or log archive.

## Host deployment

`app/deploy/sewasetu.service` is an example systemd unit for deployments outside
Docker. Configure the environment file at `/etc/sewasetu/sewasetu.env`, provide
the same secret and database settings used by the application, and provision
the PostgreSQL database and SMS gateway separately before enabling the unit.
