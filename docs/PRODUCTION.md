# Relay CRM — Production deployment runbook

Status: **prepared, not yet deployed.** Everything below was validated locally (Docker Compose,
the production nginx image, WebSocket upgrades through the edge, and the database checker against
local PostgreSQL). Supabase, Cloudflare and the production host require your accounts and have
**not** been verified end to end. Follow §9 to verify them.

---

## 1. Architecture

Cloudflare and Supabase do not run application code. Relay CRM needs one always-on Linux host
(VM or dedicated server, e.g. 4 vCPU / 8 GB RAM / 80 GB SSD; WPPConnect runs one Chromium per
WhatsApp session, ~300–500 MB each) running Docker Compose:

```
Customer / admin browser
        │  HTTPS + WSS
        ▼
Cloudflare  (DNS, TLS, WAF, rate limits, caching rules)
        │  Cloudflare Tunnel (outbound-only from the host; no open inbound ports)
        ▼
┌──────────────────────── Application host (Docker network "crm", private) ─────────────────┐
│ cloudflared ──► frontend (nginx :8080) ── /api, /ws ──► backend (Daphne :8000)             │
│                   static SPA, widget.js               │        ▲                          │
│                                                        │        │ webhook (internal only)  │
│ celery (worker + beat) ◄── redis (broker, cache) ──────┘   wppconnect (Chromium sessions)  │
│ volumes: redis_data, wpp_tokens, wpp_userdata (WhatsApp sessions — back up, never delete)   │
└──────────────────────────────────────┬───────────────────────────────────────────────────────┘
                                       │ TLS (sslmode=require / verify-full)
                                       ▼
                          Supabase PostgreSQL (managed, per environment)
```

| Component | Runs on | Public? |
|---|---|---|
| React SPA + widget.js | `frontend` (nginx, `frontend/Dockerfile.prod`) | via tunnel only |
| Django/Daphne API + WebSockets | `backend` | only through nginx `/api`, `/ws` |
| Celery worker + beat | `celery` | no |
| Redis | `redis` | no |
| WPPConnect + WhatsApp session storage | `wppconnect` + volumes `wpp_tokens`, `wpp_userdata` | no |
| PostgreSQL | Supabase | no app-facing public access besides Supabase's TLS endpoint |
| DNS / TLS / WAF | Cloudflare | edge |

`/internal/wppconnect/` (the WhatsApp webhook) is blocked at nginx; WPPConnect calls the backend
directly on the Docker network. No service in `docker-compose.prod.yml` publishes a host port.

Local development is unchanged: `docker compose up` (Vite dev server, local PostgreSQL).

---

## 2. Supabase PostgreSQL

### 2.1 Environments
Create **separate Supabase projects** for development, staging and production. Never point
staging or a laptop at the production project.

### 2.2 Choosing the connection endpoint
| Endpoint | Port | Use for Relay CRM? |
|---|---|---|
| Direct connection `db.<ref>.supabase.co` | 5432 | IPv6 only unless the IPv4 add-on is enabled. Fine for an IPv6-capable VM. |
| **Session pooler** `aws-0-<region>.pooler.supabase.com` | 5432 | **Recommended**: IPv4, full PostgreSQL session semantics, suits a long-running Daphne + Celery host. Set `DB_POOL_MODE=session`. |
| Transaction pooler | 6543 | Only for many short-lived processes. Set `DB_POOL_MODE=transaction` (auto-detected for port 6543): server-side cursors are disabled. Django's psycopg3 backend binds parameters client-side, so no prepared statements are created. |

Settings (`backend/config/database.py`): `DATABASE_URL`, `DB_SSLMODE` (default `require`; production
refuses `disable/allow/prefer`), `DB_SSLROOTCERT` (for `verify-full`), `DB_CONN_MAX_AGE` (default 0,
recommended under ASGI), `DB_CONNECT_TIMEOUT` (default 5 s), `DB_POOL_MODE`. URL-encode special
characters in the password. Credentials live only in `.env.production` on the server.

Concurrency budget: Daphne + 2 Celery processes + beat use well under 20 connections with
`CONN_MAX_AGE=0`; Supabase's pooler limits depend on your plan.

### 2.3 Validate before any cutover (read-only)
```
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm backend python manage.py check_database --concurrency 10
```
Reports host, TLS (must be **yes**), server version, unapplied migrations, and opens N concurrent
connections. It never writes data or prints credentials.

### 2.4 Migrating existing data (only with explicit approval)
Customer records are **not** migrated automatically. When approved:
1. **Freeze writes**: `docker compose stop celery backend` (WPPConnect keeps sessions; queued webhooks retry).
2. **Back up local data** (keep it until production is verified):
   `docker compose exec postgres pg_dump -U crm -d whatsapp_crm -Fc -f /tmp/relay.dump`
   then `docker compose cp postgres:/tmp/relay.dump ./backups/relay-$(date +%F).dump`.
   Verify: `pg_restore --list backups/relay-*.dump | head`.
