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

Initial suite-only decision was GO to an offline image build, not deployment. The additional bounded image/model evidence below now closes local preparation; live channel qualification and production promotion remain NO-GO. No remote CI execution is claimed.

## Immutable image and offline execution

- Source: `a7864e23015e74821d082eaad6c48dd9097148cd`, tree `3e550c482f394c0a235c233f1af7bb75e1563928`.
- Image: `sha256:7815b4880763015fd912e5787ad609cd36b1f889db4b623f8ac7f77c1096f630`. Built from `git archive` of that commit. Local image ID only; RepoDigests is empty, so this is not a registry digest/publication.
- Authenticated 169 source/config bytes from the image; runtime imports rooted in `/app` in the same interpreter as pytest, no mounted runtime checkout/config.
- `pip check`: passed. Image subset: **68 passed**, covering production composition, inbox reliability/migration, rejection continuity and blocking-error handoff. Test-only packaging constrained to the image's required `26.0`.
- Ordinary API entrypoint started under UID 10001, rootfs read-only, no external network, no real credentials, disposable state and no host-published port. `/readyz` returned HTTP 200, role `api`, all real-effect gates false. This is API-only readiness, not productive worker/channel readiness.
- Synthetic inbox compatibility: previous isolated-test image `sha256:f2ebda37b365afde8b290c4fe0a3bde2889750ed06b48aa1204be45549074aea` created state; candidate reopened/migrated and accepted another event; previous image reopened again, recognized both duplicates and accepted a third event. All remained pending. Temporary state removed. This is not a production rollback or a full operational DB downgrade qualification.
- Preserved setup diagnostics: non-root QA file permissions, unconstrained test-only packaging, fixed image ENTRYPOINT, inbox enum acceptance and no persistent `close()` method. Final qualification runs use corrected harnesses, not product-code changes.

## Real-model bounded smoke in the same image

- Real configured `openai-codex/gpt-5.6-terra`, high effort, actual structured child/parser and productive turn executor. Every real child witnessed zero tools and zero valid tool names before inference.
- Four real calls, three customer turns, two independent scenarios. Lodging port simulated; no business provider HTTP clients, reservation/payment/delivery/handoff workers, operational DB mounts or real customer IDs. Parent network mechanically denied; only the child model could use network. Private copied auth was removed and operational auth stayed byte-identical.
- Query for lodging: final reply exactly reproduced the simulated dates, 2 adults, no children, one available unit and BRL 450 total. Follow-up declining reservation/payment was honored.
- Deliberately injected invalid selection: the real Maya received the rejection and authored an informational request for dates and party, without another action or fabricated controller prose.
- Final public replies equalled accepted model chunks. Boundary commands **0**, relays **0**, undelivered public rows **3**, delivery receipts **0**, external business effects **0**.
- End-to-end scenario-turn latencies: **19.173s**, **10.037s**, **9.127s**; these are smoke observations, not an SLA or statistical stability qualification.
- Model lab ran under non-root UID 1001 because the unprivileged host could not chown a disposable auth copy; ordinary API/image tests independently ran under configured UID 10001. No production-UID real-model qualification is claimed.
- Preserved preliminary roots: r1 stopped at the image entrypoint before model calls; r2 used a nonnumeric synthetic subscriber and failed authority after two successful real proposals; r3 committed a model reply but the harness decoded the public envelope incorrectly. r4 corrects those fixtures/decoding and passed. These setup failures remain evidence and are not reclassified as product passes.
- Manual review passed for the bounded smoke only, bound to the raw result hash. One run does not prove broad conversational reliability, real-provider availability, channel delivery or human receipt of a handoff.

## Closure and next gate

`verify_release_evidence.py` passed and wrote `107-release-seal.json`, binding raw logs, runners, image result, compatibility result and manual review by SHA-256. Temporary test containers/auth/state were removed; the pre-existing local registry was not touched. Live runtime authority remains independently verified and no runtime was cut over.

Next: explicitly authorized isolated ManyChat handoff test, observe the actual operator queue/assumption, then separately authorize publication/promotion of the exact qualified artifact. Real reservations/payments remain a separate gate.
