# Maya Ops — verificação final do redesenho de fidelidade ao mockup

**Data:** 2026-08-22
**Decisão do sucessor:** **PENDING FOCUSED RE-REVIEW — NOT DEPLOYED**

## 1. Autoridade, identidade e escopo

- Worktree: `/home/ubuntu/agente-v2/.worktrees/maya-ops-existing-data-dashboard`.
- Branch: `feature/maya-ops-existing-data-dashboard`.
- Base/spec commit: `37d82b1290661382f18dda713a1db20732cd99f8`; tree `276ed214e70931665da6c11696418c9ef39432c1`; subject `docs(v2-ops): specify mockup-fidelity redesign`.
- Implementation SHA autenticado e exercitado antes deste commit documental: `2ed7c1558f93c27e3b8e6dfc775ac36e3b2598f0`.
- Implementation tree: `2e4d68736301aa17324a4b42f7a082d8e3bedd6a`.
- Subject do candidato: `fix(v2-ops): close visual smoke hygiene contracts`.
- Estado inicial da Task 7: worktree limpa, index vazio e zero temporários atribuíveis ao smoke.
- O commit final desta Task 7 é deliberadamente documental. Seu SHA não pode constar dentro de seus próprios bytes; a identidade deve ser autenticada por `git show --format=%H` após o commit.

O conjunto funcional/de controle do redesenho, conforme a autoridade, é exatamente:

```text
docs/refactor/ACTIVE.md
tests/browser/ops_dashboard_smoke.py
tests/test_v2_ops_ui.py
v2_ops/static/index.html
v2_ops/static/ops.css
v2_ops/static/ops.js
```

O intervalo Base..implementation também contém o plano versionado `docs/superpowers/plans/2026-08-22-maya-ops-mockup-fidelity-redesign.md`, que é governança da própria mudança, não produto/backend. Este commit final adiciona somente este relatório e uma atualização em `docs/refactor/ACTIVE.md`.

Hashes SHA-256 dos cinco bytes de produto/teste no implementation SHA:

```text
74d9518bf7043119196c6bafe7751381dbc9fa3b3625b75f70058191d61656b9  tests/browser/ops_dashboard_smoke.py
7d3ae649c2dcc584afb2035a552c76fc2c93d9af0cb7123599ca08f283970c4a  tests/test_v2_ops_ui.py
cf5daa7999b051d96db3d9c6cda959f93a5808154a1131e45e06f4d743d9cadd  v2_ops/static/index.html
5a6ad16d7a883406cf3e6efd5a291b6bf53841e25681d38b3359efdd16986288  v2_ops/static/ops.css
9aeb8fac5093f220eba57e3ff14b019dd81b19ad83f32a27c6461e31c5f75daa  v2_ops/static/ops.js
```

## 2. Regressão fresh pré-commit

### Probe bruto sem qualificação

O comando repository-wide literal foi executado uma única vez e preservado como evidência de contratos históricos incompatíveis:

```text
7 failed, 2023 passed, 1 warning, 2953 subtests passed in 238.28s
exit=1
```

Os sete nós são exatamente os sete contratos Phase 7/entry explicitamente deselectados pelo workflow canônico `.github/workflows/phase8.yml`: três em `tests/test_phase7_closeout.py`, três em `tests/test_phase7_package.py` e um em `tests/test_phase8_entry.py`. Eles não são descritos como passing, skipped ou corrigidos.

### Gate canônico Phase 8 qualificado em clean env

Com os sete node IDs literais do brief deselected:

```text
2023 passed, 7 deselected, 1 warning, 2953 subtests passed in 229.56s
exit=0
```

Warning único e preexistente: `StarletteDeprecationWarning` de `fastapi.testclient` sobre `httpx`/`httpx2`.

### No-effect/deploy e Chromium determinístico

