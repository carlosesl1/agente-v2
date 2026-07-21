"""Safe local runtime capture and sanitized contract manifests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from scripts.capture_phase7_runtime import (
    CaptureRejected,
    _scan_text,
    _sanitize_allowlisted_test,
    _synthetic_phone,
    build_runtime_contract_manifest,
    capture_runtime,
    source_fingerprint,
)


def run_git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def synthetic_runtime(root: Path) -> tuple[Path, str]:
    source = root / "source"
    source.mkdir()
    run_git(source, "init", "-q", "-b", "main")
    run_git(source, "config", "user.email", "\x70\x68\x61\x73\x65\x37\x40\x65\x78\x61\x6d\x70\x6c\x65\x2e\x69\x6e\x76\x61\x6c\x69\x64")
    run_git(source, "config", "user.name", "Phase 7 Test")
    files = {
        "app.py": "def _process_event(event):\n    return event\n",
        "domain/chapada_native_tools.py": (
            "CHAPADA_COMMIT_STATE_TOOL = 'chapada_commit_state'\n"
            "V2_ONLY_NATIVE_EXECUTABLE_TOOL_NAMES = ('read_tool', 'write_tool')\n"
            "V2_ONLY_NATIVE_TOOL_NAMES = (*V2_ONLY_NATIVE_EXECUTABLE_TOOL_NAMES, CHAPADA_COMMIT_STATE_TOOL)\n"
            "MAYA_VISIBLE_READONLY_NATIVE_TOOL_NAMES = ('read_tool',)\n"
            "MAYA_VISIBLE_WRITE_TOOL_NAMES = ('write_tool',)\n"
            "_NATIVE_TOOL_SCHEMAS = (\n"
            "  {'name': 'read_tool', 'description': 'read', 'parameters': {'type': 'object', 'properties': {'query': {'type': 'string', 'description': 'copy-only detail', 'default': 'example'}, 'description': {'type': 'string', 'description': 'real argument copy'}}}},\n"
            "  {'name': 'write_tool', 'description': 'write', 'parameters': {'type': 'object', 'properties': {}}},\n"
            "  {'name': CHAPADA_COMMIT_STATE_TOOL, 'description': 'state', 'parameters': {'type': 'object', 'properties': {}}},\n"
            ")\n"
        ),
        "domain/tool_executor.py": (
            "CONTACT_SCHEMA = {\n"
            "  'contact_phone': {'format': 'E.164 completo; exemplo \x2b\x35\x35\x31\x31\x30\x30\x30\x30\x30\x30\x30\x30\x30'},\n"
            "}\n"
        ),
        ".env.example": "API_KEY=placeholder\n",
        ".dockerignore": "__pycache__\n",
        "README.md": "synthetic runtime\n",
        "uv.lock": "version = 1\n",
        "tests/test_app_llm_central_webhook.py": (
            "LEAD = {\n"
            "  'id': '1873018537',\n"
            "  'first_name': 'Carlos',\n"
            "  'last_name': 'Eduardo',\n"
            "  'name': 'Carlos Eduardo',\n"
            "  'whatsapp_phone': '\x2b\x35\x35\x37\x35\x39\x39\x39\x39\x39\x32\x39\x33\x39',\n"
            "}\n"
        ),
        "tests/test_bokun_v2_tools.py": (
            "CONTACT = {'whatsapp_phone': '\x2b\x35\x35\x31\x31\x38\x38\x38\x38\x38\x37\x37\x37\x37'}\n"
        ),
        "qa/maya_test_lab/scenarios/__init__.py": (
            "from pathlib import Path\n"
            "BUILTIN_CORE_SUITE = Path(__file__).with_name('core_smoke.json')\n"
            "BUILTIN_REAL_WORLD_SUITE = Path(__file__).with_name('real_world_v1.json')\n"
        ),
        "qa/maya_test_lab/scenarios/core_smoke.json": (
            "{\"schema_version\":1,\"scenarios\":[]}\n"
        ),
        "qa/maya_test_lab/scenarios/real_world_v1.json": (
            "{\"schema_version\":1,\"fixture\":\"tracked-safe-baseline\"}\n"
        ),
    }
    for relative, content in files.items():
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    run_git(source, "add", ".")
    run_git(source, "commit", "-q", "-m", "baseline")
    head = run_git(source, "rev-parse", "HEAD")
    (source / "app.py").write_text(
        "def _process_event(event):\n"
        "    api_key = normalize_runtime_key(event.get('API_KEY', ''))\n"
        "    return {'ok': event, 'configured': bool(api_key)}\n"
    )
    (source / ".env.example").write_text("API_KEY=real-looking-but-excluded\n")
    (source / ".dockerignore").write_text("__pycache__\n*.tmp\n")
    (source / "uv.lock").write_text("version = 2\n")
    (source / "domain/tool_executor.py").write_text(
        "CONTACT_SCHEMA = {\n"
        "  'contact_phone': {'format': 'E.164 completo; exemplo \x2b\x35\x35\x32\x32\x30\x30\x30\x30\x30\x30\x30\x30\x30'},\n"
        "}\n"
    )
    (source / "qa/maya_test_lab/scenarios/real_world_v1.json").write_text(
        "{\"contact_phone\":\"\x2b\x35\x35\x32\x32\x39\x39\x38\x37\x36\x35\x34\x33\x32\"}\n"
    )
    (source / "tests/test_app_llm_central_webhook.py").write_text(
        "LEAD = {\n"
        "  'id': '1873018537',\n"
        "  'first_name': 'Carlos',\n"
        "  'last_name': 'Eduardo',\n"
        "  'name': 'Carlos Eduardo',\n"
        "  'whatsapp_phone': '\x2b\x35\x35\x37\x35\x39\x39\x39\x39\x39\x32\x39\x33\x39',\n"
        "  'changed': True,\n"
        "}\n"
        "PHONE_EVENT = {'phone_number': '\x35\x35\x37\x35\x39\x39\x39\x39\x39\x32\x39\x33\x39'}\n"
        "FOREIGN_EVENT = {'telefone': '5411999998888'}\n"
        "TOOL_REQUEST = {'name': 'cloudbeds_criar_reserva_v2'}\n"
        "OPERATIONAL_SOURCE_ID = 'ss-960889123456'\n"
        "EXPECTED_PHONE = '+55 75 99999-2939'\n"
        "assert LEAD['name'] == 'Carlos Eduardo'\n"
        "assert PHONE_EVENT['phone_number'] == '\x35\x35\x37\x35\x39\x39\x39\x39\x39\x32\x39\x33\x39'\n"
        "def test_parser_phone_relation():\n"
        "    payload = {'phone_number': '\x35\x35\x37\x35\x39\x39\x39\x39\x39\x32\x39\x33\x39'}\n"
        "    assert event.phone == '\x2b\x35\x35\x31\x31\x38\x38\x38\x38\x38\x37\x37\x37\x37'\n"
        "    assert 'telefone=+55 11 88888-7777' in prompt\n"
    )
    (source / "tests/test_bokun_v2_tools.py").write_text(
        "CONTACT = {'whatsapp_phone': '\x2b\x35\x35\x32\x32\x37\x37\x37\x37\x37\x36\x36\x36\x36', 'changed': True}\n"
    )
    untracked = source / "tests/new_test.py"
    untracked.parent.mkdir(parents=True, exist_ok=True)
    untracked.write_text("def test_new_boundary():\n    assert True\n")
    return source, head


class Phase7RuntimeCaptureTests(unittest.TestCase):
    def test_synthetic_phone_uses_reserved_non_e164_namespace_and_fixture_rescans(self) -> None:
        value = _synthetic_phone("+5575999992939")
        self.assertTrue(value.startswith("+999"))
        self.assertFalse(value.startswith("+55"))
        fixture = Path(
            "/home/ubuntu/workspace/agente-v2-phase7-runtime-candidate4b/"
            "tests/fixtures/phase7_boundary_states.json"
        )
        payload, redactions = _scan_text(
            fixture,
            "tests/fixtures/phase7_boundary_states.json",
        )
        self.assertGreater(len(payload), 0)
        self.assertEqual(redactions, 0)

    def test_sanitizer_preserves_non_contact_operational_ids(self) -> None:
        source = "OPERATIONAL_SOURCE_ID = 'ss-960889123456'\n"
        sanitized, redactions = _sanitize_allowlisted_test(source)
        self.assertEqual(sanitized, source)
        self.assertEqual(redactions, 0)

    def test_sanitizer_aligns_string_and_integer_contact_ids(self) -> None:
        original = "\x31\x38\x37\x33\x30\x31\x38\x35\x33\x37"
        source = (
            f"EVENT = {{'id': '{original}'}}\n"
            f"EXPECTED = {{'subscriber_id': {original}}}\n"
        )
        sanitized, _ = _sanitize_allowlisted_test(source)
        string_id = re.search(r"'id': '(\d+)'", sanitized)
        integer_id = re.search(r"'subscriber_id': (\d+)", sanitized)
        self.assertIsNotNone(string_id)
        self.assertIsNotNone(integer_id)
        self.assertNotEqual(string_id.group(1), original)
        self.assertEqual(string_id.group(1), integer_id.group(1))

    def test_sanitizer_aligns_message_id_with_derived_oracle(self) -> None:
        source = (
            "MESSAGE = {'id': 'audio-1'}\n"
            "EXPECTED = 'sha256:audio-1'\n"
        )
        sanitized, _ = _sanitize_allowlisted_test(source)
        message_id = re.search(r"'id': '([^']+)'", sanitized)
        self.assertIsNotNone(message_id)
        self.assertNotEqual(message_id.group(1), "audio-1")
        self.assertIn(f"sha256:{message_id.group(1)}", sanitized)

    def test_sanitizer_does_not_rewrite_short_id_inside_python_identifier(self) -> None:
        source = (
            "EVENT = {'id': 'sub'}\n"
            "reservation_confirmation_subject_signature = 'safe'\n"
        )
        sanitized, _ = _sanitize_allowlisted_test(source)
        self.assertIn("reservation_confirmation_subject_signature", sanitized)
        compile(sanitized, "<sanitized>", "exec")

    def test_capture_reconstructs_safe_dirty_state_without_source_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase7-capture-test-") as directory:
            root = Path(directory)
            source, head = synthetic_runtime(root)
            output = root / "replica"
            manifest = root / "runtime-source-manifest.json"
            contract = root / "runtime-contract-manifest.json"
            before = source_fingerprint(source)
            result = capture_runtime(
                source=source,
                output=output,
                manifest_path=manifest,
                contract_manifest_path=contract,
                expected_head=head,
                untracked_allowlist=("tests/new_test.py",),
            )
            self.assertEqual(source_fingerprint(source), before)
            self.assertEqual(
                (output / "app.py").read_text(),
                "def _process_event(event):\n"
                "    api_key = normalize_runtime_key(event.get('API_KEY', ''))\n"
                "    return {'ok': event, 'configured': bool(api_key)}\n",
            )
            self.assertTrue((output / "tests/new_test.py").is_file())
            self.assertEqual(
                (output / ".env.example").read_text(),
                "API_KEY=placeholder\n",
            )
            self.assertTrue(
                (output / "qa/maya_test_lab/scenarios/__init__.py").is_file()
            )
            self.assertTrue(
                (output / "qa/maya_test_lab/scenarios/core_smoke.json").is_file()
            )
            self.assertEqual(
                (output / "qa/maya_test_lab/scenarios/real_world_v1.json").read_text(),
                "{\"schema_version\":1,\"fixture\":\"tracked-safe-baseline\"}\n",
            )
            redacted = (output / "tests/test_app_llm_central_webhook.py").read_text()
            self.assertNotIn("1873018537", redacted)
            self.assertNotIn("Carlos", redacted)
            self.assertNotIn("Eduardo", redacted)
            self.assertNotIn("\x2b\x35\x35\x37\x35\x39\x39\x39\x39\x39\x32\x39\x33\x39", redacted)
            self.assertIn("Synthetic Lead", redacted)
            self.assertIn("'name': 'cloudbeds_criar_reserva_v2'", redacted)
            self.assertIn("OPERATIONAL_SOURCE_ID = 'ss-960889123456'", redacted)
            self.assertRegex(redacted, r"'whatsapp_phone': '\+999\d{10}'")
            self.assertRegex(redacted, r"'phone_number': '999\d{10}'")
            self.assertRegex(redacted, r"'telefone': '999\d{10}'")
            raw_phone = re.search(r"'phone_number': '(999\d{10})'", redacted)
            expected_phone = re.search(r"EXPECTED_PHONE = '(\+999\d{10})'", redacted)
            self.assertIsNotNone(raw_phone)
            self.assertIsNotNone(expected_phone)
            self.assertEqual("+" + raw_phone.group(1), expected_phone.group(1))
            relation = re.search(
                r"def test_parser_phone_relation\(\):.*?phone_number': '(999\d{10})'.*?"
                r"assert event\.phone == '(\+999\d{10})'",
                redacted,
                re.DOTALL,
            )
            self.assertIsNotNone(relation)
            self.assertEqual("+" + relation.group(1), relation.group(2))
            prompt_phone = re.search(
                r"def test_parser_phone_relation\(\):.*?telefone=(\+999\d{10})",
                redacted,
                re.DOTALL,
            )
            self.assertIsNotNone(prompt_phone)
            self.assertEqual(relation.group(2), prompt_phone.group(1))
            provider_test = (output / "tests/test_bokun_v2_tools.py").read_text()
            self.assertRegex(provider_test, r"'whatsapp_phone': '\+999\d{10}'")
            self.assertNotIn("\x2b\x35\x35\x32\x32\x37\x37\x37\x37\x37\x36\x36\x36\x36", provider_test)
            first_phone = re.search(r"'whatsapp_phone': '(\+999\d{10})'", redacted)
            second_phone = re.search(r"'whatsapp_phone': '(\+999\d{10})'", provider_test)
            self.assertIsNotNone(first_phone)
            self.assertIsNotNone(second_phone)
            self.assertNotEqual(first_phone.group(1), second_phone.group(1))
            self.assertEqual(run_git(output, "status", "--porcelain"), "")
            self.assertEqual(result.source_head, head)
            self.assertEqual(
                result.excluded_paths,
                (
                    ".env.example",
                    "qa/maya_test_lab/scenarios/real_world_v1.json",
                ),
            )
            source_doc = json.loads(manifest.read_text())
            contract_doc = json.loads(contract.read_text())
            self.assertTrue(source_doc["source_unchanged"])
            self.assertEqual(
                [row["path"] for row in source_doc["redacted_paths"]],
                [
                    "tests/test_app_llm_central_webhook.py",
                    "tests/test_bokun_v2_tools.py",
                ],
            )
            self.assertEqual(source_doc["synthetic_baseline_commit"], run_git(output, "rev-parse", "HEAD"))
            self.assertEqual(contract_doc["counts"], {"active": 3, "read": 1, "state_commit": 1, "write": 1})
            self.assertEqual(
                [tool["name"] for tool in contract_doc["tools"]],
                ["chapada_commit_state", "read_tool", "write_tool"],
            )
            read_parameters = next(
                row["parameters"] for row in contract_doc["tools"] if row["name"] == "read_tool"
            )
            self.assertEqual(
                read_parameters["properties"]["description"],
                {"type": "string"},
            )
            self.assertNotIn("copy-only detail", json.dumps(contract_doc))
            self.assertNotIn("real argument copy", json.dumps(contract_doc))

    def test_contract_manifest_is_deterministic_and_contains_only_schema_hashes_and_shapes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase7-contract-test-") as directory:
            source, _ = synthetic_runtime(Path(directory))
            first = build_runtime_contract_manifest(source)
            second = build_runtime_contract_manifest(source)
            self.assertEqual(first, second)
            serialized = json.dumps(first, sort_keys=True)
            read_parameters = next(
                row["parameters"] for row in first["tools"] if row["name"] == "read_tool"
            )
            self.assertEqual(read_parameters["properties"]["description"], {"type": "string"})
            for forbidden in ("default", "example", "examples", "title", "$comment"):
                self.assertNotIn(f'"{forbidden}"', serialized)
            for tool in first["tools"]:
                self.assertEqual(set(tool), {"category", "name", "parameters", "schema_hash"})
                self.assertEqual(
                    hashlib.sha256(
                        json.dumps(
                            tool["parameters"],
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest(),
                    tool["schema_hash"],
                )

    def test_hostile_secret_pii_symlink_unallowlisted_and_existing_output_fail_closed(self) -> None:
        cases = (
            ("secret", "TOKEN='\x67\x68\x70\x5f\x61\x62\x63\x64\x65\x66\x67\x68\x69\x6a\x6b\x6c\x6d\x6e\x6f\x70\x71\x72\x73\x74\x75\x76\x77\x78\x79\x7a\x31\x32\x33\x34\x35\x36'\n", False),
            ("generic_token", "TOKEN='highentropy0123456789abcdefXYZ'\n", False),
            ("email", "OWNER='private.person@private-domain.test'\n", False),
            ("long_digits", "REFERENCE='1234567890123456'\n", False),
            ("phone", "CONTACT='\x2b\x35\x35\x31\x31\x39\x39\x38\x37\x36\x35\x34\x33\x32'\n", False),
            ("unallowlisted", "safe = True\n", False),
            ("symlink", "", True),
        )
        for name, content, symlink in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory(
                prefix=f"phase7-hostile-{name}-"
            ) as directory:
                root = Path(directory)
                source, head = synthetic_runtime(root)
                hostile = source / "tests/hostile.py"
                if symlink:
                    hostile.symlink_to(source / "app.py")
                else:
                    hostile.write_text(content)
                allowlist = (
                    "tests/new_test.py",
                    "tests/hostile.py",
                ) if name != "unallowlisted" else ("tests/new_test.py",)
                before = source_fingerprint(source)
                with self.assertRaises(CaptureRejected):
                    capture_runtime(
                        source=source,
                        output=root / "replica",
                        manifest_path=root / "source.json",
                        contract_manifest_path=root / "contract.json",
                        expected_head=head,
                        untracked_allowlist=allowlist,
                    )
                self.assertFalse((root / "replica").exists())
                self.assertEqual(source_fingerprint(source), before)

        with tempfile.TemporaryDirectory(prefix="phase7-hostile-paths-") as directory:
            root = Path(directory)
            source, head = synthetic_runtime(root)
            (source / "runtime.db").write_bytes(b"SQLite format 3\x00")
            before = source_fingerprint(source)
            with self.assertRaises(CaptureRejected):
                capture_runtime(
                    source=source,
                    output=root / "replica",
                    manifest_path=root / "source.json",
                    contract_manifest_path=root / "contract.json",
                    expected_head=head,
                    untracked_allowlist=("tests/new_test.py", "runtime.db"),
                )
            self.assertEqual(source_fingerprint(source), before)

    def test_existing_output_is_never_removed_or_reused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase7-existing-output-") as directory:
            root = Path(directory)
            source, head = synthetic_runtime(root)
            output = root / "replica"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("keep")
            with self.assertRaises(CaptureRejected):
                capture_runtime(
                    source=source,
                    output=output,
                    manifest_path=root / "source.json",
                    contract_manifest_path=root / "contract.json",
                    expected_head=head,
                    untracked_allowlist=("tests/new_test.py",),
                )
            self.assertEqual(marker.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
