# Fase 8 — Shadow, Canary e Rollout por Digest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir uma única imagem OCI a partir da réplica sanitizada aprovada na Fase 7, provar zero efeito em dark canary e ingress canary, chegar ao teste conversacional realizado por Carlos e somente então permitir uma canary E2E e rollout gradual do mesmo image ID.

**Architecture:** O repositório `agente-v2` possui tooling/evidência da release; o conteúdo do app vem exclusivamente da réplica limpa `agente-v2-phase7-runtime-candidate10`. Um package stdlib `phase8_release` valida source, ambiente, image identity e estado dos gates. O container canary é paralelo, usa Hermes home e state efêmeros, recebe apenas credenciais de read Cloudbeds/Bókun e nunca herda a `.env` live integral. Canary e promoção referenciam a imagem já construída; `build:` é proibido depois do freeze.

**Tech Stack:** Python 3.12 stdlib, `dataclasses`, `enum`, `json`, `hashlib`, `pathlib`, `subprocess`, `tempfile`, `unittest`; Docker Engine/Buildx local; FastAPI/runtime já existentes apenas dentro da imagem; Cloudbeds/Bókun read-only; Hermes Leads profile clonado e isolado; GitHub Actions somente para gates offline.

## Global Constraints

- Base da fase: `93682024b4867d3e313324339a7060d5351dcd3d`, tree `b779e35c671f3050d056c6ef3c8c0700f5b13f35`.
- Spec aprovada: `0dbc9cb9722762dfc4f24a3ea73bfce974835a84`.
- Branch/worktree: `phase8-shadow-canary-rollout` em `/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout`.
- Runtime operacional `/home/ubuntu/chapada-leads-hermes`: estritamente somente leitura até promoção autorizada; nunca é build context.
- Réplica de build: `/home/ubuntu/workspace/agente-v2-phase7-runtime-candidate10`, commit `183fb41d645e1bb04e237c986988309a28e42b34`, tree `e546e9d88093c09a245502bcca3d119e2e450672`, status limpo.
- Wheel: SHA-256 `be1bed664f9eb0a9f0af06b31bd55688e4041c81411ee1cc22416282270446dd`, 214954 bytes.
- Patch: SHA-256 `4d0ccd5e6dae410abca8da8b555fd0784668eecd5c4e0499e919997be38e0218`, 96073 bytes.
- Imagem live de rollback na entrada: `sha256:2dc5f71557b82d4d0646ab1dba0b61edfa7d916320047dd03ce8554dbfa50d53`.
- Build uma vez por snapshot; qualquer mudança material na imagem invalida todos os gates posteriores.
- Dark canary não recebe ManyChat API key, SMTP, Stripe, Wise, Supabase, Redis ou volumes/sessões live.
- Cloudbeds/Bókun somente read-only até autorização E2E; todos os outros provider/payment gates ficam fechados.
- Nenhum texto bruto, telefone, subscriber ID, e-mail, payload provider, token ou auth entra no Git/evidência.
- Carlos executa o teste conversacional. O controller deve parar e avisá-lo no Gate D; não simula aprovação humana.
- Provider/workflow/período da canary E2E não têm default. Devem ser fixados por autorização explícita após a conversa.
- Rollout permanece `NO-GO`; `phase9_started=false` até closeout e nova decisão.
- Desenvolvimento: RED/GREEN focused, regressão por blast radius, uma suíte integral por candidato e uma construção OCI por snapshot.

## File Structure

```text
phase8_release/
  __init__.py
  identity.py            # Git/runtime/image identities and source preflight
  canary_env.py          # allowlist env, minimal Hermes-home clone, zero-effect proof
  docker_runtime.py      # build/create/inspect/tag commands with injected runner
  results.py             # closed stage result schemas and deterministic JSON
scripts/
  build_phase8_image.py
  prepare_phase8_canary.py
  run_phase8_dark_canary.py
  run_phase8_ingress_canary.py
  promote_phase8_image.py
  generate_phase8_manifest.py
  validate_phase8.py
tests/
  test_phase8_entry.py
  test_phase8_identity.py
  test_phase8_canary_env.py
  test_phase8_docker_runtime.py
  test_phase8_results.py
  test_phase8_dark_canary.py
  test_phase8_ingress.py
  test_phase8_closeout.py
docs/refactor/phases/phase-08-shadow-canary-rollout.md
docs/refactor/evidence/phase-08/
.github/workflows/phase8.yml
```

Private runtime material lives only under
`/home/ubuntu/workspace/phase8-canary-private/<release-id>/` with mode `0700`;
image archives live under `/home/ubuntu/workspace/phase8-release-artifacts/<release-id>/`.

---

### Task 1: Activate Phase 8 and authenticate entry

**Files:**
- Create: `docs/refactor/phases/phase-08-shadow-canary-rollout.md`
- Create: `docs/refactor/evidence/phase-08/README.md`
- Create: `docs/refactor/evidence/phase-08/entry-baseline.json`
- Create: `docs/refactor/evidence/phase-08/red-results.json`
- Create: `tests/test_phase8_entry.py`
- Modify: `README.md`
- Modify: `docs/refactor/README.md`
- Modify: `docs/refactor/evidence/README.md`
- Modify: `docs/refactor/phases/phase-07-boundary-migration.md`
- Modify: `docs/refactor/06-risk-register.md`
- Regenerate if changed: `docs/refactor/evidence/phase-03..07/{manifest.json,SHA256SUMS}`

**Interfaces:**
- Consumes: published Phase 7 closeout, spec commit and runtime fingerprints.
- Produces: active Phase 8 declaration, exact entry baseline and all prior validators green.

- [ ] **Step 1: Write the entry RED**

```python
# tests/test_phase8_entry.py
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Phase8EntryTests(unittest.TestCase):
    def test_entry_pins_published_phase7_and_keeps_rollout_closed(self) -> None:
        entry = json.loads(
            (ROOT / "docs/refactor/evidence/phase-08/entry-baseline.json").read_text()
        )
        self.assertEqual(entry["base_commit"], "93682024b4867d3e313324339a7060d5351dcd3d")
        self.assertEqual(entry["spec_commit"], "0dbc9cb9722762dfc4f24a3ea73bfce974835a84")
        self.assertEqual(entry["phase7_ci_run_id"], 29804123764)
        self.assertEqual(entry["phase7_review_approved"], 3)
        self.assertEqual(entry["rollout"], "NO-GO")
        self.assertFalse(entry["phase9_started"])

    def test_phase_index_has_one_active_phase(self) -> None:
        text = (ROOT / "docs/refactor/README.md").read_text()
        self.assertIn("7. Migração das fronteiras | **concluída", text)
        self.assertIn("8. Shadow, canary e rollout | **ativa — design/plano", text)
        self.assertIn("9. Remoção do legado | bloqueada", text)
```