3. **Restore into staging first**: `pg_restore --no-owner --no-privileges -d "$STAGING_DATABASE_URL" backups/relay-*.dump`,
   run `check_database`, start the stack against staging and compare row counts
   (conversations, messages, contacts, leads, follow-ups) with the source.
4. Repeat into production only after staging is verified.
5. Enable Supabase backups (daily; PITR on paid plans) before going live.

**Rollback:** point `DATABASE_URL` back to the previous database (or remove it to use the local
PostgreSQL profile: `--profile local-db`), then `docker compose ... up -d backend celery`.
The local PostgreSQL volume is never deleted by this process.

---

## 3. Cloudflare

1. Add the domain to Cloudflare (DNS proxied, orange cloud).
2. **Zero Trust → Networks → Tunnels → Create tunnel** (cloudflared). Copy the token into
   `CLOUDFLARE_TUNNEL_TOKEN`. Public hostname: `crm.example.com` → `http://frontend:8080`.
3. SSL/TLS: **Always Use HTTPS = on**, **Minimum TLS 1.2**, HSTS is sent by Django
   (`SECURE_HSTS_SECONDS`) — enable preload only after confirming every subdomain is HTTPS.
4. **Network → WebSockets = on** (needed for `/ws/workspaces/...` live updates and `/ws/guest/...`).
5. **Caching → Cache Rules**: *Bypass cache* when URI path starts with `/api/` or `/ws/` or
   `/internal/`. (The origin also sends `Cache-Control: no-store, private` and `Vary: Cookie`.)
   `/assets/*` may be cached (immutable fingerprints); `/widget.js` has a 5-minute public cache.
6. **Security → WAF**: enable Cloudflare Managed Rules. Rate limiting rules (examples):
   - `/api/auth/login/` — 10 requests / minute / IP → block 10 min.
   - `/api/guest/*` POST — 60 / minute / IP (Django additionally throttles 120/min).
   - Block `/internal/*` and `/api/health/infrastructure/` at the edge (defence in depth).
   Do **not** enable "Bot Fight Mode" challenges on `/api/guest/*` or `/widget.js`: the website
   widget is called by script from customer sites and cannot solve challenges.
7. Origin trust: Django honours `CF-Connecting-IP` only from the private proxy networks
   (`TRUSTED_PROXY_NETWORKS`), so throttling uses the real visitor IP and cannot be spoofed;
   `X-Forwarded-For` from clients is ignored for rate limiting (`NUM_PROXIES=0`).
8. Website widget: each workspace's allowed origins (exact `https://` origins) are enforced by the
   guest API; Cloudflare does not change that.

---

## 4. Required production environment variables
See `.env.production.example` (template only). Minimum: `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`, `REDIS_PASSWORD`, `WPPCONNECT_SECRET_KEY`,
`WPPCONNECT_WEBHOOK_SECRET`, `CLOUDFLARE_TUNNEL_TOKEN`, `HEALTH_CHECK_TOKEN`.
Store the file outside source control with `chmod 600`. Rotate secrets on staff changes.

---

## 5. Deploy, update and rollback

