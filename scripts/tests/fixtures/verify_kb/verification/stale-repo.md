---
schema: verify-profile/1
name: stale-repo
remotes:
  - github.com/example-org/stale-repo
default_base: main
---

# stale-repo verification profile

A deliberately stale profile: QC-001 requires a file that the test repo does
not contain, so `checks` must report PROFILE-STALE instead of FAIL.

## Quick checks

### QC-001 — repo-local check script
- Argv: sh tools/check.sh
- When: always
- Requires-path: tools/check.sh

## CI

- Workflow: CI
- Required-jobs: test
