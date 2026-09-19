# V2 — local release preparation

Base: `d63985830b7aa2254b07dda428494dcfbc7778cc`. Authorization: Carlos's “Siga” after the release-preparation proposal. No production/isolated-contact/Ops cutover, active SQLite edit, real channel delivery, booking, payment or V3 work.

## Historical failures: cause and closure

The pre-change reproduction returned 7 failures, 34 passes and 17 subtests. Six were Phase 7 release assertions incorrectly applied to later V2 metadata/code; the seventh expected the superseded Phase 8 index/blocked Slice 0 wording. Dockerfile.v2 is the actual current runtime package, not build_phase7_wheel.py.

The six historical selectors now execute their original, unmodified assertions from the authenticated Phase 7 closeout `93682024b4867d3e313324339a7060d5351dcd3d`, tree `b779e35c671f3050d056c6ef3c8c0700f5b13f35`. They build twice, authenticate archive RECORDs, install the wheel with pip and import outside the checkout; manifest/checksums and terminal evidence validator are exercised on their own source. Git history is mandatory; absent history, missing selector or historical failure fails rather than skipping. This does not certify the current V2 image. All other domain/behavior tests remain on current source.

The Phase 8 index test now checks the real fast-track heading and ACTIVE authority. Only its ACTIVE-RECONCILED current-state checksum/size/lines in the quarantine manifest changed; rejected/pre-quarantine identities remain unchanged. No Phase 7 manifest was rewritten to retroactively claim that old CI tested new code.

The seven CI deselections were removed. New checks cover no regression exclusions, Docker coverage for every declared runtime package and required configuration asset, and propagation of a failed historical test. Historical module code is executed in a separate stdlib venv, with pip bootstrapped offline via ensurepip.

## Executed evidence

- Baseline: `92-release-baseline.txt`, exit 1: seven expected failures.
- RED: `93-release-red.txt`, exit 1: CI exclusions and missing history helper, with current package check already passing.
- Preserved intermediate runner errors: `94-release-focused.txt` (/tmp noexec; current quarantine checksum), `95-release-focused.txt` (pytest/unittest module-prefix mismatch; checksum). Fixed by symlinked venv interpreter, explicit tests namespace and rebinding only the edited test's current identity.
- GREEN: `96-release-focused.txt`, exit 0: 56 tests, 17 subtests, including handoff.
- Full regression: `97-release-full.txt`, exit 0: **2283 passed, 2958 subtests passed**, no skip/deselection; two third-party deprecation warnings.
- Ruff 0.15.10 on changed tests/helper: passed. `git diff --check`: passed. `scripts/check_fasttrack_boundaries.py`: OK.
- Runtime source/Docker/config files are byte-identical to the base of this preparation increment. This is not a conversation or commercial correction.

Logs live outside Git under `/home/ubuntu/workspace/v2-simplificacao-atendimento-727d3625/`.

```json
{
  "92-release-baseline.txt": "f64351c2e14913b063626256ce8910c407e9d566c7923a404a09023c76e31974",
  "93-release-red.txt": "a0a3870ba28da1bcf28201ae40eeb6a7ea8f604e2321c037c864f434d98a0266",
  "94-release-focused.txt": "899cf7a253f31822f3a26034ac64f785b2688a86cb63c2b2fb9987c9b35a664a",
  "95-release-focused.txt": "a5bd6cfd80aea30d8950f95d09cbddbb1b29e942b890e8da6bad90b3c9dc1ae3",
  "96-release-focused.txt": "7eef6f1217be2ad3173cd9c36d65a0b4927dd6ebf840bb70ef0cf9fbbbe50382",
  "97-release-full.txt": "ded2b13b3cb96d5ce29e5b93e77868076c014bf96be13f7c414e1a8c6d050473"
}
```

## Decision and remaining gate

GO to build and exercise one local image of this candidate, with disposable state, no credentials and network denied. NO-GO for deployed readiness, real-model qualification, live ManyChat handoff or promotion based solely on this suite. No remote CI execution is claimed.