- [ ] **Step 2: Run RED and retain its envelope**

```bash
python3 -B -m unittest tests.test_phase8_entry -v \
  >/tmp/phase8-task1-red.out 2>&1
```

Expected: nonzero because Phase 8 entry files do not exist. Record command, exit,
SHA-256 and bytes in `phase-08/red-results.json`; raw output stays in `/tmp`.

- [ ] **Step 3: Write the entry evidence mechanically**

Use a stdlib Python snippet that reads Git objects and the already-authenticated
Phase 7 JSON. `plan_commit` is the current `git rev-parse HEAD` at Task 1 start;
it must be the committed plan tip, not a guessed SHA. Include:

```json
{
  "base_commit": "93682024b4867d3e313324339a7060d5351dcd3d",
  "base_tree": "b779e35c671f3050d056c6ef3c8c0700f5b13f35",
  "phase7_ci_run_id": 29804123764,
  "phase7_review_approved": 3,
  "runtime_original_head": "57408d8b2040399bc25ee7957505208079458884",
  "runtime_original_tree": "67b5fe18d4685281778e41cd61cd584dd063ea60",
  "runtime_original_status_entries": 86,
  "runtime_original_status_z_sha256": "e299a15f0336646ef62d5e88a4989d46ef46d6865c5d3163e092969fa9a8ef7a",
  "runtime_original_diff_sha256": "1b66221d27290ab6eb3c76e7cea2ab6e678fd06d1489f99752a42ee89cd1608b",
  "rollout": "NO-GO",
  "phase9_started": false
}
```

Also record Python `3.12.13`, SQLite `3.46.1`, replica commit/tree/status,
live image ID/release metadata and the post-merge 762/762 output digest.

- [ ] **Step 4: Correct stale Phase 7 status and activate only Phase 8**

`phase-07-boundary-migration.md` must state the final functional candidate,
terminal snapshot, review 3/3, run `29804123764`, closeout `9368202...`, runtime
untouched and Phase 8 authorization date. Do not rewrite historical invalidations.

- [ ] **Step 5: Regenerate shared manifests in dependency order**

```bash
for n in 3 4 5 6 7; do
  python3 -B "scripts/generate_phase${n}_manifest.py" --write
 done
python3 - <<'PY'
import json
from pathlib import Path
p = Path("docs/refactor/evidence/phase-07/candidate.json")
candidate = json.loads(p.read_text())
manifest = json.loads(Path("docs/refactor/evidence/phase-07/manifest.json").read_text())
candidate["manifest_sha256"] = manifest["aggregate_sha256"]
p.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n")
PY
python3 -B scripts/generate_phase7_manifest.py --check
for n in 0 1 2 3 4 5 6; do python3 -B "scripts/validate_phase${n}.py"; done
python3 -B scripts/validate_phase7.py --terminal
```

If a validator identifies a different shared-owner dependency, update that
owner's generated artifact; never weaken a validator.

- [ ] **Step 6: Run GREEN and commit**

```bash
python3 -B -m unittest tests.test_phase8_entry -v
python3 -B -m unittest tests.test_phase7_closeout -v
git diff --check
git add README.md docs/refactor tests/test_phase8_entry.py
git commit -m "docs(phase8): activate shadow canary phase"
```

---

### Task 2: Implement closed source identities and preflight

**Files:**
- Create: `phase8_release/__init__.py`
- Create: `phase8_release/identity.py`
- Create: `tests/test_phase8_identity.py`
- Modify: `docs/refactor/evidence/phase-08/red-results.json`

**Interfaces:**
- Produces:
  - `GitIdentity(path: str, commit: str, tree: str, status_entries: int, status_z_sha256: str)`
  - `RuntimeFingerprint(head: str, tree: str, status_entries: int, status_z_sha256: str, diff_sha256: str)`
  - `git_identity(path: Path) -> GitIdentity`
  - `runtime_fingerprint(path: Path) -> RuntimeFingerprint`
  - `verify_release_sources(pins: ReleasePins) -> dict[str, object]`

- [ ] **Step 1: Write causal identity REDs**

```python
class Phase8IdentityTests(unittest.TestCase):
    def test_preflight_rejects_operational_tree_as_build_context(self) -> None:
        pins = fixture_pins(replica_path=Path("/home/ubuntu/chapada-leads-hermes"))
        with self.assertRaisesRegex(ValueError, "operational runtime cannot be build context"):
            verify_release_sources(pins)

    def test_preflight_requires_exact_clean_replica_commit_and_tree(self) -> None:
        with temporary_git_repo() as repo:
            pins = fixture_pins(replica_path=repo.path, replica_commit="0" * 40)
            with self.assertRaisesRegex(ValueError, "replica commit mismatch"):
                verify_release_sources(pins)
            repo.write("untracked.txt", "x")
            pins = fixture_pins(replica_path=repo.path, replica_commit=repo.head)
            with self.assertRaisesRegex(ValueError, "replica is not clean"):
                verify_release_sources(pins)
```

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase8_identity -v \
  >/tmp/phase8-task2-red.out 2>&1
```

Expected: import failure for missing package.

- [ ] **Step 3: Implement exact identity types**

```python
# phase8_release/identity.py
@dataclass(frozen=True)
class GitIdentity:
    path: str
    commit: str
    tree: str
    status_entries: int
    status_z_sha256: str


def git_identity(path: Path) -> GitIdentity:
    root = path.resolve(strict=True)
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "-z", "-uall"], cwd=root
    )
    return GitIdentity(
        path=str(root),
        commit=_git(root, "rev-parse", "HEAD"),
        tree=_git(root, "rev-parse", "HEAD^{tree}"),
        status_entries=len([row for row in status.split(b"\0") if row]),
        status_z_sha256=hashlib.sha256(status).hexdigest(),
    )
```

Use exact lowercase hex40 validation, resolved paths and `git diff --binary HEAD`
for the runtime fingerprint. `ReleasePins` contains all exact identities from the
spec. No fallback to branch names or labels.

- [ ] **Step 4: Run GREEN and real read-only preflight**

```bash
python3 -B -m unittest tests.test_phase8_identity -v
python3 -B - <<'PY'
from pathlib import Path
from phase8_release.identity import ReleasePins, verify_release_sources
pins = ReleasePins.phase8_defaults(
    agent_repo=Path.cwd(),
    replica=Path('/home/ubuntu/workspace/agente-v2-phase7-runtime-candidate10'),
    operational=Path('/home/ubuntu/chapada-leads-hermes'),
)
print(verify_release_sources(pins))
PY
```

Expected: exact replica/runtime identities and no mutation.

- [ ] **Step 5: Commit**

```bash
git add phase8_release tests/test_phase8_identity.py \
  docs/refactor/evidence/phase-08/red-results.json
