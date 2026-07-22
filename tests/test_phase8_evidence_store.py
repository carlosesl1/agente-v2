"""Focused durability tests for the Phase 8 evidence object store."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from phase8_release import evidence_store
from phase8_release.evidence_store import EvidenceArtifactStore, ManualReviewError


class EvidenceStoreTests(unittest.TestCase):
    def test_publish_fsyncs_every_s0_s1_s2_directory_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceArtifactStore(Path(directory) / "evidence")
            events: list[str] = []
            real_mkdir = evidence_store.os.mkdir
            real_open = evidence_store.os.open
            real_fsync = evidence_store.os.fsync

            def descriptor_path(descriptor: int) -> str:
                return str(Path(f"/proc/self/fd/{descriptor}").resolve())

            def record_mkdir(path: object, *args: object, **kwargs: object) -> None:
                events.append(f"mkdir:{path}")
                real_mkdir(path, *args, **kwargs)

            def record_open(path: object, *args: object, **kwargs: object) -> int:
                descriptor = real_open(path, *args, **kwargs)
                if path in {"owner.lock", "object.tmp"}:
                    events.append(f"open:{path}")
                return descriptor

            def record_fsync(descriptor: int) -> None:
                path = descriptor_path(descriptor)
                if path.endswith("/.staging"):
                    events.append("fsync:.staging")
                elif path.endswith("/owner.lock"):
                    events.append("fsync:owner.lock")
                elif path.endswith("/object.tmp"):
                    events.append("fsync:object.tmp")
                elif "/.staging/" in path:
                    events.append("fsync:stage")
                real_fsync(descriptor)

            with (
                mock.patch.object(evidence_store.os, "mkdir", side_effect=record_mkdir),
                mock.patch.object(evidence_store.os, "open", side_effect=record_open),
                mock.patch.object(evidence_store.os, "fsync", side_effect=record_fsync),
            ):
                store.publish(b"durable transitions\n")

            expected = (
                "mkdir:",
                "fsync:.staging",
                "open:owner.lock",
                "fsync:owner.lock",
                "fsync:stage",
                "open:object.tmp",
                "fsync:stage",
            )
            cursor = 0
            for event in events:
                if cursor == 0 and event.startswith(expected[cursor]):
                    cursor += 1
                elif cursor < len(expected) and event == expected[cursor]:
                    cursor += 1
            self.assertEqual(cursor, len(expected), events)

    def test_object_tmp_open_failure_releases_owner_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceArtifactStore(Path(directory) / "evidence")
            real_open = evidence_store.os.open

            def fail_object_tmp(path: object, *args: object, **kwargs: object) -> int:
                if path == "object.tmp":
                    raise OSError("injected object.tmp open failure")
                return real_open(path, *args, **kwargs)

            with mock.patch.object(evidence_store.os, "open", side_effect=fail_object_tmp):
                with self.assertRaises(OSError):
                    store.publish(b"must remain recoverable\n")

            report = store.recover()
            self.assertEqual(len(report.removed), 1)
            self.assertEqual(report.retained, ())
            self.assertEqual(tuple((store.root / ".staging").iterdir()), ())

    def test_concurrent_publish_same_bytes_returns_one_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceArtifactStore(Path(directory) / "evidence")
            payload = b"same retained RED output\n"

            with ThreadPoolExecutor(max_workers=8) as executor:
                objects = tuple(executor.map(store.publish, (payload,) * 16))

            self.assertEqual({item.sha256 for item in objects}, {hashlib.sha256(payload).hexdigest()})
            self.assertEqual({item.bytes for item in objects}, {len(payload)})
            self.assertEqual({item.path for item in objects}, {objects[0].path})
            self.assertEqual(objects[0].path.read_bytes(), payload)
            self.assertEqual(objects[0].path.stat().st_mode & 0o777, 0o400)
            self.assertEqual(tuple((store.root / ".staging").iterdir()), ())
            self.assertEqual(len(tuple((store.root / "objects").iterdir())), 1)

    def test_divergent_existing_object_is_manual_review_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceArtifactStore(Path(directory) / "evidence")
            payload = b"authoritative output\n"
            digest = hashlib.sha256(payload).hexdigest()
            object_path = store.root / "objects" / digest
            divergent = b"divergent bytes\n"
            object_path.write_bytes(divergent)
            object_path.chmod(0o400)

            with self.assertRaises(ManualReviewError):
                store.publish(payload)

            self.assertEqual(object_path.read_bytes(), divergent)
            self.assertEqual(object_path.stat().st_mode & 0o777, 0o400)

    def test_restart_recovers_only_s0_s1_s2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceArtifactStore(Path(directory) / "evidence")
            staging = store.root / ".staging"
            valid = {
                "0" * 32: (),
                "1" * 32: ("owner.lock",),
                "2" * 32: ("owner.lock", "object.tmp"),
            }
            for name, members in valid.items():
                stage = staging / name
                stage.mkdir(mode=0o700)
                for member in members:
                    path = stage / member
                    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                    os.close(descriptor)

            report = store.recover()

            self.assertEqual(report.removed, tuple(sorted(valid)))
            self.assertEqual(report.retained, ())
            self.assertEqual(tuple(staging.iterdir()), ())

            invalid = staging / ("3" * 32)
            invalid.mkdir(mode=0o700)
            (invalid / "unexpected").write_bytes(b"must remain")
            with self.assertRaises(ManualReviewError):
                store.recover()
            self.assertEqual((invalid / "unexpected").read_bytes(), b"must remain")