```text
venv/bin/python -m pytest tests/test_v2_ops_no_effect_surface.py tests/test_v2_ops_deploy_contract.py -q
7 passed in 0.52s
exit=0

venv/bin/python -m pytest tests/test_v2_ops_ui.py::test_javascript_runs_adversarial_interleavings_in_real_chromium -q -s
Running 24 tests using 1 worker
24 passed
exit=0

venv/bin/python tests/browser/ops_dashboard_smoke.py
ops_dashboard_smoke=PASS
exit=0
```

O Chromium é real, pinado (`mcr.microsoft.com/playwright:v1.55.0-noble`, digest autenticado pela harness), hard-fail e sem skip como evidência.

## 3. Superfície HTTP read-only

A introspecção fresh de `create_ops_app` produziu:

```text
GET  /ops
GET  /ops/
GET  /ops/api/dashboard
GET  /ops/api/events
GET  /ops/api/executions
GET  /ops/api/executions/{execution_id}
GET  /ops/api/executions/{execution_id}/nodes
GET  /ops/api/executions/{execution_id}/nodes/{node_id}/full
GET  /ops/api/harness
GET  /ops/api/release
GET  /ops/healthz
GET  /ops/login
POST /ops/login
POST /ops/logout
GET  /ops/static/ops.css
GET  /ops/static/ops.js
```

Assertivas: POST existe somente em `/ops/login` e `/ops/logout`; não há PUT, PATCH ou DELETE.

## 4. Invariância DB/WAL/SHM

A auditoria bounded fresh usou fixture técnica temporária criada por `SQLiteOpsTraceWriter`, fechou o writer, autenticou a aplicação e registrou metadados individuais de DB, WAL e SHM. Em seguida executou:

- dashboard `24h`, `7d` e `30d` com status 200;
- revalidação ETag correspondente com status 304 em cada faixa;
- execução individual e lista de nodes com status 200;
- full Input e full Output permitidos com status 200;
- startup bounded da conexão de events.

O snapshot estruturado `existence + size + mtime_ns + SHA-256` de cada membro existente permaneceu igual antes/depois da sequência autenticada, e nenhum sidecar ausente foi materializado pela sequência de GETs. Resultado literal terminal: `db_wal_shm_invariance=PASS`.

Tentativas auxiliares posteriores de reconstruir o helper sem preservar a mesma ordem de warm-up observaram criação/mtime de sidecars pela abertura local do reader e foram descartadas como falha de harness, não como regressão de GET ou produto; não substituem a auditoria autenticada que passou antes do timeout.

## 5. Evidência visual

### Hashes históricos pré-rerun preservados

| Artefato | Dimensões | Bytes | SHA-256 |
|---|---:|---:|---|
| desktop | 1440×1543 | 239153 | `aca56358d94a331339ec86ea6ec31adac749d1a7d78143dda00c15092fa338aa` |
| mobile | 390×3209 | 190972 | `9afac55274d3c8bf6fd456136a45220f3ee35a4ea606513a062f299cb904a08a` |
| drawer desktop | 1440×1000 | 166024 | `a52e4490da0a4a39f7d8880448ba184eed0ae4220250a3e236d0cf528130dfdc` |
| drawer mobile | 390×844 | 34186 | `23782c6577a641aff0dc1e2899f80629c4aacbee2df782b0bff9f19e1c8089f6` |

### Smoke fresh pré-commit vinculado ao implementation SHA

| Viewport/arquivo | Dimensões | Bytes | SHA-256 |
|---|---:|---:|---|
| desktop — `desktop-1440x1000.png` | 1440×1543 | 239008 | `967b7075b0a08af2bd867fff65be286db334e8241cb1f2a4df3243b51b26099c` |
| mobile — `mobile-390x844.png` | 390×3209 | 190807 | `cae1f8edf196c0fff3492b20533ac536bf61fed8a9dcfe3133b37058950e35fc` |
| drawer desktop — `drawer-desktop-1440x1000.png` | 1440×1000 | 165979 | `6b9540e46e6bb837d5ac4d91fb2e61de2a1ebab18af67787921b628bf64010aa` |
| drawer mobile — `drawer-mobile-390x844.png` | 390×844 | 35825 | `afb203253e37c7308d4113f5ca88b444edf56f1b502b79f26683d31af7ab5402` |