git commit -m "feat(phase8): add fail-closed release preflight"
```

---

### Task 3: Build an allowlisted canary environment and minimal profile clone

**Files:**
- Create: `phase8_release/canary_env.py`
- Create: `scripts/prepare_phase8_canary.py`
- Create: `tests/test_phase8_canary_env.py`
- Modify: `docs/refactor/evidence/phase-08/red-results.json`

**Interfaces:**
- Produces:
  - `build_dark_env(source: Mapping[str, str], private_root: Path, webhook_secret: str) -> dict[str, str]`
  - `validate_dark_env(env: Mapping[str, str]) -> None`
  - `clone_minimal_hermes_home(global_home: Path, profile_dir: Path, replica: Path, target: Path) -> ProfileCloneResult`
  - `write_private_env(path: Path, env: Mapping[str, str]) -> None`

- [ ] **Step 1: Write environment REDs**

```python
class Phase8CanaryEnvTests(unittest.TestCase):
    def test_dark_env_keeps_only_provider_reads_and_closes_every_effect(self) -> None:
        source = live_like_env_fixture()
        env = build_dark_env(source, Path("/private"), "synthetic-secret")
        self.assertEqual(env["HERMES_LEADS_MODE"], "shadow")
        self.assertEqual(env["HERMES_LEADS_DRY_RUN"], "true")
        self.assertEqual(env["HERMES_LEADS_ALLOW_LIVE_SENDS"], "false")
        self.assertEqual(env["HERMES_CLOUDBEDS_READONLY_ENABLED"], "true")
        self.assertEqual(env["HERMES_BOKUN_READONLY_ENABLED"], "true")
        for key in CLOSED_BOOLEAN_KEYS:
            self.assertEqual(env[key], "false", key)
        for key in FORBIDDEN_SECRET_KEYS:
            self.assertNotIn(key, env)

    def test_profile_clone_excludes_state_logs_env_memory_and_unrelated_skills(self) -> None:
        result = clone_fixture_profile()
        names = result.relative_files
        self.assertIn("profiles/leads/config.yaml", names)
        self.assertIn("profiles/leads/SOUL.md", names)
        self.assertIn("profiles/leads/auth.json", names)
        self.assertNotIn("profiles/leads/state.db", names)
        self.assertNotIn("profiles/leads/.env", names)
        self.assertFalse(any(name.startswith("profiles/leads/logs/") for name in names))
```

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase8_canary_env -v \
  >/tmp/phase8-task3-red.out 2>&1
```

- [ ] **Step 3: Implement exact allowlists**

```python
READ_SECRET_KEYS = (
    "CLOUDBEDS_API_KEY", "CLOUDBEDS_PROPERTY_ID", "CLOUDBEDS_SOURCE_ID",
    "CLOUDBEDS_BASE_URL", "BOKUN_ACCESS_KEY", "BOKUN_SECRET_KEY",
    "BOKUN_BASE_URL",
)
CLOSED_BOOLEAN_KEYS = (
    "HERMES_LEADS_ALLOW_LIVE_SENDS",
    "HERMES_AUTO_FLUSH_ENABLED",
    "HERMES_PUBLIC_OUTBOX_AUTO_FLUSH_ENABLED",
    "HERMES_POST_PAYMENT_OUTBOX_WORKER_ENABLED",
    "HERMES_SIDE_EFFECT_LEDGER_ENABLED",
    "HERMES_CLOUDBEDS_WRITE_ENABLED",
    "HERMES_CLOUDBEDS_UPSELL_WRITE_ENABLED",
    "HERMES_CLOUDBEDS_PAYMENT_CONFIRMATION_WRITE_ENABLED",
    "HERMES_BOKUN_CART_WRITE_ENABLED",
    "HERMES_BOKUN_RESERVATION_WRITE_ENABLED",
    "HERMES_BOKUN_PAYMENT_CONFIRMATION_WRITE_ENABLED",
    "HERMES_STRIPE_PAYMENT_LINK_WRITE_ENABLED",
    "HERMES_CLOUDBEDS_STRIPE_PAYMENT_LINK_WRITE_ENABLED",
    "HERMES_WISE_PAYMENT_MATCHER_ENABLED",
    "HERMES_WISE_PAYMENT_MATCHER_SETTLEMENT_ENABLED",
    "HERMES_WISE_PAYMENT_VALIDATION_ENABLED",
    "HERMES_WISE_CLOUDBEDS_HOSTEL_PAYMENT_VALIDATION_WRITE_ENABLED",
    "HERMES_MEDIA_PROCESSING_ENABLED",
    "HERMES_LEADS_LLM_ENABLED",
    "LANGFUSE_ENABLED",
)
FORBIDDEN_SECRET_KEYS = (
    "MANYCHAT_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
    "REDIS_URL", "STRIPE_SECRET_KEY", "STRIPE_LIVE_SECRET_KEY",
    "WISE_API_TOKEN", "HERMES_LEADS_AGENT_EMAIL_PASSWORD_FILE",
)
```

The clone copies only global/profile `auth.json`, profile `config.yaml`, replica
`hermes_profiles/leads/SOUL.md`, the five versioned Chapada skills and manifest.
Create an empty `memories/MEMORY.md`; never copy `state.db`, `.env`, logs or cache.
Use `umask 077`, root mode `0700`, files `0600` and atomic writes.

`build_dark_env()` also sets these non-secret isolated paths and native-agent
owners exactly:

```python
env.update({
    "HERMES_LEAD_STATE_FILE": "/app/state/leads.json",
    "HERMES_SHADOW_LOG_FILE": "/app/state/shadow.jsonl",
    "HERMES_PUBLIC_MESSAGE_OUTBOX_FILE": "/app/state/public-outbox.json",
    "HERMES_POST_PAYMENT_OUTBOX_FILE": "/app/state/post-payment-outbox.json",
    "HERMES_LEADS_MIND_TYPE": "native_agent",
    "HERMES_LEADS_PROFILE_ENABLED": "true",
    "HERMES_LEADS_PROFILE_NAME": "leads",
    "HERMES_LEADS_PROFILE_HOME": "/home/hermeswebui/.hermes",
    "HERMES_LEADS_PROFILE_CWD": "/app",
    "HERMES_LEADS_PROFILE_TOOLSETS": "chapada-leads",
    "HERMES_LEADS_ALLOW_DETERMINISTIC_FALLBACK": "false",
    "ALLOW_DETERMINISTIC_FALLBACK": "false",
})
```

- [ ] **Step 4: GREEN and private-output safety check**

