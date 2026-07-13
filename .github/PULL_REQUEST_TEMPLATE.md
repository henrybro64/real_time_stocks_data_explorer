## Summary

<!-- What changed and why (1–3 bullets). -->

## Test plan

- [ ] Relevant unit/integration tests added or updated
- [ ] Local or CI `backend-pytest` / frontend suites covering the change are green

## Taiwan market-data backend (if applicable)

Use when touching institutional, 資券, Taiwan refresh/store, or shared Taiwan helpers.

Follow [docs/taiwan-market-data-sonar-checklist.md](docs/taiwan-market-data-sonar-checklist.md):

- [ ] SonarCloud PR dashboard reviewed; failed conditions listed
- [ ] Shared Taiwan helpers reused (`parse_taiwan_number`, `shares_to_lots`, history-scan helpers) — no local `/1000` or copied calendar walks
- [ ] New branches covered (success / empty / malformed / error)
- [ ] Cheap Sonar findings fixed in this PR, **or** deferred findings documented with dashboard link + rationale
- [ ] SonarCloud Code Analysis check is green after the latest push