**First deploy** (on the host, from a tagged release checkout):
```
cp .env.production.example .env.production   # fill in
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml config >/dev/null
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml run --rm backend python manage.py check_database
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
**Update:** back up (Supabase backup + `docker run --rm -v whatsapp-ai-crm_wpp_userdata:/d -v $PWD/backups:/b alpine tar czf /b/wpp_userdata-$(date +%F).tgz -C /d .`, same for `wpp_tokens`),
`git pull` the new tag, then `... up -d --build backend celery frontend` (migrations run first via
the `migrate` service). Avoid restarting `wppconnect` unless its files changed — restarts can
force WhatsApp re-pairing.
**Rollback:** check out the previous tag and run the same `up -d --build`. Migrations in this
release are additive (new tables/columns, backfills that only fill defaults); rolling the code back
leaves the extra columns unused. To reverse schema explicitly:
`manage.py migrate conversations 0016 && manage.py migrate products 0002` (drops follow-ups,
viewing history, CRM status and lead priority — **data loss; back up first**).

---

## 5a. Attachments
- Allowed: JPG/PNG/GIF/WebP, PDF, DOC/DOCX, XLS/XLSX, PPTX, CSV, TXT, up to `ATTACHMENT_MAX_BYTES`
  (10 MB). The extension must match the file's content signature; SVG/HTML/executables and
  macro-enabled Office files are rejected.
- `ATTACHMENT_STORAGE=local` keeps files in the `attachments` volume (back it up with the WhatsApp
  volumes). For Supabase, create a **private** bucket (no public access, no RLS policies granting
  `anon`), set `ATTACHMENT_STORAGE=supabase`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
- Files are never public: members download through `/api/workspaces/<id>/conversations/<id>/attachments/<id>/download/`
  (session auth, workspace check, `no-store`, sandboxed CSP); website visitors only through their
  guest token for their own conversation. Downloads and sends are audited.
- WhatsApp files are sent with WPPConnect `send-file`; website files are delivered in the chat.
  Inbound customer media from WhatsApp is still shown as "[Unsupported message type]".
- Cloudflare: allow request bodies of ~11 MB on the upload path (default plans allow 100 MB).

## 6. Security checklist (implemented)
- Role-based access: admins reassign, view viewing history, manage settings; agents claim, work
  their own or unassigned conversations/leads/follow-ups; every endpoint checks workspace membership
  (cross-tenant requests return 404).
- Cookie JWT (`HttpOnly`, `Secure` outside DEBUG, `SameSite=Lax`), CSRF on cookie-authenticated
  writes, WebSocket origins validated against `CSRF_TRUSTED_ORIGINS`.
- Audit events: claims, assignment, status changes, takeovers, AI resumes, conversions, follow-up
  create/reschedule/complete/reopen, lead updates.
- Logs contain IDs and counts, never message text, phone numbers, tokens or QR data.
- Production refuses `DEBUG`-style wildcards (`ALLOWED_HOSTS=*`) and unencrypted database URLs.
- Destructive operations (guest data deletion, connection removal) remain admin-only with confirmation.

## 7. Monitoring
- `GET /api/health/live/` — process up. `GET /api/health/ready/` — PostgreSQL + Redis (used by Docker).
- `GET /api/health/status/` with `Authorization: Bearer $HEALTH_CHECK_TOKEN` — database TLS,
  Redis, **Celery worker heartbeat** (stale > 90 s = critical; a running container is not proof),
  webhook backlog/stuck/failed events, failed automatic replies in the last hour, unknown/queued
  sends, WhatsApp sessions expected vs connected. Point an uptime monitor at it (alert on 503 or
  `status != ok`).
- Celery beat runs `monitor_health` every 60 s: logs `health.critical|degraded` and, if
  `ALERT_WEBHOOK_URL` is set, posts state changes (counts only) to Slack/Teams.
- Failed replies and delivery errors are also shown in the inbox and WhatsApp AI settings.

## 8. WhatsApp reliability
Implemented: per-session Chromium profiles, stale-lock cleanup at boot, serialized browser
launches, waiting for a previous browser to release a profile, 180 s QR window, one bounded
automatic restore when a paired session drops (never after logout or manual disconnect), QR
polling directly from the provider while pairing, specific pairing errors, HMAC-checked webhook
credentials, stable de-duplication keys (including messages without a serialized ID), ordered
event processing with bounded retries, and outbound delivery tracking that never auto-resends an
ambiguous send. The pairing changes in `infrastructure/wppconnect/privacy.js` and `init: true`
take effect only after `docker compose up -d wppconnect` (recreates the container; sessions are
kept in volumes but WhatsApp may ask to re-pair).

**WPPConnect is unofficial.** It automates WhatsApp Web and can break when WhatsApp changes its
web client, and accounts using unofficial automation risk restriction. For a commercial service,
plan a migration to the **official WhatsApp Business Platform (Cloud API)**: Meta-hosted, webhook
based (fits the existing `WebhookEvent` → Celery pipeline), no browser sessions, official delivery
receipts, but requires a verified Business account, approved message templates to start
conversations outside the 24-hour customer-service window, and per-conversation pricing. A
`provider` field already exists on `Connection`, so a Cloud API provider can be added alongside
WPPConnect without changing the inbox, chatbot or CRM workflow.

## 9. End-to-end verification after deployment
1. `curl -s https://crm.example.com/api/health/live/` → `ok`; status endpoint with token → `ok`.
2. Browser devtools on the admin app: the `/ws/workspaces/...` request returns **101** and the
   header shows "Live updates".
3. `curl -sI https://crm.example.com/api/workspaces/` → `cache-control: no-store, private`,
   `cf-cache-status: BYPASS` or `DYNAMIC`.
4. `curl -s -o /dev/null -w '%{http_code}' https://crm.example.com/internal/wppconnect/` → 404.
5. Pair WhatsApp; Phone B sends "Hi" → menu reply; open the chat (viewing recorded, AI still on),
   Claim → your name in the header, Status → Converted → customer in Leads, Follow-up icon →
   save notes for a date → entry on that date in Follow-ups.
6. Website: publish the widget for your exact `https://` origin, chat "Hi" → menu, then claim,
   convert and schedule a follow-up from the same inbox.

## 10. CRM workflow semantics
- **Viewing** records `ConversationView` (one row per member; repeat opens within 5 minutes are not
  counted) and never changes owner, status or reply mode.
- **Claim** sets the owner (unassigned → Open) and keeps AI replies running. Admins can reassign.
- **Human takeover** switches the conversation to human replies and cancels pending automatic
  sends; **Resume AI** turns automation back on and keeps the owner.
- **Status** (Unassigned, Open, Unanswered, Not Interested, Converted) is a separate field from the
  owner and from open/resolved. **Converted** creates or updates the conversation's single lead
  (one lead per conversation, enforced by a unique key) with status Converted, the owner and the
  product from the chatbot context. Moving away from Converted **keeps** the lead; the lead's own
  status is then managed on the Leads page. Converting again re-marks the same lead Converted.
- **Follow-ups** belong to a workspace, conversation and contact (and the lead when one exists).
  Done follow-ups are kept; reopening returns them to Pending; every change is audited.
  "Overdue" uses the viewer's local date.