```bash
python3 -B -m unittest tests.test_phase8_canary_env -v
python3 -B scripts/prepare_phase8_canary.py --check-only \
  --replica /home/ubuntu/workspace/agente-v2-phase7-runtime-candidate10 \
  --operational-env /home/ubuntu/chapada-leads-hermes/.env
```

`--check-only` prints only key names, counts and fingerprints. It never prints
values or writes private material.

- [ ] **Step 5: Commit**

```bash
git add phase8_release/canary_env.py scripts/prepare_phase8_canary.py \
  tests/test_phase8_canary_env.py docs/refactor/evidence/phase-08/red-results.json
git commit -m "feat(phase8): isolate dark canary environment"
```

---

### Task 4: Pin Docker build, image identity and container topology

**Files:**
- Create: `phase8_release/docker_runtime.py`
- Create: `scripts/build_phase8_image.py`
- Create: `tests/test_phase8_docker_runtime.py`
- Modify: `docs/refactor/evidence/phase-08/red-results.json`

**Interfaces:**
- Produces:
  - `ImageIdentity(image_id: str, tags: tuple[str, ...], layers: tuple[str, ...], size: int, archive_sha256: str, archive_bytes: int)`
  - `build_command(spec: BuildSpec) -> tuple[str, ...]`
  - `dark_container_command(spec: CanaryContainerSpec) -> tuple[str, ...]`
  - `ingress_container_command(spec: CanaryContainerSpec) -> tuple[str, ...]`
  - `inspect_image(runner: Runner, tag: str, archive: Path) -> ImageIdentity`

- [ ] **Step 1: Write Docker command REDs**

```python
class Phase8DockerRuntimeTests(unittest.TestCase):
    def test_build_uses_clean_replica_once_and_unique_tag(self) -> None:
        command = build_command(build_fixture())
        self.assertEqual(command[:3], ("docker", "buildx", "build"))
        self.assertIn("--load", command)
        self.assertIn("--provenance=false", command)
        self.assertEqual(command[-1], "/replica")
        self.assertNotIn("/home/ubuntu/chapada-leads-hermes", command)

    def test_dark_container_has_no_public_route_or_live_mount(self) -> None:
        command = dark_container_command(canary_fixture())
        material = "\n".join(command)
        self.assertIn("--tmpfs\n/app/state", material)
        self.assertNotIn("traefik", material.casefold())
        self.assertNotIn("/home/ubuntu/chapada-leads-hermes-state", material)
        self.assertNotIn("/home/ubuntu/.hermes/profiles/leads", material)
        self.assertNotIn("--publish", command)
```

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase8_docker_runtime -v \
  >/tmp/phase8-task4-red.out 2>&1
```

- [ ] **Step 3: Implement command builders with injected runner**

```python
@dataclass(frozen=True)
class BuildSpec:
    replica: Path
    dockerfile: Path
    tag: str
    runtime_commit: str
    release_version: str


def build_command(spec: BuildSpec) -> tuple[str, ...]:
    return (
        "docker", "buildx", "build", "--load", "--provenance=false",
        "--sbom=false", "--file", str(spec.dockerfile),
        "--tag", spec.tag,
        "--build-arg", f"HERMES_RELEASE_COMMIT={spec.runtime_commit}",
        "--build-arg", f"HERMES_RELEASE_VERSION={spec.release_version}",
        str(spec.replica),
    )
```

The real builder creates a lock file, rejects an existing result/archive, executes
one build, inspects the image, saves it once with `docker image save`, hashes the
archive and writes `build-result.json`. It never retries automatically.

`dark_container_command()` uses `docker create`, unique container name, `--init`,
resource limits, `--restart=no`, `--env-file`, `--tmpfs /app/state`, private
Hermes home mount RW and Hermes CLI/checkout/Python mounts RO. It has no public
port/network labels.

`ingress_container_command()` is identical except it joins network `coolify` and
adds exact Traefik labels for
`/phase8-canary/health` and `/phase8-canary/webhook/manychat` with strip-prefix.
It still has no host port.

- [ ] **Step 4: Run GREEN and static command audit**

```bash
python3 -B -m unittest tests.test_phase8_docker_runtime -v
python3 -B scripts/build_phase8_image.py --print-command \
  --replica /home/ubuntu/workspace/agente-v2-phase7-runtime-candidate10 \
  --operational /home/ubuntu/chapada-leads-hermes
```

Expected: one buildx command pointing only at the replica; no Docker mutation.

- [ ] **Step 5: Commit**

```bash
git add phase8_release/docker_runtime.py scripts/build_phase8_image.py \
  tests/test_phase8_docker_runtime.py docs/refactor/evidence/phase-08/red-results.json
git commit -m "feat(phase8): pin OCI build and canary topology"
```

---

### Task 5: Add closed stage results, manifest, validator and offline CI

**Files:**
- Create: `phase8_release/results.py`
- Create: `scripts/generate_phase8_manifest.py`
- Create: `scripts/validate_phase8.py`
- Create: `tests/test_phase8_results.py`
- Create: `tests/test_phase8_closeout.py`
- Create: `.github/workflows/phase8.yml`
- Modify: `docs/refactor/evidence/phase-08/red-results.json`

**Interfaces:**
- Produces:
  - `Gate` enum: `entry`, `build`, `dark`, `ingress`, `conversation`, `e2e`, `rollout`, `terminal`.
  - `validate_phase8(gate: Gate) -> dict[str, object]`.
  - deterministic Phase 8 manifest and `SHA256SUMS`.

- [ ] **Step 1: Write blocked-vs-failed REDs**

```python
class Phase8ResultTests(unittest.TestCase):
    def test_missing_future_artifact_is_blocked_only_when_gate_requires_it(self) -> None:
        entry = validate_fixture(Gate.ENTRY, files=entry_files())
        self.assertEqual(entry["result"], "passed")
        dark = validate_fixture(Gate.DARK, files=entry_files())
        self.assertEqual(dark["result"], "blocked")
        self.assertEqual(dark["missing"], ["build-result.json", "dark-canary-result.json"])

    def test_existing_stale_artifact_is_failed_even_if_later_artifact_is_missing(self) -> None:
        files = entry_files() | {"build-result.json": stale_build_result()}
        result = validate_fixture(Gate.DARK, files=files)
        self.assertEqual(result["result"], "failed")
        self.assertIn("build image id mismatch", result["failures"])
```

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase8_results tests.test_phase8_closeout -v \
  >/tmp/phase8-task5-red.out 2>&1
```

- [ ] **Step 3: Implement closed schemas and aggregate diagnostics**

Every result requires exact types for `phase`, `schema_version`, `captured_at`,
`agent_commit/tree`, `runtime_commit/tree`, `image_id`, `capabilities_executed`,
`rollout` and `phase9_started`. Unknown keys fail. Validator collects all existing
artifact failures before reporting missing later gates.

