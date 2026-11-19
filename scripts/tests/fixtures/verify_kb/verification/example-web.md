---
schema: verify-profile/1
name: example-web
remotes:
  - github.com/example-org/example-web
default_base: main
---

# example-web verification profile

## Quick checks

### QC-001 — whitespace errors
- Argv: git diff --check
- When: always
- Timeout: 60
- Required: true

## Paths

- UI: src/components/**
- Tests: tests/**

## CI

- List-pattern: ^(test_\w+) \(
- Workflow: CI
- Jobs: unit-tests, quality
- Required-jobs: unit-tests, quality

## Flows

### FL-001 — smoke
- When: ui
- Use: open the changed page and check it renders
- Notes: fictional example flow

## Checklist

### EX-001 — end-to-end suite touched
- Trigger-paths: e2e/**
- Check: the end-to-end suite still runs locally
- Evidence: manual
- Incident: example-org incident 201
- Added: 2026-01-01

## Verifier notes

Example free-form notes for the verifier.
