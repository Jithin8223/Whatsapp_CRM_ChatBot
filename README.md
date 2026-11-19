# Product Chat CRM

A Django + React multi-tenant CRM with a product-aware website assistant, customer chat widget, shared human inbox, and optional WhatsApp integration. Website chat and product answers do not require WhatsApp pairing.

**Current status:** Phase 3 product chatbot implementation is in progress. Automated tests cover the existing CRM and product, guest-chat, retrieval, and assisted-reply paths. Phase 2 real-device WhatsApp pairing and live messaging remain unverified; see [validation results](docs/VALIDATION.md).

## Run on Windows PowerShell

Start Docker Desktop with WSL 2 and Linux containers, then:

```powershell
Set-Location 'C:\Users\jithi\Desktop\WhatsApp AI CRM'
powershell -ExecutionPolicy Bypass -File .\scripts\Initialize.ps1
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 600
docker compose ps --all
powershell -ExecutionPolicy Bypass -File .\scripts\Verify.ps1
Start-Process 'http://localhost:5173'
```

Initialization preserves existing credentials and adds the new webhook secret if missing. Keep `.env` private. No existing volumes need to be deleted. On first startup, the one-shot `migrate` service must exit with code 0; all six long-running services should be healthy.

Register through the browser to create your account and business workspace. There are no default passwords or seeded customer records. Team administrators can create new agent accounts in Settings. Sessions last eight hours; sign in again after expiration.

## Use the CRM

- **Overview:** workspace counts, recent conversations, connection health and sends needing review.
- **WhatsApp accounts:** administrators create a named connection, start pairing, view its QR, reconnect, disconnect, or remove it. Open WhatsApp → Linked devices → Link a device to scan. Pairing is confirmed only by provider status. Up to five active connection records per workspace are allowed.
- **Conversations:** search and filter the shared inbox, read paginated message history, assign an agent, close/reopen a conversation and send text replies. Agents can assign conversations to themselves; administrators can assign any active member. Messages identify customers, AI, administrators and agents.
- **Products and leads:** administrators manage products, prices, availability, features, specifications, warranties, HTTPS image URLs, FAQs and bounded text/Markdown notes. Website enquiries appear as leads and link to the same conversation history.
- **Website chatbot:** publish an origin-restricted widget and choose AI automatic, AI draft approval, or human handling per conversation. AI answers use only the selected product facts and FAQs; unclear product names trigger clarification. Human takeover cancels unapproved drafts.
- **Contacts:** search the directory, add a direct WhatsApp identifier (for example `919876543210@c.us`), edit display names and open a conversation on a chosen connection. Incoming direct messages create contacts automatically. LID identifiers are preserved without inventing a phone number.
- **Settings:** rename the workspace, create another isolated workspace and manage admin/agent membership. The last administrator cannot be removed or demoted.
- **Infrastructure:** the original Phase 1 status page remains at `/infrastructure` for local diagnostics.

If delivery is **unknown**, an administrator must check the actual WhatsApp conversation and mark it as sent or not sent. Resolving an unknown send never resends it. Later queued messages in that conversation wait for resolution. If an earlier connection attempt failed before submission, the worker retries at most three times. A send interrupted after submission is never retried automatically.

## How it works

Django stores workspaces and memberships; every business API resolves membership before querying workspace data. PostgreSQL triggers reject cross-workspace relationships and changes to record ownership. The custom user from Phase 1 is reused. API errors do not return provider responses or database exception details.

JWT authentication uses an HTTP-only cookie and a revocable server-side session record. CSRF protection covers login, registration and API mutations. WebSockets use that cookie and exact trusted origins; membership and expiration are checked before every event and on a periodic heartbeat. Only the server chooses the tenant channel group. Redis notifications contain invalidation IDs, while the dashboard reloads authorized data through REST. Reconnects and a 15-second polling fallback recover missed updates.

Webhook receipt authenticates before parsing, validates and normalizes supported payloads, and durably saves encrypted events before returning HTTP 202. Celery drains events in receipt order and deduplicates by connection and provider message ID. A periodic recovery task scans persisted work after broker failures. Delivery acknowledgements are separate records and can arrive before send confirmation. The outbound queue claims each send durably before calling the provider.

The Celery worker includes one embedded Beat scheduler for recovery and connection checks. Keep exactly one worker service running with `--beat`; scaling workers requires separating Beat first. This adds no new microservice for local development.