Manifest includes only declared Phase 8 code/tests/docs/workflow and excludes
stage result JSON that would cause recursive identity. `release-manifest.json`
contains their identities instead.

- [ ] **Step 4: Add offline-only workflow**

`.github/workflows/phase8.yml` runs on the Phase 8 branch and contains no Docker
socket, secrets, provider, ManyChat or deploy step:

```yaml
permissions:
  contents: read
jobs:
  offline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -B -m unittest discover -s tests -v
      - run: python -B scripts/generate_phase8_manifest.py --check
      - run: python -B scripts/validate_phase8.py --gate entry
```

- [ ] **Step 5: GREEN and commit**

```bash
python3 -B -m unittest tests.test_phase8_results tests.test_phase8_closeout -v
python3 -B scripts/generate_phase8_manifest.py --write
python3 -B scripts/generate_phase8_manifest.py --check
python3 -B scripts/validate_phase8.py --gate entry
git diff --check
git add phase8_release scripts tests .github/workflows/phase8.yml \
  docs/refactor/evidence/phase-08
git commit -m "feat(phase8): add staged release evidence gates"
```

---

### Task 6: Freeze and build the single candidate image

**Files:**
- Create: `docs/refactor/evidence/phase-08/release-manifest.json`
- Create: `docs/refactor/evidence/phase-08/build-result.json`
- Modify: `docs/refactor/evidence/phase-08/README.md`
- Modify: `docs/refactor/evidence/phase-08/manifest.json`
- Modify: `docs/refactor/evidence/phase-08/SHA256SUMS`

**Interfaces:**
- Consumes: Tasks 1–5, clean replica and Docker daemon.
- Produces: one immutable local image, one archive, exact rollback identity and build gate `passed`.

- [ ] **Step 1: Run focused gates and prove non-drift**

```bash
python3 -B -m unittest \
  tests.test_phase8_identity tests.test_phase8_canary_env \
  tests.test_phase8_docker_runtime tests.test_phase8_results \
  tests.test_phase8_closeout -v
python3 -B scripts/validate_phase7.py --terminal
git diff --name-only 2c99be11b1bdc1b66d14bd7a19c510ec50d502d4..HEAD \
  -- reservation_boundary schemas/phase7 pyproject.toml scripts/build_phase7_wheel.py
```

Expected: no kernel/schema/wheel diff.

- [ ] **Step 2: Execute the one runtime full-suite window**

```bash
env -i HOME="$HOME" PATH=/usr/local/bin:/usr/bin:/bin \
  PYTHONPATH=/home/ubuntu/workspace/agente-v2-phase7-runtime-candidate10 \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/workspace/agente-v2-phase7-runtime-candidate10/config/leads_agent.yaml \
  CHAPADA_LEADS_APP_PATH=/home/ubuntu/workspace/agente-v2-phase7-runtime-candidate10 \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  >/tmp/phase8-runtime-full.out 2>&1
```

Run once. A pure PATH/environment error may be repeated only after correcting the
environment and must be aggregated explicitly; no code/test failure is rerun
without a change.

- [ ] **Step 3: Build once and save once**

```bash
release_id="$(git rev-parse HEAD)"
python3 -B scripts/build_phase8_image.py \
  --replica /home/ubuntu/workspace/agente-v2-phase7-runtime-candidate10 \
  --operational /home/ubuntu/chapada-leads-hermes \
  --tag "chapada-leads-hermes:phase8-${release_id}" \
  --archive "/home/ubuntu/workspace/phase8-release-artifacts/${release_id}/chapada-leads-hermes.tar" \
  --result docs/refactor/evidence/phase-08/build-result.json
```

The script must refuse if result/archive/tag already exists. Do not pass a retry
flag; investigate failure before deciding whether a new snapshot is required.

- [ ] **Step 4: Verify image metadata and live non-drift**

```bash
python3 -B scripts/validate_phase8.py --gate build
docker inspect chapada-leads-hermes --format '{{.Image}} {{.State.StartedAt}} {{.RestartCount}}'
git -C /home/ubuntu/chapada-leads-hermes status --porcelain=v1 -z -uall | sha256sum
```

Expected: live image/start/restarts and runtime fingerprint unchanged.

- [ ] **Step 5: Record release manifest, regenerate and commit with skip CI**

```bash
python3 -B scripts/generate_phase8_manifest.py --write
python3 -B scripts/generate_phase8_manifest.py --check
python3 -B scripts/validate_phase8.py --gate build
git add docs/refactor/evidence/phase-08
git commit -m "build(phase8): freeze immutable canary image [skip ci]"
git push origin phase8-shadow-canary-rollout
```

Evidence push is `[skip ci]`; the offline CI candidate was already exercised by
code commits, while the image identity is local operational evidence.

---

### Task 7: Implement and execute the three-flow dark canary

**Files:**
- Create: `scripts/run_phase8_dark_canary.py`
- Create: `tests/test_phase8_dark_canary.py`
- Create after real run: `docs/refactor/evidence/phase-08/dark-canary-result.json`
- Create after real run: `docs/refactor/evidence/phase-08/dark-canary-reports/summary.json`
- Modify: `docs/refactor/evidence/phase-08/red-results.json`

**Interfaces:**
- Produces `DarkCanaryDriver` with injected Docker/HTTP/readiness adapters and a closed result containing three flow summaries without text/PII.

- [ ] **Step 1: Write driver REDs with fake adapters**

```python
class Phase8DarkCanaryTests(unittest.TestCase):
    def test_three_flows_require_real_read_and_zero_effects(self) -> None:
        result = run_dark_canary(fake_driver(successful=True))
        self.assertEqual(result["flow_count"], 3)
        self.assertEqual(result["provider_reads"], {"cloudbeds": 1, "bokun": 1})
        self.assertEqual(result["provider_write_calls"], 0)
        self.assertEqual(result["manychat_sends"], 0)
        self.assertEqual(result["email_sends"], 0)
        self.assertEqual(result["commands_per_subject_max"], 1)

    def test_any_effect_or_second_command_is_no_go(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "dark canary stop condition"):
            run_dark_canary(fake_driver(provider_write_calls=1, max_commands=2))
```

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase8_dark_canary -v \
  >/tmp/phase8-task7-red.out 2>&1
