# PDF Proof Support Implementation Plan

**Goal:** Accept PDF receipts through the visual Pix/Wise flow, as approved in chat after explaining original retention and page rendering.
**Architecture:** Keep the same Maya and financial owner. Download PDFs via the current media reader; render all pages with PDFium to PNG in an isolated, time-bounded subprocess. Each page retains its pixel hash plus original PDF hash, page number/count and source event. No OCR agent or new financial authority.
**Tech Stack:** Python, Pillow, pypdfium2==5.13.0. reportlab/pypdf only for synthetic test fixtures.

## Constraints
- Existing candidate worktree and source base 9b5540c; preserve preexisting ACTIVE.md changes.
- No deploy, live SQLite writes, provider effects, ManyChat sends or V3 access.
- All pages or none: maximum four images/pages per batch; 4 MiB original and 6 MiB encoded aggregate. PDF pages rendered at 144 dpi; max 16 megapixels per page. Password-required, corrupt, empty or oversized PDFs are explicitly unavailable, never partially accepted.
- Original PDF saved by hash in the same evidence archive; comparison records carry original and complete page hashes. Transaction identity remains the global deduplication key.

## Qualification result

- RED verified: valid PDFs originally unavailable; generic-binary PDF also failed before its correction.
- Full isolated suite: 2,623 passed + 2,958 subtests (one Starlette/httpx deprecation warning). Runtime unchanged since this run.
- Final focused regression: 113 passed, including an additional PDF turn-executor case added after full-suite collection.
- Immutable lab image: 90 tests passed; runtime imports asserted under `/app` in the pytest interpreter; no network or runtime-module mount. `pip check` clean.
- Real model in the image: 3/3 scenarios (completed multipage Pix, completed scanned multipage Wise, scheduled multipage Pix), random pixel-only identities, original binding, local order and image replay deduplication verified. No actual settlement or ManyChat send.
- Initial real probe retained: synthetic recipient conflicted with production-account literals in the lab prompt. Fixed only laboratory context, replayed identical Pix PDF; runtime and validation unchanged.
- Laboratory image: `sha256:1a497816a61fb3c0f4e2d93b30eba04995798040ec5374cf7a13689ee860ae5e`, derived from verified TEST base plus frozen runtime sources and PDF/test dependencies. Not a promoted release image.
- Evidence root: `/home/ubuntu/workspace/v2-pdf-727d3625/`. Local code delivery only, no activation/deployment.

## Execution (inline, no subagents)
1. RED: create `tests/test_v2_pdf_proofs.py` using actual PDF fixtures (text, raster, multiple pages, encryption, malformed and page limits). Exercise `ProofMediaReader.extract`, `_request_wire`, `_closed_request`, proposal parsing and `VisualProofService.accept`. Verify current reader returns unavailable for a valid PDF.
2. GREEN: add `v2_adapters/pdf_render.py`, extend `proof_media.py`, `ModelAttachment` and request group validation, and persist document provenance in `visual_proofs.py`. Keep non-PDF attachment representation compatible. Add pypdfium2 to Dockerfile.v2 and runtime extras.
3. Test incomplete page groups, wrong hash/event, rendering failure/timeouts and aggregate limits. Exercise PDF then image/reexport replay without another payment command and with human review pending.
4. Build an immutable lab image from the pinned dependency image and frozen candidate sources. Verify imports/dependencies and execute PDF tests inside the image without a runtime source overlay.
5. With isolated model auth only, send synthetic PDF pages through the actual adapter/child/parser; identifiers appear only in pixels. Prove Pix/Wise completed and scheduled negative, record mixed composition honestly. No real provider effects.
6. Run affected regression and full isolated suite; check boundaries/diff. Update PDF runbook, commit only scoped paths, remove temporary lab auth, reverify runtime authority. Record source/image identity and raw reports outside Git.

Test command prefix: `env -i PATH=/usr/bin:/bin HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 HERMES_LEADS_AGENT_CONFIG_PATH=/tmp/v2-no-live-config /home/ubuntu/workspace/v2-pix-wise-727d3625/venv/bin/python -m pytest`.
