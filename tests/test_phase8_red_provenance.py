"""Focused RED provenance and candidate-pair tests for Phase 8."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from typing import Iterator

from phase8_release.candidate_pair import CandidatePair, CandidatePairError
from phase8_release.red_provenance import (
    ExecutionRootManifest,
    RedProvenance,
    verify_red_replay,
)


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


@contextmanager
def _provenance_fixture() -> Iterator[dict[str, object]]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repository = root / "repository"
        repository.mkdir()
        _git(repository, "init", "-q")
        _git(repository, "config", "user.name", "Phase 8 Test")
        _git(repository, "config", "user.email", "phase8@example.invalid")
        (repository / "README.md").write_text("baseline\n", encoding="utf-8")
        _git(repository, "add", "README.md")
        _git(repository, "commit", "-qm", "baseline")
        unfixed_commit = _git(repository, "rev-parse", "HEAD")
        unfixed_tree = _git(repository, "rev-parse", "HEAD^{tree}")

        tests_dir = repository / "tests"
        tests_dir.mkdir()
        test_path = tests_dir / "test_probe.py"
        test_path.write_text(
            "import unittest\n\n"
            "class ProbeTests(unittest.TestCase):\n"
            "    def test_missing_contract(self):\n"
            "        self.fail('contract missing')\n",
            encoding="utf-8",
        )
        _git(repository, "add", "-N", "tests/test_probe.py")
        patch_bytes = subprocess.run(
            [
                "git",
                "diff",
                "--binary",
                "--full-index",
                "-U0",
                unfixed_commit,
                "--",
                "tests/test_probe.py",
            ],
            cwd=repository,
            check=True,
            capture_output=True,
        ).stdout
        _git(repository, "reset", "--", "tests/test_probe.py")
        test_path.unlink()
        patch_path = root / "red.patch"
        patch_path.write_bytes(patch_bytes)

        _git(
            repository,
            "apply",
            "--index",
            "--binary",
            "--unidiff-zero",
            "--whitespace=nowarn",
            str(patch_path),
        )
        staged_tree = _git(repository, "write-tree")
        test_blob = _git(repository, "rev-parse", f"{staged_tree}:tests/test_probe.py")
        output_path = root / "red-output.txt"
        output_path.write_bytes(b"FAILED (failures=1)\n")
        git_dir_value = Path(_git(repository, "rev-parse", "--git-dir"))
        git_dir = (
            git_dir_value
            if git_dir_value.is_absolute()
            else repository / git_dir_value
        ).resolve()
        execution = ExecutionRootManifest(
            absolute_root=str(repository.resolve()),
            root_kind="temporary_index",
            git_dir_identity=str(git_dir),
            head_commit=unfixed_commit,
            staged_tree=staged_tree,
            patch_paths=("tests/test_probe.py",),
            python_executable=sys.executable,
            python_version=platform.python_version(),
            tool_versions=(("git", _git(repository, "--version")),),
            env_names=("PATH", "PYTHONDONTWRITEBYTECODE"),
        )
        provenance = RedProvenance.from_run(
            execution_root_manifest=execution,
            test_patch_path=patch_path,
            argv=(
                sys.executable,
                "-B",
                "-m",
                "unittest",
                "tests.test_probe.ProbeTests.test_missing_contract",
                "-v",
            ),
            cwd=str(repository.resolve()),
            env_name_allowlist=("PATH", "PYTHONDONTWRITEBYTECODE"),
            exit_code=1,
            duration_ns=123,
            counts=(("tests", 1), ("failures", 1), ("errors", 0)),
            output_path=output_path,
        )
        yield {
            "execution": execution,
            "output_path": output_path,
            "patch_path": patch_path,
            "provenance": provenance,
            "repository": repository,
            "staged_tree": staged_tree,
            "test_blob": test_blob,
            "unfixed_commit": unfixed_commit,
            "unfixed_tree": unfixed_tree,
        }


class RedProvenanceTests(unittest.TestCase):
    def test_staged_tree_is_exact_application_of_patch_to_unfixed_tree(self) -> None:
        with _provenance_fixture() as fixture:
            replay = verify_red_replay(
                fixture["repository"],
                fixture["provenance"],
                fixture["patch_path"],
            )

        self.assertEqual(replay.staged_tree, fixture["staged_tree"])
        self.assertEqual(replay.changed_paths, ("tests/test_probe.py",))
        self.assertEqual(replay.test_blobs, (("tests/test_probe.py", fixture["test_blob"]),))
        self.assertEqual(fixture["provenance"].unfixed_tree, fixture["unfixed_tree"])

    def test_green_candidate_cannot_change_red_patch_bytes(self) -> None:
        with _provenance_fixture() as fixture:
            repository = fixture["repository"]
            replay = verify_red_replay(
                repository,
                fixture["provenance"],
                fixture["patch_path"],
            )
            source = repository / "phase8_release"
            source.mkdir()
            (source / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            _git(repository, "add", "phase8_release/feature.py")
            _git(repository, "commit", "-qm", "functional")
            functional_commit = _git(repository, "rev-parse", "HEAD")
            functional_tree = _git(repository, "rev-parse", "HEAD^{tree}")

            evidence_path = "docs/refactor/evidence/phase-08/tasks/task-00/green-result.json"
            absolute_evidence = repository / evidence_path
            absolute_evidence.parent.mkdir(parents=True)
            absolute_evidence.write_text('{"status":"PASS"}\n', encoding="utf-8")
            _git(repository, "add", evidence_path)
            _git(repository, "commit", "-qm", "evidence")
            evidence_commit = _git(repository, "rev-parse", "HEAD")
            evidence_tree = _git(repository, "rev-parse", "HEAD^{tree}")

            pair = CandidatePair.verify(
                repository_root=repository,
                functional_commit=functional_commit,
                functional_tree=functional_tree,
                evidence_commit=evidence_commit,
                evidence_tree=evidence_tree,
                evidence_allowed_paths=(evidence_path,),
                required_test_blobs=replay.test_blobs,
            )
            self.assertEqual(pair.evidence_paths, (evidence_path,))

            _git(repository, "checkout", "-qb", "bad-functional", functional_commit)
            (repository / "tests/test_probe.py").write_text(
                "# weakened after RED\n",
                encoding="utf-8",
            )
            _git(repository, "add", "tests/test_probe.py")
            _git(repository, "commit", "-qm", "mutate red test")
            bad_functional = _git(repository, "rev-parse", "HEAD")
            bad_functional_tree = _git(repository, "rev-parse", "HEAD^{tree}")
            absolute_evidence.parent.mkdir(parents=True, exist_ok=True)
            absolute_evidence.write_text('{"status":"PASS"}\n', encoding="utf-8")
            _git(repository, "add", evidence_path)
            _git(repository, "commit", "--allow-empty", "-qm", "bad evidence")
            bad_evidence = _git(repository, "rev-parse", "HEAD")
            bad_evidence_tree = _git(repository, "rev-parse", "HEAD^{tree}")

            with self.assertRaises(CandidatePairError):
                CandidatePair.verify(
                    repository_root=repository,
                    functional_commit=bad_functional,
                    functional_tree=bad_functional_tree,
                    evidence_commit=bad_evidence,
                    evidence_tree=bad_evidence_tree,
                    evidence_allowed_paths=(),
                    required_test_blobs=replay.test_blobs,
                )

    def test_output_pointer_has_hash_size_command_and_environment(self) -> None:
        with _provenance_fixture() as fixture:
            provenance = fixture["provenance"]
            output = fixture["output_path"].read_bytes()
            patch = fixture["patch_path"].read_bytes()

        self.assertEqual(provenance.output_sha256, hashlib.sha256(output).hexdigest())
        self.assertEqual(provenance.output_bytes, len(output))
        self.assertEqual(provenance.test_patch_sha256, hashlib.sha256(patch).hexdigest())
        self.assertEqual(
            provenance.argv[-2:],
            ("tests.test_probe.ProbeTests.test_missing_contract", "-v"),
        )
        self.assertEqual(
            provenance.env_name_allowlist,
            ("PATH", "PYTHONDONTWRITEBYTECODE"),
        )
        payload = json.loads(provenance.to_json())
        self.assertNotIn("output", payload)
        self.assertEqual(payload["output_sha256"], provenance.output_sha256)
