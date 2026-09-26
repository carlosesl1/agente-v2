"""Bounded image/PDF GET for the same Maya child. No bank/model/effect APIs."""

import base64
import hashlib
import io
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from v2_contracts.model import ModelAttachment, AttachmentContentStatus as Status


def retain_bytes(root, data):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = hashlib.sha256(data).hexdigest()
    path = root / digest
    try:
        with path.open("xb") as stream:
            path.chmod(0o600)
            stream.write(data)
            stream.flush()
            import os

            os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_bytes() != data:
            raise RuntimeError("retained proof content hash mismatch")
    return digest


class ProofMediaReader:
    def __init__(self, *, allowed_hosts, archive, client=None):
        if not allowed_hosts:
            raise ValueError("explicit media hosts required")
        self.hosts, self.archive, self.client = (
            tuple(allowed_hosts),
            Path(archive),
            client,
        )

    def _allowed_url(self, value):
        try:
            url = urlsplit(value)
            return (
                url.scheme == "https"
                and url.hostname in self.hosts
                and not url.username
                and not url.password
                and url.port in (None, 443)
                and not url.fragment
            )
        except ValueError:
            return False

    def _sources(self, events):
        """Transport discovery only: retain original text, URL bytes and event ID."""
        for event in events:
            seen = set()
            if event.media_url is not None:
                seen.add(event.media_url)
                yield event, event.media_url, event.media_type
            # ManyChat may put the attachment URL in the text field. Do not infer
            # financial meaning or file type from prose/extensions; HTTP owns MIME.
            for token in (getattr(event, "text", "") or "").split():
                if token not in seen and self._allowed_url(token):
                    seen.add(token)
                    yield event, token, None

    def extract(self, events):
        client = self.client or httpx.Client(
            trust_env=False, timeout=10, follow_redirects=False
        )
        result = []
        budget = 6 * 1024 * 1024
        try:
            for event, media_url, media_type in self._sources(events):
                digest, document_digest, mime = None, None, media_type
                try:
                    if not self._allowed_url(media_url):
                        raise ValueError("media_url_rejected")
                    image_count = sum(a.image_data_url is not None for a in result)
                    if image_count >= 4:
                        raise ValueError("media_count_exceeded")
                    with client.stream(
                        "GET", media_url, follow_redirects=False
                    ) as response:
                        response.raise_for_status()
                        mime = (
                            response.headers.get("content-type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        if mime not in (
                            "image/png",
                            "image/jpeg",
                            "image/webp",
                            "application/pdf",
                            "application/octet-stream",
                        ):
                            raise ValueError("image_required")
                        raw = bytearray()
                        for chunk in response.iter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 4 * 1024 * 1024:
                                raise ValueError("media_too_large")
                    if mime == "application/octet-stream":
                        if not raw.startswith(b"%PDF-"):
                            raise ValueError("media_invalid")
                        mime = "application/pdf"
                    if mime == "application/pdf":
                        from v2_adapters.pdf_render import render_pdf

                        document_digest = retain_bytes(self.archive, bytes(raw))
                        pages = render_pdf(bytes(raw))
                        if image_count + len(pages) > 4:
                            raise ValueError("media_count_exceeded")
                        data_urls = [
                            "data:image/png;base64," + base64.b64encode(page).decode()
                            for page in pages
                        ]
                        size = sum(len(data) for data in data_urls)
                        if size > budget:
                            raise ValueError("media_too_large")
                        rendered = tuple(
                            ModelAttachment(
                                mime,
                                Status.IMAGE_READY,
                                event.event_id,
                                retain_bytes(self.archive, page),
                                data,
                                document_sha256=document_digest,
                                page_number=number,
                                page_count=len(pages),
                            )
                            for number, (page, data) in enumerate(
                                zip(pages, data_urls), 1
                            )
                        )
                        result.extend(rendered)
                        budget -= size
                        continue
                    from PIL import Image

                    with Image.open(io.BytesIO(raw)) as image:
                        if (
                            image.width * image.height > 16_000_000
                            or Image.MIME.get(image.format) != mime
                        ):
                            raise ValueError("media_invalid")
                        image.verify()
                    data = "data:" + mime + ";base64," + base64.b64encode(raw).decode()
                    if len(data) > budget:
                        raise ValueError("media_too_large")
                    budget -= len(data)
                    digest = retain_bytes(self.archive, bytes(raw))
                    result.append(
                        ModelAttachment(
                            mime, Status.IMAGE_READY, event.event_id, digest, data
                        )
                    )
                    continue
                except (ValueError, OSError, ImportError, httpx.HTTPError):
                    result.append(
                        ModelAttachment(
                            mime or None,
                            Status.UNAVAILABLE,
                            event.event_id,
                            digest,
                            error_code="proof_pdf_unavailable"
                            if mime == "application/pdf"
                            else "proof_image_unavailable",
                            document_sha256=document_digest,
                        )
                    )
            return tuple(result)
        finally:
            if self.client is None:
                client.close()
