# Server-side PDF URL reading — implementation plan

**Authorization:** Carlos approved URL → server download → page images → same Maya, asking to make it work. Implement inline, no subagents. TEST only; payment, reservation and unrelated delivery effects remain closed.
**Architecture:** reuse ProofMediaReader and the existing turn executor. Discover attachment URLs from current event text using URL parsing and exact configured media hosts, not document names or payment keywords. Preserve original text, event identity and signed query bytes. Explicit media fields still work. Configure media reading independently from payment-proof settlement. No alternate model, OCR interpreter or second financial owner.
**Isolation:** worktree `/home/ubuntu/agente-v2/.worktrees/pdf-url-727d3625`, branch `fix/v2-pdf-url-727d3625`; base is verified TEST authority. Existing worktrees and historical ACTIVE documents untouched.

## Task 1 — URL reader and read-only composition
- [ ] Add failing `tests/test_v2_pdf_url_media.py` tests for URL-only and caption URLs, signed query preservation, duplicate URL in text/media, untrusted hosts, redirects/broken PDFs, whole-batch page budget and immutable original message/event.
- [ ] Reuse downloader/rendering by deriving per-event media references in `v2_adapters/proof_media.py`. No network calls at webhook admission, no URL mutation or new semantic regex rules.
- [ ] Test actual `_build_inbox_worker` with `proof_media_hosts` configured and all effect gates closed; instantiate reader without VisualProofService in `v2_host/production.py`. Require explicit valid hosts in settings. Keep financial prerequisites unchanged.
- [ ] Run focal tests, boundaries and generated manifest check; integration test native relay normalization → actual executor → model wire images → commit → replay.

## Task 2 — qualification and TEST delivery
- [ ] Use existing hosted technical PDF and a fresh opaque pixel-only challenge for real-model reading. Prove reply contains challenge unavailable in prompt, two pages attached, and replay causes no new model call or effect.
- [ ] Run one integrated clean regression. Freeze source; build immutable image and attest source bytes.
- [ ] Prepare TEST identity/config-only successor, exact backups/rollback, reader host configuration; leave financial gates closed. Publish source, image and authority only after readiness/queue gates pass.
- [ ] Send URL in a normal prefixed WhatsApp text to test native ManyChat → V2 → download → Maya. Preserve final committed reply and terminal event, no financial commands.
- [ ] Report this as URL-message integration, not proof that ManyChat now emits document-only events. Do not install polling of `last_input_text` as a supposedly lossless message feed: it exposes only a mutable latest value, not durable attachment history. File-only notifications upstream remain a distinct integration limit unless directly verified.

## Acceptance
The server automatically recognizes an allowed current-message URL, GETs and renders it, passes actual images to Maya, commits her genuine reply once, and preserves original text and document/page identities. Unknown hosts are never fetched. Download/parser failures surface unavailable attachments, never an approved payment. Reading works with financial capabilities closed. No GA/Ops/V3 or live SQL edits.