The adapter interface in `backend/apps/whatsapp/providers.py` separates provider calls from CRM logic. Evolution/Baileys can be added behind this interface later. WPPConnect endpoints and response envelopes were checked against the installed pinned 2.10.81 code. See [provider contract and API reference](docs/PHASE2.md).

## Project map

```text
backend/
  config/                 Django settings, ASGI routes, health checks, safe errors
  apps/accounts/          Custom user, revocable JWT sessions, auth APIs and tests
  apps/tenants/           Businesses, memberships, permissions, audit events
  apps/whatsapp/          Provider adapter, encrypted credentials, operation outbox
  apps/conversations/     Contacts, conversations, messages, webhook inbox/outbox
  apps/notifications/     Tenant WebSocket consumer and notifications
  apps/products/          Tenant products, FAQs, uploaded notes and leads
  apps/ai_engine/         Workspace settings, approved knowledge and reply jobs
  apps/conversations/     Shared WhatsApp and website inbox, guest sessions and messages
  scripts/                Infrastructure and installed-provider smoke checks
frontend/src/
  pages/                  Auth, dashboard, connections, inbox, contacts, settings
  api.js                  Axios + CSRF handling
  auth.js                 Zustand authentication/workspace state
  hooks.js                REST loading, actions and WebSocket reconnection
  Infrastructure.jsx      Preserved Phase 1 page
infrastructure/wppconnect/privacy.js
                          Supported provider initServer configuration and private logging
scripts/                  Windows initialization and verification
```

## Verify and troubleshoot

```powershell
Invoke-RestMethod http://localhost:8000/api/health/live/
Invoke-RestMethod http://localhost:8000/api/health/ready/
Invoke-RestMethod http://localhost:8000/api/health/infrastructure/
docker compose exec -T backend python manage.py test config apps.accounts apps.whatsapp apps.conversations apps.notifications --verbosity 2
docker compose exec -T backend python manage.py makemigrations --check --dry-run
docker compose exec -T backend python scripts/verify_infrastructure.py
docker compose exec -T backend python scripts/verify_provider_contract.py
docker compose exec -T frontend npm run build
docker compose logs --tail 80 backend celery migrate
```

The provider contract smoke check verifies real token generation/status and callback authentication without starting a WhatsApp session or printing secrets. Automated messaging tests mock external sends. Passing these checks does not establish live delivery.

Django/Daphne does not reload automatically. After Python changes, use `docker compose restart backend celery`. Changes to requirements need an image rebuild. Frontend code hot-reloads; after dependency changes rebuild its image and synchronize the node_modules volume with `docker compose run --rm --no-deps frontend npm ci`.

If ports conflict, change BACKEND_PORT or FRONTEND_PORT and CSRF_TRUSTED_ORIGINS in `.env`, then recreate the containers. Trusted origins include the full scheme, hostname and port and are also used for WebSocket origin checks. Internal Docker service ports stay fixed.

Only Django (8000) and Vite (5173) bind to localhost. Database, Redis and WPPConnect have no host ports. Do not forward `/internal/` through a public reverse proxy. Production HTTPS, an ingress rate limiter, monitoring, backup/restore, retention, independent provider image review and stronger secret management still need deployment work; see [security notes](docs/SECURITY.md).

If Docker reports read-only storage or snapshot I/O errors, check free space on the drive holding its disk image. Make sufficient space and restart Docker Desktop at an appropriate time; that restart can affect other projects. Never delete Docker's virtual disk to repair this project.

## Restart and stop safely

```powershell
docker compose restart backend celery wppconnect
docker compose up -d --wait --wait-timeout 180
powershell -ExecutionPolicy Bypass -File .\scripts\Verify.ps1
docker compose down
```

Named volumes retain PostgreSQL data, Redis AOF and provider sessions/browser profiles across container recreation. `down` keeps these volumes. Do not use `down -v` unless you intend to erase them. Removing one connection through the UI logs out only that connection and retains CRM history.

See [website chat setup](docs/WEBSITE_CHAT.md) for widget installation, AI provider setup, products and human handoff. The [real-device checklist](docs/PHASE2.md#real-device-acceptance-checklist) is still required to accept Phase 2 WhatsApp behavior; automated tests do not replace it.
