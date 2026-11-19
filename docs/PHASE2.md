# Phase 2 API and provider contract

All workspace resources live under `/api/workspaces/{business_uuid}/`. Registration creates the first workspace with an admin membership. A member can access only resources owned by that workspace. Invalid or foreign resource identifiers return 404. No platform-superuser bypass is implemented for tenant APIs.

## Authentication

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/auth/csrf/` | Set CSRF cookie; return masked token |
| POST | `/api/auth/register/` | username, email, password, business_name |
| POST | `/api/auth/login/` | username, password |
| POST | `/api/auth/logout/` | Revoke current session and clear cookie |
| GET | `/api/auth/me/` | User and authorized workspaces |
| POST | `/api/workspaces/` | Create workspace using name |

Send the masked token as `X-CSRFToken` on mutations, including login/registration; browsers send the HTTP-only JWT cookie automatically. Tokens are never returned to browser JavaScript or stored in localStorage. Only the selected workspace ID is stored in localStorage. JWT sessions expire after eight hours and cannot be refreshed silently. Login and registration are throttled to ten attempts/minute/IP. Production needs an ingress limiter as well; DRF cache-based throttling is not a strict distributed abuse-control boundary.

## Workspace APIs

Paths below are relative to the workspace prefix.

| Method | Path | Access and behavior |
|---|---|---|
| GET, PATCH | `/` | Member read; admin rename |
| GET, POST | `team/` | Member list; admin creates a new account and membership |
| PATCH, DELETE | `team/{id}/` | Admin changes role/removes membership; last admin protected |
| GET | `dashboard/` | Counts scoped to workspace |
| GET, POST | `connections/` | Member list; admin create with name |
| GET | `connections/{id}/` | Member reads sanitized status |
| POST | `connections/{id}/start/` | Admin queues initialization |
| POST | `connections/{id}/reconnect/` | Admin queues start for existing persistent session |
| POST | `connections/{id}/disconnect/` | Admin closes browser session, retains authentication |
| POST | `connections/{id}/remove/` | Admin logs out, removes provider session data, soft-removes connection; retains CRM history |
| GET | `connections/{id}/qr/` | Admin-only QR data URI with expiry; no-store |
| GET, POST | `contacts/` | Member search/list/create direct contacts |
| GET, PATCH | `contacts/{id}/` | Details; update display_name |
| GET, POST | `conversations/` | List/search; create/open by contact and connection UUIDs |
| GET, PATCH | `conversations/{id}/` | Read; update status or assigned_agent user ID |
| POST | `conversations/{id}/read/` | Reset shared unread count |
| GET, POST | `conversations/{id}/messages/` | Paginated newest-first history; enqueue content and UUID client_id |
| POST | `conversations/{id}/resolve-send/` | Admin: message_id + outcome confirmed_sent/confirmed_not_sent |

List contacts/conversations with `?search=...&page=1`. Conversations additionally support `status=open|closed` and `contact={uuid}`. Page size is 50. Message pages are newest-first; the UI reverses each window for display. Unread counts are shared across the team, not individual read receipts. Contact details show up to the first page of associated conversations; use the inbox API filters for additional pages.

Connection mutations are throttled to 30/minute/user. General authenticated APIs use the existing 600/minute/user limit. All connection operations are durable records. An admin must explicitly retry a failed lifecycle operation; sending a 202 means queued, not paired or delivered.

## Installed WPPConnect 2.10.81 contract

Verified against the running pinned image's `dist/routes/index.js`, `controller/{sessionController,messageController,encryptController}.js`, `util/{functions,createSessionUtil}.js` and middleware. Source references:

- https://github.com/wppconnect-team/wppconnect-server/tree/v2.10.81/src
- https://github.com/wppconnect-team/wppconnect-server/blob/v2.10.81/src/config.ts
- https://github.com/wppconnect-team/wppconnect-server/blob/v2.10.81/docker-compose.yml

| Method | Provider endpoint | Contract used |
|---|---|---|
| POST | `/api/{session}/{secret}/generate-token` | Returns session bearer token; server-to-server only |
| POST | `/api/{session}/start-session` | Bearer; waitQrCode=false; initialization continues asynchronously |
| GET | `/api/{session}/status-session` | Returns status and optional PNG data-URI qrcode |
| POST | `/api/{session}/close-session` | Close without logout |
| POST | `/api/{session}/logout-session` | Logout and remove that session's provider data |
| POST | `/api/{session}/send-message` | phone (full c.us/LID ID), message, isGroup=false, isLid flag; response is a one-element response array containing id |

The QR endpoint `/qrcode-session` also exists but returns PNG bytes. This implementation deliberately uses the QR returned by `status-session` instead, with an application expiry of 45 seconds that is not extended for an unchanged code. Status polling runs every 15 seconds through Celery Beat. QR refresh follows the provider's changing codes; reconnect is available when initialization has stopped. No unsupported delete-session API is invented.

Provider session names are server-generated random identifiers, globally unique and unrelated to customer-supplied names. Session bearer tokens and cached QR codes are Fernet-encrypted in PostgreSQL. The encryption key is purpose-derived from DJANGO_SECRET_KEY; preserve the key with backups, and re-encrypt stored tokens/QRs before changing it. Production should use a separately managed rotatable encryption root/KMS. Server and webhook secrets remain in environment configuration, never in browser responses.

## Private webhook

WPPConnect's sender posts an event/session envelope, merged with event fields. It does not emit a signature header. It accepts only a configured exact webhook URL/allowlisted URL. Compose therefore configures one trusted callback with URL Basic authentication:

`http://wppconnect:<private-secret>@backend:8000/internal/wppconnect/`

