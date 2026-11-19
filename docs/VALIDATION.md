# Phase 2 validation

Date: 2026-11-17. The previous disk-space blocker was resolved before this phase began. The initial audit confirmed healthy Phase 1 services and all seven original tests passing.

## Verified

- `docker compose config --quiet`: passed.
- `docker compose up --build -d --wait --wait-timeout 600`: passed.
- All six long-running services healthy; migration service exited 0. Existing volumes preserved.
- Original Phase 1 tests: 7 passing.
- Phase 2 tests: 32 passing (39 combined), including auth/CSRF/logout revocation, roles, workspace isolation, SQL relationship guards, provider failures, QR transitions, webhook auth/deduplication, ordered persistence, outbound idempotency, bounded retries, ambiguous-send recovery, delivery receipts and WebSocket isolation.
- `makemigrations --check --dry-run`: no missing migrations.
- Health endpoints: HTTP 200.
- Real Celery broker → worker → result backend and Channels Redis round trip: passed after backend/worker restart.
- Installed WPPConnect token generation and status endpoint: passed against real container, without starting or pairing an account.
- Live callback authentication: correct Basic credentials reached payload validation; missing credentials rejected with 401.
- React production build: passed. Runtime dependency audit: zero known npm advisories.

Mocked provider calls are used in tests that exercise pairing states and send outcomes. The real provider smoke test does not contact customer accounts.

## Unverified acceptance criteria

- Scanning a QR code on a real phone and detecting successful pairing.
- Receiving a real customer message and sending a real reply through WhatsApp.
- Delivery/read receipts on a real account.
- Real-device session recovery and persisted customer message history after container recreation.
- Visual/responsive browser behavior: the available browser tool could not initialize because its sandbox metadata was unavailable. No browser screenshots or end-to-end UI interaction are claimed.
- Production load/latency and failure testing, external penetration testing, independent image/dependency provenance review.

No default accounts, synthetic customer conversations, or simulated provider success states are shipped. This is the Phase 2 baseline validation record; later website-chat and AI workflow implementation is described in WEBSITE_CHAT.md. See PHASE2.md for the real-device acceptance checklist and limitations.

## Product chatbot implementation check

Date: 2026-11-18. This is an implementation checkpoint, not a Phase 3 completion or production-readiness claim.

### Passed

- Docker Compose reports all six long-running services healthy; the migration job exited successfully. Existing volumes were preserved.
- Django system check and migration consistency check passed after the final schema changes.
- Updated verification script: 7 health tests and 82 application tests passed. The application suite includes product permissions, tenant-scoped products and knowledge, uploads, visitor chat without WhatsApp, product comparisons and ambiguity, AI-disabled behavior, draft approval, takeover cancellation, sender attribution, prompt-injection boundaries, and existing CRM regressions.
- The migration test database applied the full migration graph, including product tenant guards and message sender backfill.
- Automated guest-chat checks verified optional email/phone encryption, staff-only visibility, and visitor removal. The additive email migration was applied to the existing database.
- Health endpoints, Celery worker/result backend, Channels Redis, WPPConnect token/status, and private webhook authentication passed their automated smoke checks. The provider check did not start a WhatsApp session.
- React production build passed.
- Additive migrations were applied to the existing database. Backend and worker were restarted; the service stack returned healthy.

### Unverified or still required

- No real WhatsApp device was paired. Real QR scan, customer message, reply, delivery receipt, and session recovery remain manual Phase 2 checks from PHASE2.md.
- The embeddable widget was exercised by automated API tests, not installed on an external website or manually tested in a browser.
- External AI calls were mocked in automated tests. Configure a server-side API key/model and verify the selected provider separately before enabling external processing for a workspace.
- Product image references use administrator-supplied HTTPS URLs; image uploads and non-text document formats are not supported. Product notes accept UTF-8 `.txt` and `.md` up to 20 KB.
- Production security, load, backup/restore, and external penetration checks remain outstanding.

The product chatbot implementation is in progress. Do not treat this checkpoint as final Phase 3 acceptance.
