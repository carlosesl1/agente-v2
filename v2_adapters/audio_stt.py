"""xAI REST speech decoding and durable byte-bound reuse; no semantic decisions."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

import httpx

from v2_contracts.model import ModelAttachment, AttachmentContentStatus as Status

MODEL = "grok-voice-transcribe-2.0"
ENDPOINT = "https://api.x.ai/v1/stt"
MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 16_384
AUDIO_MIMES = frozenset({
    "audio/ogg", "application/ogg", "audio/opus", "audio/mpeg", "audio/mp3",
    "audio/wav", "audio/x-wav", "audio/wave", "audio/mp4", "audio/m4a",
    "audio/x-m4a", "audio/aac", "audio/flac", "audio/x-flac", "audio/webm",
    "audio/x-matroska",
})


def audio_mime(raw: bytes) -> str | None:
    """Container signatures only; xAI owns full codec decoding/validation."""
    if len(raw) < 16:
        return None
    if raw.startswith(b"OggS") and (b"OpusHead" in raw[:512] or b"vorbis" in raw[:512]):
        return "audio/ogg"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WAVE":
        return "audio/wav"
    if raw.startswith(b"fLaC"):
        return "audio/flac"
    if raw.startswith(b"ID3") or raw[0] == 255 and raw[1] & 0xE0 == 0xE0:
        return "audio/mpeg" if raw.startswith(b"ID3") or raw[1] & 0x06 else "audio/aac"
    if raw[4:8] == b"ftyp":
        return "audio/mp4"
    if raw.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm"
    return None


def _text(value):
    if (type(value) is not str or not value.strip() or "\x00" in value
            or len(value.encode("utf-8")) > MAX_TRANSCRIPT_BYTES):
        raise ValueError("audio_transcription_unavailable")
    return value  # Preserve provider text exactly, including accents and negation.


def _atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".stt-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def transcript_dialogue(original: str, attachments) -> str:
    parts = [original] if original else []
    for item in attachments:
        if item.content_status is Status.TRANSCRIPT_READY:
            parts.append(f"[Transcrição do áudio {item.source_event_id}]\n{item.transcript_text}")
    return "\n\n".join(parts)


class XaiSpeechToText:
    """Worker-owned credential, bounded multipart, success-only persistent caches."""

    def __init__(self, *, api_key: str, archive, client=None):
        if not api_key or api_key != api_key.strip() or any(c in api_key for c in "\r\n\x00"):
            raise ValueError("xAI STT key required")
        self._api_key, self.archive, self.client = api_key, Path(archive), client
        self.archive.mkdir(parents=True, exist_ok=True, mode=0o700)

    def transcribe(self, raw: bytes, mime: str) -> str:
        if not 0 < len(raw) <= MAX_AUDIO_BYTES or mime not in AUDIO_MIMES or audio_mime(raw) is None:
            raise ValueError("audio_transcription_unavailable")
        digest = hashlib.sha256(raw).hexdigest()
        key = hashlib.sha256((MODEL + ":" + digest).encode()).hexdigest()
        path = self.archive / (key + ".json")
        descriptor = os.open(self.archive / (key + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if path.exists():
                value = json.loads(path.read_text())
                if value.get("audio_sha256") != digest or value.get("model") != MODEL:
                    raise ValueError("audio_transcription_unavailable")
                return _text(value.get("text"))
            client = self.client or httpx.Client(trust_env=False, timeout=30, follow_redirects=False)
            try:
                # httpx serializes data before files, as required by the xAI API.
                with client.stream("POST", ENDPOINT,
                        headers={"Authorization": "Bearer " + self._api_key},
                        data={"model": MODEL, "format": "false"},
                        files={"file": ("audio." + {"audio/ogg":"ogg", "audio/mpeg":"mp3",
                            "audio/wav":"wav", "audio/mp4":"m4a", "audio/flac":"flac",
                            "audio/aac":"aac", "audio/webm":"webm"}.get(mime, "ogg"), raw, mime)},
                        follow_redirects=False) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 1024 * 1024:
                            raise ValueError("audio_transcription_unavailable")
                value = json.loads(body)
                if type(value) is not dict:
                    raise ValueError("audio_transcription_unavailable")
                text = _text(value.get("text"))
                _atomic_json(path, {"model": MODEL, "audio_sha256": digest, "text": text})
                return text
            except (httpx.HTTPError, ValueError, UnicodeError):
                raise ValueError("audio_transcription_unavailable") from None
            finally:
                if self.client is None:
                    client.close()

    def _event_path(self, event_id, url):
        identity = json.dumps([MODEL, event_id, url], ensure_ascii=False, separators=(",", ":"))
        return self.archive / "events" / (hashlib.sha256(identity.encode()).hexdigest() + ".json")

    def cached_event(self, event_id, url):
        path = self._event_path(event_id, url)
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        if value["source_event_id"] != event_id or value["transcription_model"] != MODEL:
            raise ValueError("audio_transcription_unavailable")
        value["content_status"] = Status(value["content_status"])
        return ModelAttachment(**value)

    def bind_event(self, event_id, url, attachment):
        if attachment.source_event_id != event_id or attachment.content_status is not Status.TRANSCRIPT_READY:
            raise ValueError("audio_transcription_unavailable")
        _atomic_json(self._event_path(event_id, url), attachment.to_dict())