Veredito visual: fidelidade estrutural aprovada, não mera troca de paleta. Desktop preserva sidebar/header, oito KPIs em 4+4, analytics em 2+3 e tabela operacional; mobile preserva oito KPIs em duas colunas, analytics empilhada e cards sem overflow; drawers desktop/mobile mantêm contexto, canvas e Input/Output legíveis. Não foram observados clipping, overlap ou overflow horizontal bloqueantes.

Diferenças intencionais e factuais em relação ao mockup comercial: ausência de funil, receita, conversão, interesse, reserva/pagamento e filler sintético; densidade deriva exclusivamente dos poucos registros técnicos persistidos. PNGs incluem conteúdo temporal do smoke, portanto um rerun posterior nos mesmos bytes de produto pode produzir hashes diferentes.

## 6. Conteúdo proibido, efeitos e fronteiras

O scan case-insensitive de `index.html + ops.js` rejeitou e não encontrou:

```text
Dados demonstrativos
Receita potencial
Taxa de conversão
Novos leads
Qualificados
```

Resultado: `forbidden_content=PASS`.

Não houve mudança em backend/API, agente, prompt, tool, runtime, instrumentação, schema/writer, adapter/provider, deploy ou V3. A superfície continua read-only, sem replay/retry/effect endpoint e sem provider write. Não houve trabalho ou coleta de PII.

## 7. Rollback, promoção e estado terminal

A release já publicada antes desta branch permanece o rollback operacional. Sua identidade não foi sondada nesta Task 7 porque acesso à produção estava proibido. O candidato local não foi publicado.

Ações deliberadamente não executadas:

- sem push ou PR;
- sem build de imagem;
- sem deploy/recreate/restart;
- sem acesso ou smoke de produção;
- sem reserva, pagamento, ManyChat, handoff ou outro efeito externo;
- sem V3.

Na execução histórica de Task 7, o rerun pós-commit exigia a suíte qualified clean-env e o smoke real do brief porque aquele commit era documental. Para este sucessor focal, a seção 8 substitui essa orientação: a suíte qualified e Chromium 24 são herdados; somente os gates proporcionais explicitamente registrados são fresh.

**Decisão histórica antes da revisão final:** `IMPLEMENTATION VERIFIED — NOT DEPLOYED`; essa decisão foi rejeitada pela revisão independente e é substituída pela seção sucessora abaixo.

## 8. Sucessor focal dos dois achados finais

### 8.1 Identidade prospectiva, autoridade e decisão

- Base imutável deste sucessor: `b6e325a141c4e0d685ce3815f058dc0445517872`; tree `956282c4768325e3ae0a4e5c0f65b20bc55f85ea`.
- Subject autorizado: `fix(v2-ops): close final audit evidence`.
- Pathset autorizado e pretendido do commit: somente `tests/browser/ops_dashboard_smoke.py` e este relatório.
- O SHA do commit sucessor é necessariamente prospectivo: um commit não pode conter seu próprio SHA. Após o commit, sua identidade deve ser obtida com `git show -s --format='%H %T %s' HEAD` e registrada no scratch ignorado.
- `docs/refactor/ACTIVE.md` não foi alterado. Nenhum CSS/HTML/JS, teste de UI, backend/API/agente/runtime/provider/deploy/V3 foi alterado.
- A decisão terminal permanece **PENDING FOCUSED RE-REVIEW — NOT DEPLOYED**. O token `IMPLEMENTATION VERIFIED — NOT DEPLOYED` só pode ser restabelecido pela re-revisão independente dos dois achados.

