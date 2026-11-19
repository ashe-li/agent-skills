---
schema: verify-candidate/1
id: CAND-001
status: candidate
proposed_scope: common
created: 2026-01-02
---

# Feature flag removed without cleanup

## Pattern

A feature flag is deleted from the flag registry while code still reads it.

## Proposed trigger

- Trigger-paths: src/flags/**
- Trigger-added: isEnabled\(

## Occurrences

2026-01-02 | example-org/example-web | example-org incident 301 | flag read after removal
2026-01-05 | example-org/example-api | example-org incident 302 | same pattern in the API