Axios turns the URL credentials into an Authorization header. Django checks it with constant-time comparison before parsing JSON and maps the server-generated session to its owner. The callback path contains no secret. Provider secrets are not needed by the browser. The provider launcher suppresses raw upstream log details, QR console output, file logs and media auto-downloads. Health/status APIs and sanitized application errors remain available for diagnostics. Do not bypass this launcher when handling real credentials.

Supported events: onmessage/unreadmessages (direct contacts only), onack, qrcode, status-find and closesession. Outgoing/self events and groups are ignored. Status/QR events schedule an authoritative status read. Acknowledgements retain separate event records and are reconciled monotonically, including when they precede the send response. Media is represented by a visible unsupported-type placeholder; attachments, group chats, broadcast lists, templates and reactions are outside this phase.

The callback has no public proxy route in Vite. Local Django is bound only to loopback. A production reverse proxy must block `/internal/` from external clients. Basic credentials without TLS are acceptable only on this local private bridge; remote deployments need TLS or a protected private network. Shared callback credentials do not provide cryptographic event provenance or replay freshness. Rotate the shared callback secret in both services together.

## Durable work and failure recovery

The incoming inbox is saved before queue publication. Queued outbound messages and connection operations are also committed before broker notification. Beat scans incomplete work every 15 seconds, so a broker outage does not discard already-accepted events. Incoming worker processing is ordered by persisted ingress time per connection; provider timestamps are stored separately. This does not guarantee the provider itself delivers events in WhatsApp chronology.

The outbound worker serializes claims per conversation and commits `sending` before its network call. Only pre-connection failures are retried automatically, at most three attempts. Timeouts after sending, unexpected provider responses and crashes during submission become `unknown`. Later messages wait. Stale `sending` records are held after two minutes. A provider ID is required for automatic successful submission. Client UUIDs prevent duplicate enqueueing when the browser retries an ambiguous HTTP request.

WPPConnect does not supply a verified idempotent send API, so exactly-once delivery is not claimed. The provider itself retries only selected network webhook failures and does not guarantee replay after permanent errors or receiver downtime. Durable processing starts after Django receipt; events never delivered to Django may require reconciliation. Poison events stop automatic retries after five failures and require operator investigation. Add monitoring/retention/dead-letter tooling before production.

## Real-device acceptance checklist

1. Open localhost:5173 and register a workspace. Create a second workspace or account and check that its directory/inbox stays separate.
2. As an admin, create a WhatsApp connection and wait for a QR. Scan it on a test account you control. Confirm the actual provider state becomes connected.
3. From another test phone, send a text. Confirm contact creation, unread count, conversation and message timestamp. Verify another signed-in team member sees the update without reloading.
4. Reply from the inbox. Confirm receipt on the phone and inspect send/delivery/read status as available. Do not equate submitted with delivered.
5. Close/reopen and assign the conversation. Check that an agent cannot change connection settings or access a foreign workspace.
6. Run `docker compose restart backend celery wppconnect`, wait for health, then verify history remains and the persistent provider session reconnects. Also verify records survive `docker compose down` then `up -d --wait` without deleting volumes.
7. Log out in the browser. Confirm its WebSocket stops receiving events, and that the old session cannot fetch API data.

At the Phase 2 baseline, no real account was available, so device-dependent checks were unverified. Website chatbot and AI workflow functionality is documented in [WEBSITE_CHAT.md](WEBSITE_CHAT.md); WhatsApp pairing remains optional for website chat.