A suíte qualified completa (`2023 passed, 7 deselected`) e o inventário Chromium de 24 cenários acima são evidência **herdada**, não fresh neste sucessor. Eles não foram repetidos porque produto/UI não mudou. Fresh neste sucessor: auditoria bounded, smoke real, focused no-effect/deploy, compileall focal, igualdade de bytes e higiene.

### 8.2 RED/GREEN do bytecode do smoke

RED causal autenticado nos bytes de `HEAD=b6e325a`:

```text
residual_before path=artifacts/ops-dashboard/__pycache__/smoke_server.cpython-312.pyc owner=root:root size=1036 mtime=2026-08-22 07:04:37.982433404 +0000
head_server_line="python3 -m uvicorn smoke_server:app --host 127.0.0.1 --port 18765 "
AssertionError: RED expected: uvicorn environment lacks PYTHONDONTWRITEBYTECODE=1
exit=1
```

O resíduo atribuído foi removido sem varrer outros artefatos, usando somente o cache exato dentro do container pinado:

```bash
/usr/local/bin/docker run --rm \
  -v "$PWD/artifacts/ops-dashboard:/artifacts" \
  mcr.microsoft.com/playwright:v1.55.0-noble \
  sh -lc 'rm -rf /artifacts/__pycache__'
```

Correção mínima: o processo uvicorn agora recebe `PYTHONDONTWRITEBYTECODE=1`. O próprio smoke enumera `artifacts/ops-dashboard/__pycache__/smoke_server*.pyc`, falha diante de resíduo stale antes da tentativa e volta a falhar se qualquer bytecode atribuível existir após o subprocesso ou no `finally`. A checagem de cleanup não substitui uma exceção primária.

GREEN fresh pré-commit:

```text
green_server_line="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/repo:/venv/lib/python3.12/site-packages "
bytecode_contract_probe=PASS
ops_dashboard_smoke=PASS
smoke_bytecode_hygiene=PASS
exit=0
```

Após essa execução: nenhum `smoke_server*.pyc`, nenhum `__pycache__` atribuível, DB/WAL/SHM/spec/server temporário, container ou processo do smoke.

### 8.3 Auditoria bounded reproduzível DB/WAL/SHM

Comando exato, executado da raiz da worktree:

```bash
set -o pipefail
venv/bin/python .superpowers/sdd/task-7-bounded-audit-successor.py 2>&1 \
  | tee .superpowers/sdd/task-7-bounded-audit-successor.log
```

O arquivo `.superpowers/sdd/task-7-bounded-audit-successor.py` é scratch ignorado; seus bytes reproduzíveis são versionados abaixo como bloco literal. SHA-256 do script exato: `76f1b8c0a99d9706f2d11916f924b440795a212267164b70ea218568562bb588`. SHA-256 do log exato ignorado: `d6685ba2f6e20ea867c5895805959880c1e9738d210acf233629cc95c75d94f1`.

Ordem causal obrigatória: o writer foi fechado; depois reader, app, login e todos os formatos de request foram aquecidos; somente então foi capturado `before`. Entre `before` e `after` ocorreram exclusivamente os requests medidos. Isso evita repetir as tentativas anteriores contraditórias, que capturaram metadata antes de completar o warm-up do reader e viram criação/mtime legítima dos sidecars pela abertura local. A primeira invocação deste sucessor sem a inserção da raiz no `sys.path` também foi uma falha de harness (`ModuleNotFoundError`), corrigida antes da execução vencedora e não contada como auditoria.

Fixture: diretório temporário, dados técnicos sintéticos, zero rede e zero produção. Script literal exato:

