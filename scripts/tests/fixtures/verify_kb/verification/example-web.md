---
schema: verify-profile/1
name: example-web
remotes:
  - github.com/example-org/example-web
default_base: main
---

# example-web verification profile

## Quick checks

### QC-001 — lint
- Argv: npm run lint
- When: always

## Paths

- UI: src/components/**
- Tests: tests/**

## CI

- Workflow: ci.yml
- Required-jobs: test

## Flows

### FL-001 — smoke
- When: ui
- Use: open the page and check it renders

## Checklist

### EX-001 — example item
- Trigger-paths: src/**
- Check: confirm the change renders in a browser
- Evidence: manual
- Incident: example incident
- Added: 2026-01-01

## Verifier notes

Example free-form notes for the verifier.
