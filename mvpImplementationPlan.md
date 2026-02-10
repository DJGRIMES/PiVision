# PiVision MVP Implementation Plan (Simplified)

This plan intentionally keeps scope small so we can start building now and harden later.

## What we are deciding right now

1. Keep decisions simple and document unknowns.
2. Build stubs where details are not final yet.
3. Center events around **person interaction count**, with optional notes.
4. Implement enough structure now to support future expansion.
5. Track edge cases as future tests, not blockers.
6. (Reserved for future decisions.)
7. Prefer the simplest storage + URI approach.
8. Minimal observability: disk space + basic process health.
9. Multi-device fairness can wait.
10. Keep event data long-term.

---

## MVP decisions locked in

### A) Event model (immediate)
- Primary event for MVP: `interaction_detected`.
- Event represents a person interaction opportunity (countable interaction event).
- Event can include operator notes such as:
  - `added_items`
  - `took_items`
  - freeform comment
- Keep event records indefinitely.

### B) Stubbing strategy (immediate)
We will implement placeholders for the parts we can defer:
- Auth details: basic device-key check first; advanced HMAC/replay protection later.
- Queue policy: basic DB queue semantics now; advanced retry tuning later.
- Event dedupe and merge windows: TODO.
- Fine-grained retention exceptions: TODO.
- Multi-device fairness and rate limits: TODO.

### C) Simplified storage strategy (immediate)
- Store image files on local filesystem.
- Keep `storage_uri` as a local file URI.
- Use one canonical file layout and avoid storage abstraction complexity for MVP.

### D) Observability (immediate)
Bare minimum metrics/dashboard:
- Remaining disk space (primary)
- Ingest success/failure counts (basic)
- Worker queue depth (basic)

---

## Build phases

### Phase 1 — Build now
- Postgres schema + migrations.
- Ingest endpoint writes capture + staging file.
- Worker stub marks captures processed and emits basic interaction events.
- Admin API reads event timeline and notes.

### Phase 2 — Soon after MVP works
- Better event confidence/tuning detail schema.
- Better retry/backoff behavior.
- Add cleanup job with stronger idempotency/error handling.
- Add tests for known edge cases.

### Phase 3 — Later
- Multi-device scheduling/fairness.
- Rich analytics and optional long-horizon capture pruning.
- Optional object storage backend.

---

## Explicit future test targets (not blockers now)
- Partial write failures between DB and filesystem.
- Duplicate ingest retries with same `device_seq`.
- Missing files referenced by DB rows.
- Cleanup job safety on repeated runs (idempotency).
- Queue worker crashes while processing jobs.