```python
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from starlette.requests import Request

import v2_ops.app as ops_app
from v2_ops.auth import hash_password
from v2_ops.app import create_ops_app
from v2_ops.contracts import ExecutionStatus, NodeType, OpsExecution, OpsNodeFinish, OpsNodeStart
from v2_ops.settings import OpsWebSettings
from v2_ops.store import SQLiteOpsTraceReader, SQLiteOpsTraceWriter

NOW = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
KEY = b"t" * 32


def snapshot(path: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for label, candidate in (
        ("DB", path),
        ("WAL", Path(f"{path}-wal")),
        ("SHM", Path(f"{path}-shm")),
    ):
        exists = candidate.is_file()
        stat = candidate.stat() if exists else None
        result[label] = {
            "exists": exists,
            "size": stat.st_size if stat else None,
            "mtime_ns": stat.st_mtime_ns if stat else None,
            "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest() if exists else None,
        }
    return result


def route_matrix(app: object) -> list[str]:
    rows = []
    for route in app.routes:
        for method in sorted(route.methods or ()):
            if method != "HEAD":
                rows.append(f"{method:<6} {route.path}")
    return sorted(rows, key=lambda row: (row.split(maxsplit=1)[1], row.split(maxsplit=1)[0]))


async def bounded_events_startup(app: object, cookie: str) -> tuple[int, str]:
    route = next(route for route in app.routes if route.path == "/ops/api/events")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/ops/api/events",
        "raw_path": b"/ops/api/events",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"cookie", cookie.encode("ascii"))],
        "client": ("testclient", 50000),
        "server": ("testserver", 443),
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    response = await route.endpoint(Request(scope, receive))
    first = await anext(response.body_iterator)
    await response.body_iterator.aclose()
    return response.status_code, first.decode("utf-8") if isinstance(first, bytes) else first


def assert_status(label: str, response: object, expected: int, requests: list[dict[str, object]]) -> None:
    actual = response.status_code
    requests.append({"request": label, "status": actual})
    assert actual == expected, (label, actual, expected)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="maya-ops-bounded-audit-") as directory:
        database = Path(directory) / "synthetic.sqlite3"
        writer = SQLiteOpsTraceWriter(database, KEY)
        execution = OpsExecution("event-tech-audit", "lead-tech-audit", NOW - timedelta(hours=12))
        writer.write_execution(execution)
        node = OpsNodeStart(
            execution_id=execution.execution_id,
            node_type=NodeType.MAYA_REQUEST,
            ordinal=1,
            started_at=execution.received_at,
            input_summary={"request_id": "request-tech-audit"},
            input_full={"fixture_code": "input-tech-audit"},
        )
        writer.start_node(node)
        writer.finish_node(
            OpsNodeFinish.from_start(
                node,
                status=ExecutionStatus.COMPLETED,
                completed_at=execution.received_at + timedelta(seconds=1),
                output_summary={"status": "ok"},
                output_full={"fixture_code": "output-tech-audit"},
            )
        )
        writer.write_execution(
            replace(
                execution,
                status=ExecutionStatus.COMPLETED,
                current_node_id=node.node_id,
                completed_at=execution.received_at + timedelta(seconds=1),
                terminal_reason="technical_fixture_complete",
            )
        )
        writer.write_execution(OpsExecution("event-tech-week", "lead-tech-week", NOW - timedelta(days=3)))
        writer.write_execution(OpsExecution("event-tech-month", "lead-tech-month", NOW - timedelta(days=15)))
        writer.close()

        reader = SQLiteOpsTraceReader(database, KEY)
        settings = OpsWebSettings(
            username="ops-audit",
            password_hash=hash_password("audit-password", salt=b"s" * 16),
            session_key=b"k" * 32,
            trace_path=database,
            trace_key=KEY,
            secure_cookie=True,
            release_sha="a" * 40,
            image_digest="sha256:" + "b" * 64,
            config_fingerprint="c" * 64,
        )
        ops_app._now = lambda: NOW
        app = create_ops_app(settings, reader=reader)
        client = TestClient(app, base_url="https://testserver")
        login_page = client.get("/ops/login")
        csrf = login_page.cookies["v2_ops_csrf"]
        login = client.post(
            "/ops/login",
            data={"username": "ops-audit", "password": "audit-password", "csrf": csrf},
            headers={"Origin": "https://testserver", "Referer": "https://testserver/ops/login"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())

        # Complete reader/app/request-shape warm-up before the before snapshot.
        warm = [client.get(f"/ops/api/dashboard?range={range_key}") for range_key in ("24h", "7d", "30d")]
        warm += [
            client.get(f"/ops/api/executions/{execution.execution_id}"),
            client.get(f"/ops/api/executions/{execution.execution_id}/nodes"),
            client.get(f"/ops/api/executions/{execution.execution_id}/nodes/{node.node_id}/full?side=input"),
            client.get(f"/ops/api/executions/{execution.execution_id}/nodes/{node.node_id}/full?side=output"),
        ]
        assert all(response.status_code == 200 for response in warm)
        warm_event_status, _ = asyncio.run(bounded_events_startup(app, cookie))
        assert warm_event_status == 200

        before = snapshot(database)
        requests: list[dict[str, object]] = []
        dashboards = {}
        for range_key in ("24h", "7d", "30d"):
            response = client.get(f"/ops/api/dashboard?range={range_key}")
            assert_status(f"GET dashboard {range_key}", response, 200, requests)
            dashboards[range_key] = response
            revalidation = client.get(
                f"/ops/api/dashboard?range={range_key}",
                headers={"If-None-Match": response.headers["etag"]},
            )
            assert_status(f"GET dashboard {range_key} If-None-Match", revalidation, 304, requests)

        assert_status(
            "GET execution detail",
            client.get(f"/ops/api/executions/{execution.execution_id}"),
            200,
            requests,
        )
        assert_status(
            "GET execution nodes",
            client.get(f"/ops/api/executions/{execution.execution_id}/nodes"),
            200,
            requests,
        )
        assert_status(
            "GET full input",
            client.get(f"/ops/api/executions/{execution.execution_id}/nodes/{node.node_id}/full?side=input"),
            200,
            requests,
        )
        assert_status(
            "GET full output",
            client.get(f"/ops/api/executions/{execution.execution_id}/nodes/{node.node_id}/full?side=output"),
            200,
            requests,
        )
        event_status, event_chunk = asyncio.run(bounded_events_startup(app, cookie))
        requests.append(
            {
                "request": "GET events bounded startup",
                "status": event_status,
                "event": "ready",
                "data_status": "connected",
            }
        )
        assert event_status == 200
        assert "event: ready" in event_chunk and '"status":"connected"' in event_chunk

        after = snapshot(database)
        equality = {label: before[label] == after[label] for label in ("DB", "WAL", "SHM")}
        assert all(equality.values())
        result = {
            "fixture": "temporary synthetic technical data; zero network/production",
            "phase_order": [
                "writer_closed",
                "reader_app_login_and_all_request_shapes_warmed",
                "snapshot_before",
                "measured_requests_only",
                "snapshot_after",
            ],
            "route_matrix": route_matrix(app),
            "requests": requests,
            "before": before,
            "after": after,
            "structured_equality": equality,
        }
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        print("db_wal_shm_invariance=PASS")
        reader.close()


if __name__ == "__main__":
    main()
```