```

- [ ] **Step 3: Implement the image-level driver**

The script:

1. reruns source/image preflight;
2. creates private env/profile under `phase8-canary-private/<release-id>`;
3. creates/starts `chapada-leads-hermes-phase8-canary` from the frozen image;
4. verifies effective `Settings` booleans inside the container;
5. uses read-only provider probes to choose one positive future lodging window and
   one positive Bókun option by canonical product ID;
6. drives synthetic webhook turns inside the container for lodging, activity and
   replay/correction;
7. records only route/status/hash/count/state/command/effect fields;
8. inspects mounts, files, outboxes and logs for prohibited effects;
9. stops/removes the canary but preserves private evidence fingerprints;
10. verifies the live container/runtime fingerprint again.

Do not print model replies or provider data. Negative/uncertain read stops the
gate and records its class; it is not converted into a positive result.

- [ ] **Step 4: GREEN with fakes**

```bash
python3 -B -m unittest tests.test_phase8_dark_canary -v
```

- [ ] **Step 5: Execute the real dark canary once**

```bash
python3 -B scripts/run_phase8_dark_canary.py \
  --build-result docs/refactor/evidence/phase-08/build-result.json \
  --private-root /home/ubuntu/workspace/phase8-canary-private \
  --result docs/refactor/evidence/phase-08/dark-canary-result.json
```

Expected: three flows, Cloudbeds/Bókun reads positive, zero provider write,
zero delivery, zero live state/profile mounts.

- [ ] **Step 6: Validate and commit**

```bash
python3 -B scripts/generate_phase8_manifest.py --write
python3 -B scripts/validate_phase8.py --gate dark
git add scripts/run_phase8_dark_canary.py tests/test_phase8_dark_canary.py \
  docs/refactor/evidence/phase-08
git commit -m "test(phase8): prove zero-effect dark canary [skip ci]"
git push origin phase8-shadow-canary-rollout
```

---

### Task 8: Independent dark-canary review and snapshot authentication

**Files:**
- Create: `docs/refactor/evidence/phase-08/dark-review-result.json`
- Create: `docs/refactor/evidence/phase-08/dark-review-reports/lane-{1,2,3}.txt`
- Modify: `docs/refactor/evidence/phase-08/README.md`

**Interfaces:**
- Consumes: exact frozen image/build/dark snapshot.
- Produces: 3/3 authenticated `Approved` or invalidates the snapshot.

- [ ] **Step 1: Freeze identities and dispatch three non-overlapping lanes**

Lanes:

1. source/image/archive/rollback identity;
2. env/mount/state isolation and zero effects;
3. three-flow semantics, provider-read provenance and live non-drift.

Each summary requires sections `Authentication`, `Verdict`, `Findings`,
`Residual risks`; verdict is exactly `Approved` or `Needs fixes`. Timeout,
missing summary or wrong identity counts zero.

- [ ] **Step 2: Authenticate full summaries and hashes**

Copy complete summaries into `dark-review-reports/`; do not reconstruct truncated
text. `dark-review-result.json` binds report hashes, bytes, snapshot commit/tree,
image ID and archive SHA.

- [ ] **Step 3: Handle verdict**

If any lane is `Needs fixes`, retain a causal RED, fix only the demonstrated
finding and invalidate the image if its bytes or effective behavior changed.
If all are `Approved`, continue.

- [ ] **Step 4: Validate/publish**

```bash
python3 -B scripts/generate_phase8_manifest.py --write
python3 -B scripts/validate_phase8.py --gate dark
git add docs/refactor/evidence/phase-08
git commit -m "docs(phase8): approve dark canary snapshot [skip ci]"
git push origin phase8-shadow-canary-rollout
```

---

### Task 9: Expose the same image on a closed canary ingress

**Files:**
- Create: `scripts/run_phase8_ingress_canary.py`
- Create: `tests/test_phase8_ingress.py`
- Create after deploy: `docs/refactor/evidence/phase-08/ingress-result.json`
- Modify: `phase8_release/docker_runtime.py`
- Modify: `docs/refactor/evidence/phase-08/red-results.json`

**Interfaces:**
- Produces a canary service on the existing domain under prefix
  `/phase8-canary`, same image ID, isolated state/profile, outbound and writes
  closed.

- [ ] **Step 1: Write ingress topology REDs**

```python
class Phase8IngressTests(unittest.TestCase):
    def test_ingress_route_is_prefix_scoped_and_stripped(self) -> None:
        command = ingress_container_command(canary_fixture())
        material = "\n".join(command)
        self.assertIn("PathPrefix(`/phase8-canary/health`)", material)
        self.assertIn("PathPrefix(`/phase8-canary/webhook/manychat`)", material)
        self.assertIn("stripprefix", material.casefold())
        self.assertNotIn("PathPrefix(`/`)", material)

    def test_ingress_env_still_has_zero_delivery_and_zero_provider_write(self) -> None:
        validate_dark_env(ingress_env_fixture())
```

- [ ] **Step 2: RED/GREEN**

```bash
python3 -B -m unittest tests.test_phase8_ingress -v \
  >/tmp/phase8-task9-red.out 2>&1
# implement the two exact Traefik routers, strip-prefix middleware and service
python3 -B -m unittest tests.test_phase8_ingress -v
```

- [ ] **Step 3: Deploy only the canary container**

```bash
python3 -B scripts/run_phase8_ingress_canary.py deploy \
  --build-result docs/refactor/evidence/phase-08/build-result.json \
  --private-root /home/ubuntu/workspace/phase8-canary-private \
  --prefix /phase8-canary
```

The script must assert `docker inspect chapada-leads-hermes` image/start/restarts
before and after. It never invokes the production deploy script.

- [ ] **Step 4: Verify public boundary with outbound closed**

```bash
python3 -B scripts/run_phase8_ingress_canary.py verify-boundary \
  --base-url https://leads-hermes.chapadabackpackers.com/phase8-canary \
  --result docs/refactor/evidence/phase-08/ingress-result.json
