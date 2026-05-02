# Q4 Review Checklist — Skills with High Static but Delta=0

Generated: 2026-03-15
Source: skills-ecosystem-eval full-analysis.json (616 ablation runs)

These 10 skills scored well on static quality but showed delta=0 in ablation testing.
They need human review to decide: keep, retire, or merge into existing rules.

## Likely Retire (model already knows — both with/without = 100%)

| # | Skill | With | Without | Rationale |
|---|-------|------|---------|-----------|
| 1 | `python-list-multiplication-shared-refs` | 100% | 100% | Python basic knowledge, model nails it without skill |
| 2 | `ts-module-scope-lazy-evaluation` | 100% | 100% | TypeScript pattern, model already knows |
| 3 | `fastapi-pydantic-union-type-python39` | 100% | 100% | Python 3.9 specific, version is EOL |

## Need Review (overlap or unclear value)

| # | Skill | Review Question |
|---|-------|-----------------|
| 4 | `playwright-automation-security-hardening` | Overlaps with security-reviewer agent? |
| 5 | `playwright-mcp-csr-content-extraction` | Overlaps with webfetch-blocklist.md rule? |
| 6 | `immutability-dict-unpacking` | Already covered by CLAUDE.md immutability preference? |
| 7 | `content-update-stale-value-grep` | Generic enough to be useful? Or too narrow? |
| 8 | `cross-repo-api-contract-alignment` | Useful for multi-repo work or too project-specific? |
| 9 | `sequential-to-content-addressed-id-migration` | Reusable pattern or one-time migration artifact? |
| 10 | `systematic-cross-reference-migration` | Generic rename workflow or too specific? |

## Review Process

For each skill:
1. Run `/learn-eval-deep <skill-name>` to get full 3-system assessment
2. Check if the knowledge is covered by existing CLAUDE.md rules or ECC agents
3. Decide: **Keep** (unique value) / **Retire** (redundant) / **Merge** (fold into existing rule)

## Decision Log

| Skill | Decision | Date | Reason |
|-------|----------|------|--------|
| | | | |