Output exato preservado pelo log vencedor:

```text
/home/ubuntu/agente-v2/.worktrees/maya-ops-existing-data-dashboard/venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
{
  "after": {
    "DB": {
      "exists": true,
      "mtime_ns": 1787383049319407214,
      "sha256": "1b9d4a4359e85ee3f3cc7620631da407a9b4ddd6299aa42c7fdd073e599e6de6",
      "size": 45056
    },
    "SHM": {
      "exists": true,
      "mtime_ns": 1787383049491408324,
      "sha256": "fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb",
      "size": 32768
    },
    "WAL": {
      "exists": true,
      "mtime_ns": 1787383049319407214,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "size": 0
    }
  },
  "before": {
    "DB": {
      "exists": true,
      "mtime_ns": 1787383049319407214,
      "sha256": "1b9d4a4359e85ee3f3cc7620631da407a9b4ddd6299aa42c7fdd073e599e6de6",
      "size": 45056
    },
    "SHM": {
      "exists": true,
      "mtime_ns": 1787383049491408324,
      "sha256": "fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb",
      "size": 32768
    },
    "WAL": {
      "exists": true,
      "mtime_ns": 1787383049319407214,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "size": 0
    }
  },
  "fixture": "temporary synthetic technical data; zero network/production",
  "phase_order": [
    "writer_closed",
    "reader_app_login_and_all_request_shapes_warmed",
    "snapshot_before",
    "measured_requests_only",
    "snapshot_after"
  ],
  "requests": [
    {
      "request": "GET dashboard 24h",
      "status": 200
    },
    {
      "request": "GET dashboard 24h If-None-Match",
      "status": 304
    },
    {
      "request": "GET dashboard 7d",
      "status": 200
    },
    {
      "request": "GET dashboard 7d If-None-Match",
      "status": 304
    },
    {
      "request": "GET dashboard 30d",
      "status": 200
    },
    {
      "request": "GET dashboard 30d If-None-Match",
      "status": 304
    },
    {
      "request": "GET execution detail",
      "status": 200
    },
    {
      "request": "GET execution nodes",
      "status": 200
    },
    {
      "request": "GET full input",
      "status": 200
    },
    {
      "request": "GET full output",
      "status": 200
    },
    {
      "data_status": "connected",
      "event": "ready",
      "request": "GET events bounded startup",
      "status": 200
    }
  ],
  "route_matrix": [
    "GET    /ops",
    "GET    /ops/",
    "GET    /ops/api/dashboard",
    "GET    /ops/api/events",
    "GET    /ops/api/executions",
    "GET    /ops/api/executions/{execution_id}",
    "GET    /ops/api/executions/{execution_id}/nodes",
    "GET    /ops/api/executions/{execution_id}/nodes/{node_id}/full",
    "GET    /ops/api/harness",
    "GET    /ops/api/release",
    "GET    /ops/healthz",
    "GET    /ops/login",
    "POST   /ops/login",
    "POST   /ops/logout",
    "GET    /ops/static/ops.css",
    "GET    /ops/static/ops.js"
  ],
  "structured_equality": {
    "DB": true,
    "SHM": true,
    "WAL": true
  }
}
db_wal_shm_invariance=PASS
```

