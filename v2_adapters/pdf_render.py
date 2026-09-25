"""Bounded PDFium rasterization. No OCR, model calls or financial interpretation."""

import base64
import io
import json
import math
import subprocess
import sys

MAX_PAGES = 4
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_ENCODED_BYTES = 6 * 1024 * 1024
MAX_PAGE_PIXELS = 16_000_000


def render_pdf(raw: bytes) -> tuple[bytes, ...]:
    """All pages or an error; native parser cannot hang/crash the worker."""
    if not raw.startswith(b"%PDF-") or len(raw) > MAX_INPUT_BYTES:
        raise ValueError("pdf_invalid")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "v2_adapters.pdf_render"],
            input=raw,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("pdf_render_timeout") from exc
    if result.returncode or len(result.stdout) > MAX_ENCODED_BYTES + 1024:
        raise ValueError("pdf_render_failed")
    try:
        encoded = json.loads(result.stdout)
        if type(encoded) is not list or not 1 <= len(encoded) <= MAX_PAGES:
            raise ValueError("pdf_invalid_pages")
        return tuple(base64.b64decode(page, validate=True) for page in encoded)
    except (ValueError, TypeError) as exc:
        raise ValueError("pdf_render_failed") from exc


def _render(raw):
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(raw)
    try:
        if not 1 <= len(document) <= MAX_PAGES:
            raise ValueError("pdf_page_limit")
        pages, budget = [], MAX_ENCODED_BYTES
        for index in range(len(document)):
            page = document[index]
            try:
                width, height = page.get_size()
                if not (math.isfinite(width) and math.isfinite(height)):
                    raise ValueError("pdf_page_dimensions")
                if (
                    min(width, height) <= 0
                    or math.ceil(width * 2) * math.ceil(height * 2) > MAX_PAGE_PIXELS
                ):
                    raise ValueError("pdf_page_dimensions")
                bitmap = page.render(scale=2)  # 144 dpi, including raster-only scans.
                try:
                    image = bitmap.to_pil()
                    try:
                        out = io.BytesIO()
                        image.save(out, "PNG")
                    finally:
                        image.close()
                finally:
                    bitmap.close()
                encoded = base64.b64encode(out.getvalue()).decode("ascii")
                budget -= len(encoded) + len("data:image/png;base64,")
                if budget < 0:
                    raise ValueError("pdf_render_size")
                pages.append(encoded)
            finally:
                page.close()
        return pages
    finally:
        document.close()


def _main():
    # These limits apply only to this short-lived renderer, never the host worker.
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES or not raw.startswith(b"%PDF-"):
        raise ValueError("pdf_invalid")
    sys.stdout.write(json.dumps(_render(raw)))


if __name__ == "__main__":
    try:
        _main()
    except Exception:
        # No document data in parser diagnostics; caller gets an unavailable attachment.
        raise SystemExit(1)