```

Verify health image/release/gates, webhook without secret `401`, synthetic webhook
with private canary secret `200`, no ManyChat send, no provider write and isolated
state. Set `manychat_real_ingress_observed=false`; direct HTTP does not claim
ManyChat provenance.

- [ ] **Step 5: Validate and commit**

```bash
python3 -B scripts/generate_phase8_manifest.py --write
python3 -B scripts/validate_phase8.py --gate ingress
git add phase8_release scripts tests docs/refactor/evidence/phase-08
git commit -m "feat(phase8): expose closed canary ingress [skip ci]"
git push origin phase8-shadow-canary-rollout
```

---

### Task 10: Prepare the human conversation gate and notify Carlos

**Files:**
- Create: `docs/refactor/evidence/phase-08/conversation-readiness.json`
- Create private: `/home/ubuntu/workspace/phase8-canary-private/<release-id>/conversation.env`
- Modify: `scripts/prepare_phase8_canary.py`
- Modify: `scripts/run_phase8_ingress_canary.py`
- Modify: `tests/test_phase8_canary_env.py`
- Modify: `tests/test_phase8_ingress.py`

**Interfaces:**
- Produces: same canary image with ManyChat outbound enabled only for one private
  allowlisted subscriber, every provider/payment write closed, clean canary state,
  and a user-facing test brief.

- [ ] **Step 1: Write conversation-mode REDs**

```python
class ConversationModeTests(unittest.TestCase):
    def test_conversation_env_opens_only_manychat_delivery(self) -> None:
        env = build_conversation_env(live_like_env_fixture(), allowed_subscriber="private")
        self.assertEqual(env["HERMES_LEADS_MODE"], "live")
        self.assertEqual(env["HERMES_LEADS_DRY_RUN"], "false")
        self.assertEqual(env["HERMES_LEADS_ALLOW_LIVE_SENDS"], "true")
        self.assertEqual(env["MANYCHAT_LIVE_ALLOWED_SUBSCRIBER_IDS"], "private")
        self.assertEqual(env["HERMES_DEBOUNCE_SECONDS"], "0")
        self.assertEqual(env["HERMES_AUTO_FLUSH_ENABLED"], "false")
        self.assertEqual(env["HERMES_PUBLIC_OUTBOX_AUTO_FLUSH_ENABLED"], "true")
        self.assertEqual(env["HERMES_POST_PAYMENT_OUTBOX_WORKER_ENABLED"], "false")
        for key in PROVIDER_WRITE_BOOLEAN_KEYS:
            self.assertEqual(env[key], "false", key)
        self.assertNotIn("SUPABASE_URL", env)
        self.assertNotIn("REDIS_URL", env)
```

- [ ] **Step 2: RED/GREEN focused**

```bash
python3 -B -m unittest \
  tests.test_phase8_canary_env.ConversationModeTests \
  tests.test_phase8_ingress -v \
  >/tmp/phase8-task10-red.out 2>&1
# implement build_conversation_env and exact mode transition
python3 -B -m unittest \
  tests.test_phase8_canary_env.ConversationModeTests \
  tests.test_phase8_ingress -v
```

- [ ] **Step 3: Audit and clean only canary state**

Remove the canary state files, canary cloned `state.db`, canary outboxes and canary
shadow log. Do not touch provider bookings/payments, live Supabase/Redis, live
Hermes sessions or `/home/ubuntu/chapada-leads-hermes-state`.

Use ManyChat API only to set the exact test-flow operational custom fields for
the authorized contact to JSON `null`. Save a private rollback manifest with
field IDs and pre-value hashes, not values or PII.

- [ ] **Step 4: Recreate the canary with same image ID and verify readiness**

Effective runtime must show:

```text
mode=live
dry_run=false
live_replies_enabled=true
allowlist_count=1
debounce_quiet_window_seconds=0
public_outbox_auto_flush_enabled=true
post_payment_outbox_worker_enabled=false
cloudbeds_readonly_ready=true
bokun_readonly_ready=true
all provider/payment write_ready=false
state backend isolated local
idempotency/debounce isolated local
image_id=<Gate A exact ID>
```

No test message is sent by the controller.

- [ ] **Step 5: Write readiness evidence**

`conversation-readiness.json` includes image ID, release commit, health hash,
allowlist count `1`, zero private identifier, state-clean checks, write-ready map,
expected endpoint and `ready_for_carlos=true`.

- [ ] **Step 6: STOP and notify Carlos**

Tell Carlos explicitly that the test moment arrived. Provide this natural matrix:

1. hostel request with dates/adults split across turns;
2. tour request where Maya chooses canonical ID privately;
3. package or correction/change of idea;
4. natural confirmation up to the summary, but do not authorize a real booking;
5. payment or handoff question without financial/provider write.

State the stop phrase: if Maya claims a reservation/payment, repeats confirmation,
loses known data, exposes IDs/technical terms, or replies from a legacy route,
stop the conversation and report the exact turn. Wait for Carlos before Task 11.

---

### Task 11: Authenticate Carlos's real conversation results

**Files:**
- Create after user test: `docs/refactor/evidence/phase-08/conversation-result.json`
- Create: `docs/refactor/evidence/phase-08/conversation-report.json`
- Modify: `docs/refactor/evidence/phase-08/README.md`

**Interfaces:**
- Consumes: real ManyChat events/replies from the authorized canary route.
- Produces: authenticated conversation GO/NO-GO without raw text/PII.

- [ ] **Step 1: Correlate provenance**

For each tested turn, authenticate webhook timestamp/message hash, canary container
image ID, Hermes `leads` session ID hash, route, tool/action classes, state before/
after and ManyChat send result. Prove `manychat_real_ingress_observed=true` and no
production/legacy response.

- [ ] **Step 2: Evaluate the matrix**

Require state continuity, one natural confirmation, ID privacy, no internal tool
vocabulary, no prohibited write and no false provider/payment claim. Every tested
scenario remains in the denominator.

- [ ] **Step 3: Handle failure or approval**

On failure, write one causal RED reproducer, patch only the demonstrated owner,
run focused GREEN and invalidate the image if behavior bytes changed. Do not rerun
the real commercial path before RCA/review.

On success, write closed `conversation-result.json` with
`result="passed_by_human_and_authenticated"` and no raw message content.

- [ ] **Step 4: Validate/review/publish**

```bash
python3 -B scripts/generate_phase8_manifest.py --write
python3 -B scripts/validate_phase8.py --gate conversation
git add docs/refactor/evidence/phase-08
git commit -m "test(phase8): authenticate human conversation canary [skip ci]"
git push origin phase8-shadow-canary-rollout
```

Then ask separately for the Task 12 provider/workflow authorization.

---

### Task 12: Execute one explicitly authorized provider-write canary

**Files:**
- Create only after authorization: `docs/refactor/evidence/phase-08/e2e-authorization.json`
- Create after run: `docs/refactor/evidence/phase-08/e2e-canary-result.json`
- Create: `tests/test_phase8_e2e_scope.py`
- Modify: `phase8_release/canary_env.py`
- Modify: `scripts/run_phase8_ingress_canary.py`

**Interfaces:**
- Consumes exact user choice: provider, workflow, reservation target/window and
  cancellation plan.
- Produces one reservation at most, read-back, one public outbox message and replay
  proof.
- Produces `build_e2e_env(closed: Mapping[str, str], authorization: E2EAuthorization) -> dict[str, str]`.

- [ ] **Step 1: Require exact authorization; do not infer a default**

Execution stops unless an authorization JSON contains exactly one provider and
one workflow:

The schema accepts `provider` only from `{"cloudbeds", "bokun"}` and accepts
`workflow` only from `{"cloudbeds_reservation", "bokun_reservation"}`. The
executable file must also contain `subscriber_count=1`, `reservation_limit=1`,
`payment_enabled=false`, a non-empty ISO date/window, and
`cancellation_plan_confirmed=true`. No enum value is selected automatically; the
file is rejected until the user's concrete choice is recorded.

- [ ] **Step 2: Write scope REDs**

```python
class Phase8E2EScopeTests(unittest.TestCase):
    def test_scope_opens_only_selected_provider_workflow(self) -> None:
        env = build_e2e_env(closed_conversation_env(), authorization_fixture("cloudbeds"))
        self.assertEqual(env["HERMES_CLOUDBEDS_WRITE_ENABLED"], "true")
        self.assertEqual(env["HERMES_BOKUN_CART_WRITE_ENABLED"], "false")
        self.assertEqual(env["HERMES_BOKUN_RESERVATION_WRITE_ENABLED"], "false")
        self.assertEqual(env["HERMES_STRIPE_PAYMENT_LINK_WRITE_ENABLED"], "false")