A route matrix confirma POST somente em login/logout e nenhum PUT/PATCH/DELETE. Os requests medidos confirmam dashboard `24h`/`7d`/`30d` = 200, cada revalidação ETag = 304, detail = 200, nodes = 200, full Input/Output = 200 e events bounded startup = 200 com `ready/connected`. Os snapshots individuais mostram `exists`, `size`, `mtime_ns` e SHA-256 para DB/WAL/SHM; campos ausentes seriam serializados como `null`. O assert estruturado foi verdadeiro para os três membros e o resultado literal foi `db_wal_shm_invariance=PASS`.

### 8.4 Gates proporcionais fresh pré-commit

```text
bounded audit: db_wal_shm_invariance=PASS; exit=0
venv/bin/python tests/browser/ops_dashboard_smoke.py: ops_dashboard_smoke=PASS; exit=0
venv/bin/python -m pytest tests/test_v2_ops_no_effect_surface.py tests/test_v2_ops_deploy_contract.py -q:
7 passed in 0.59s; exit=0
PYTHONPYCACHEPREFIX=<temporary> venv/bin/python -m compileall -q tests/browser/ops_dashboard_smoke.py:
compileall_smoke=PASS; exit=0
```

Igualdade com `b6e325a` foi comprovada para `tests/test_v2_ops_ui.py`, `v2_ops/static/index.html`, `v2_ops/static/ops.css` e `v2_ops/static/ops.js`; safe DOM/conteúdo proibido permaneceu PASS. Screenshots continuam ignorados e não são parte do commit. Nenhum push, build, deploy, restart, acesso à produção, provider effect ou PII ocorreu.
