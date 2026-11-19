# Phase 2 security and release review

This is a local development environment, not a completed production deployment. Phase 2 adds authenticated workspace APIs, connection management, a private webhook and tenant WebSockets. The repository also contains website-chat and AI workflow endpoints; workspace AI remains disabled unless an administrator enables it. Public health checks contain no credentials; detailed infrastructure health is disabled when DEBUG is false.

## WPPConnect review (2026-11-17)

The upstream changelog records startup secret collection in 2.10.82 and removal in 2.10.84. Do not use 2.10.82, 2.10.83, nightly or latest. This project selects the preceding 2.10.81 image and pins the registry digest observed during setup:

`sha256:7c882ee934a5663a107b5f5a0382bfd8b1fb95150cd21ab35cd8ff9cea70d1f8`

This is a limited source/configuration review, not proof that the image or its dependencies are free from vulnerabilities. Before pairing real accounts, independently audit the image and dependency tree and review the incident remediation. If any affected image was previously run, treat its environment credentials and sessions as potentially exposed and rotate them.

Relevant earlier fixes include restricted sensitive operations (2.10.70), browser configuration hardening (2.10.71), authenticated Chatwoot webhooks and dependency patches (2.10.75), and private media cache protection (2.10.77). The chosen version includes those changes. Configuration variables, volume paths and `/healthz` were checked against the selected tag's Compose and config source.

Sources:

- https://github.com/wppconnect-team/wppconnect-server/blob/main/CHANGELOG.md
- https://github.com/wppconnect-team/wppconnect-server/releases/tag/v2.10.81
- https://github.com/wppconnect-team/wppconnect-server/blob/v2.10.81/docker-compose.yml
- https://github.com/wppconnect-team/wppconnect-server/blob/v2.10.81/src/config.ts
- https://github.com/wppconnect-team/wppconnect-server/blob/v2.10.81/Dockerfile

WPPConnect receives its own server secret and a dedicated callback secret. No database, Redis or Django secret is injected into it. Manager is disabled and no connector port is published. The authenticated callback is configured only for the internal Django hostname. The shared bridge permits outbound traffic needed by WhatsApp and is not an egress firewall. Its private launcher disables media auto-download, console QR output and raw upstream error/file logging, because error objects can contain callback credentials.

## Implemented application controls

- Revocable HTTP-only JWT cookie sessions with CSRF on all mutations, including login/registration. Cookies are Secure outside DEBUG and SameSite=Lax. Tokens are not exposed to JavaScript.
- Workspace membership checks on API requests and background task lookups; admin/agent permissions; last-admin safeguard and membership-change audit events. No global superuser bypass for tenant APIs.
- PostgreSQL triggers reject foreign workspace relationships, foreign agent assignments and changes to record business ownership. These supplement application-scoped queries; they are not PostgreSQL row-level security or a defense against a compromised application database account.
- WebSocket origin validation, authenticated handshake, server-selected workspace groups, per-event authorization and periodic expiry/revocation checks. Redis events contain invalidation IDs, not message bodies.
- Fernet encryption for provider bearer tokens, cached QR codes, webhook payloads, and voluntarily shared website visitor phone/email. Encryption is purpose-derived from DJANGO_SECRET_KEY; rotating that key requires re-encryption/reconnection. Provider session files remain sensitive data in its private Docker volumes.
- Dedicated private Basic-auth webhook verification before payload parsing; bounded request size; normalized payloads; self-message/group filtering; durable inbox and provider-ID deduplication.
- Durable outbound claims, request UUID idempotency, bounded pre-connection retries, no automatic repeats after ambiguous submission, and audited administrator reconciliation.
- Product, FAQ, document-note and lead queries are scoped to the authorized workspace. PostgreSQL triggers reject mismatched product relationships and immutable ownership changes. Public visitors use the existing origin-bound opaque session token and never receive CRM credentials.
- Product uploads accept only bounded UTF-8 text/Markdown and store text in the database; uploaded content is not served as a file. Product images are administrator-provided HTTPS URLs.
- External AI processing is gated by workspace settings; provider credentials and model IDs remain server-side. Human takeover cancels queued/running jobs and unapproved drafts.
- Cache-backed login and API throttles, no-store API responses, sanitized application exceptions and disabled HTTP client URL logging. Audit rows contain action/object IDs, not secrets or content.

See [PHASE2.md](PHASE2.md) for exact authentication contracts and failure behavior. Real-device security and delivery tests remain unverified.

WPPConnect and Evolution/Baileys provide unofficial connectivity. Protocol changes and account restrictions can interrupt service; ban-free operation cannot be promised.

## Before production (later phase)

Add independent dependency/image provenance review, ingress rate limits, production TLS, managed encryption keys and rotation tooling, backups with restore drills, audit export, retention/deletion policy, poison-event alerting and monitoring. Block `/internal/` on public ingress. Add account recovery, password change/reset, verified email/invitations and MFA before public SaaS onboarding. Audit logs are application records, not tamper-proof external logs. Run Django deployment checks, disable DEBUG, remove bind mounts/Vite, serve built assets and restrict allowed hosts/origins. This Compose file does not implement those production deployment controls.

PostgreSQL now holds the durable webhook inbox and outbound queue; Redis persistence alone is not relied on as an outbox. The provider can still lose events before Django receipt, and ambiguous sends require manual reconciliation. Exactly-once delivery is not promised. Keep one Beat scheduler; separate it before scaling workers. No malicious-traffic stress test, penetration test or latency target is claimed.