```

For a Bókun authorization, only cart+reservation gates may be true because they
form one Bókun reservation workflow; every Cloudbeds/payment gate remains false.

- [ ] **Step 3: Execute the exact E2E sequence once**

Natural conversation → summary → one confirmation → one command → one dispatch →
provider read-back → one outbox delivery → redelivery of the same webhook with
zero new command/provider/outbox effect → planned cancellation/audit.

- [ ] **Step 4: Stop on any uncertainty**

Timeout after possible dispatch is `called_unknown/manual_review`. Do not retry,
cancel blindly or run a second reservation.

- [ ] **Step 5: Review and publish**

Require independent financial/provider, idempotency and provenance lanes. Then:

```bash
python3 -B scripts/validate_phase8.py --gate e2e
python3 -B scripts/generate_phase8_manifest.py --write
git add docs/refactor/evidence/phase-08 tests/test_phase8_e2e_scope.py \
  phase8_release/canary_env.py scripts/run_phase8_ingress_canary.py
git commit -m "test(phase8): authenticate single provider canary [skip ci]"
```

---

### Task 13: Promote the same image gradually with tested rollback

**Files:**
- Create: `scripts/promote_phase8_image.py`
- Create: `tests/test_phase8_promotion.py`
- Create per started stage: `docs/refactor/evidence/phase-08/rollout-result-<stage>.json`
- Modify: `docs/refactor/evidence/phase-08/README.md`

**Interfaces:**
- Produces `PromotionPlan(candidate_image_id, rollback_image_id, stage, max_conversations, min_observation_seconds)` and a runner that never builds/pulls.
- Produces `promotion_commands(plan: PromotionPlan) -> tuple[tuple[str, ...], ...]`.

- [ ] **Step 1: Write promotion/rollback REDs**

```python
class Phase8PromotionTests(unittest.TestCase):
    def test_promotion_never_builds_or_pulls_and_requires_exact_ids(self) -> None:
        commands = promotion_commands(plan_fixture())
        material = "\n".join(" ".join(row) for row in commands)
        self.assertNotIn(" build ", f" {material} ")
        self.assertNotIn(" pull ", f" {material} ")
        self.assertIn(CANDIDATE_IMAGE_ID, material)
        self.assertIn(ROLLBACK_IMAGE_ID, material)

    def test_each_stage_requires_prior_green_and_explicit_go(self) -> None:
        with self.assertRaisesRegex(ValueError, "prior stage not green"):
            PromotionPlan.for_stage("5%", prior_result="blocked", explicit_go=True)
```

- [ ] **Step 2: Implement exact stage table**

```python
STAGES = {
    "1%": (100, 24 * 60 * 60),
    "5%": (300, 24 * 60 * 60),
    "25%": (1000, 48 * 60 * 60),
    "100%": (None, 48 * 60 * 60),
}
```

The promoter retags the already-loaded candidate image under the compose-expected
name, verifies the tag resolves to the same ID, runs the existing compose with
`--no-build`, checks health/release/image, and automatically retags/recreates the
rollback image on failed health. It never calls the current deploy script because
that script contains `--build`.

- [ ] **Step 3: Test rollback without changing production**

Use fake runner unit tests plus a disposable container/tag rehearsal. Do not point
the rehearsal at `chapada-leads-hermes`.

- [ ] **Step 4: Require explicit GO per live stage**

Before each stage, present the prior metrics and ask the owner. Record decision,
time window, conversation count, errors/handoff/timeouts, image ID and rollback
readiness. Any stop condition rolls back immediately.

- [ ] **Step 5: Validate each stage**

```bash
python3 -B scripts/validate_phase8.py --gate rollout
```

A partial rollout is not called Phase 8 complete unless the owner explicitly
accepts that terminal stage and reason in the phase document.

---

### Task 14: Terminal review, remote CI and Phase 8 closeout

**Files:**
- Create: `docs/refactor/evidence/phase-08/review-result.json`
- Create: `docs/refactor/evidence/phase-08/ci-result.json`
- Create: `docs/refactor/evidence/phase-08/review-reports/lane-{1,2,3}.txt`
- Modify: `docs/refactor/phases/phase-08-shadow-canary-rollout.md`
- Modify: `docs/refactor/README.md`
- Modify: `docs/refactor/06-risk-register.md`
- Modify: `docs/refactor/evidence/phase-08/README.md`

**Interfaces:**
- Produces terminal 3/3 review, authenticated CI and published phase closeout;
  `phase9_started=false` remains until a later explicit decision.

- [ ] **Step 1: Freeze terminal snapshot and run one final local gate**

```bash
python3 -B -m unittest discover -s tests -v
for n in 0 1 2 3 4 5 6; do python3 -B "scripts/validate_phase${n}.py"; done
python3 -B scripts/validate_phase7.py --terminal
python3 -B scripts/generate_phase8_manifest.py --check
python3 -B scripts/validate_phase8.py --gate terminal
git diff --check
```

- [ ] **Step 2: Obtain terminal 3/3 on the same snapshot**

Lanes: image/rollback, safety/effects, ingress/conversation/E2E/rollout
provenance. Verdicts and authentication format follow Task 8.

- [ ] **Step 3: Publish terminal candidate and authenticate CI**

Push one terminal SHA without `[skip ci]`. Authenticate the Phase 8 workflow head,
all declared jobs and artifacts. `ci-result.json` binds that exact SHA/run.

- [ ] **Step 4: Write documentary closeout and publish with `[skip ci]`**

Set Phase 8 concluded, risks updated, final image/rollback/run/review identities,
and `phase9_started=false`. Regenerate prior affected manifests in owner order,
then Phase 8 manifest last.

- [ ] **Step 5: Verify remote and preserve rollback**

```bash
git push origin phase8-shadow-canary-rollout
git fetch origin phase8-shadow-canary-rollout
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/phase8-shadow-canary-rollout)"
python3 -B scripts/validate_phase8.py --gate terminal
```

Do not merge/start Phase 9 automatically. Present closeout and next decision.
