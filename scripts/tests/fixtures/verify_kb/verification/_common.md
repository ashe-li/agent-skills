---
schema: verify-checklist/1
---

# Common checklist

All content is fictional (organization `example-org`). The engine tests build a
diff that adds `migrations/0002_add_index.sql`, adds a `fetch(` call under
`src/api/`, and adds a `console.log(` call in `src/util.ts`. Exactly CM-001,
CM-002 and CM-003 match that diff.

## Checklist

### CM-001 — new migration needs a rollback note
- Trigger-paths: migrations/**
- Trigger-status: A
- Check: a newly added migration documents how to roll it back
- Evidence: manual
- Incident: example-org incident 101; example-org incident 102
- Added: 2026-01-01

### CM-002 — new network call in the API client
- Trigger-paths: src/api/**
- Trigger-added: fetch\(
- Check: the new request handles timeouts and non-2xx responses
- Evidence: npm test -- api
- Incident: example-org incident 103
- Added: 2026-01-01

### CM-003 — debug logging left in code
- Trigger-added: console\.log\(
- Check: no debug logging ships to production
- Evidence: manual
- Incident: example-org incident 104
- Added: 2026-01-01

### CM-004 — deleted migration
- Trigger-paths: migrations/**
- Trigger-status: D
- Check: a deleted migration was never applied anywhere
- Evidence: manual
- Incident: example-org incident 105
- Added: 2026-01-01

### CM-005 — browser storage in the API client
- Trigger-paths: src/api/**
- Trigger-added: localStorage
- Check: no credentials are written to browser storage
- Evidence: manual
- Incident: example-org incident 106
- Added: 2026-01-01

### CM-006 — infrastructure change
- Trigger-paths: infra/**
- Check: the change was planned against a non-production environment first
- Evidence: manual
- Incident: example-org incident 107
- Added: 2026-01-01

### CM-007 — security follow-up marker
- Trigger-added: TODO\(security\)
- Check: every security follow-up has a tracking ticket
- Evidence: manual
- Incident: example-org incident 108
- Added: 2026-01-01

### CM-008 — any source change (retired)
- Trigger-paths: src/**
- Check: retired example; must never be selected
- Evidence: manual
- Incident: example-org incident 109
- Added: 2026-01-01
- Status: retired

### CM-009 — lockfile modified
- Trigger-paths: package-lock.json
- Trigger-status: M
- Check: the lockfile change matches a manifest change
- Evidence: manual
- Incident: example-org incident 110
- Added: 2026-01-01

### CM-010 — generic lockfiles
- Trigger-paths: **/*.lock
- Check: lockfile changes are intentional
- Evidence: manual
- Incident: example-org incident 111
- Added: 2026-01-01
