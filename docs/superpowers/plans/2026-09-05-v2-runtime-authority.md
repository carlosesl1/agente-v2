# Agente V2 Runtime Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar inequívocos e verificáveis o código, a imagem, o deploy, o estado e o rollback ativos de cada componente do Agente V2, corrigindo a identidade divergente do runtime GA sem mudar seu comportamento ou seus dados.

**Architecture:** Um verificador stdlib versionado no repositório valida um contrato host-local sem segredos em `/home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json`. GA, teste isolado e Maya Ops são componentes separados no contrato; branches `production/*` apontam aos commits exatos, imagens são fixadas por digest e qualquer divergência entre Git, OCI, Compose ou Docker causa falha fechada.

**Tech Stack:** Python 3.12 stdlib, pytest, Git/worktrees, Docker/Compose, registry OCI local, JSON, Markdown e HTTP readiness.

## Global Constraints

- Escopo estrito do Agente V2; não ler, editar, testar, reiniciar ou usar o V3 como referência.
- Repositório canônico: `/home/ubuntu/agente-v2`, remoto `carlosesl1/agente-v2`.
- `/home/ubuntu/chapada-leads-hermes` é legado/inativo e somente leitura.
- Não versionar segredo, PII, subscriber ID, payload real, banco, WAL/SHM ou log bruto.
- Não editar SQLite/WAL/SHM ativos manualmente.
- GA, teste isolado e Ops mantêm identidades próprias; não declarar um SHA único para os três.
- Código ativo exige concordância entre Git, bytes da imagem/container e configuração observada.
- A nova imagem GA usa exatamente a árvore funcional `b3173693d6d852ba8bcf8df5f7aa8e7a6d4c10f3` já observada, com identidade OCI corrigida.
- Maya Ops permanece no commit `039dd1a3c0a93e6b4e2e5192ba9da23c4f2977fb` e não é reiniciado.
- Preservar estados, Hermes home, auditoria, imagens anteriores, composes versionados e bundles de rollback.
- O verificador é read-only, sanitizado e fail-closed.
- O endpoint de prontidão do V2 é `/readyz`; redirecionamentos e smoke público usam `GET`.

---

## File map

### Versioned in `/home/ubuntu/agente-v2`

- `scripts/runtime_authority.py`: schema, renderização e verificação read-only.
- `tests/test_runtime_authority.py`: contratos unitários e sanitização.
- `docs/operations/runtime-authority.md`: runbook estável, sem valores mutáveis.
- `AGENTS.md`: entrada obrigatória para agentes no repositório.
- `README.md`: identifica `main` como desenvolvimento e aponta à autoridade operacional.
- `docs/superpowers/specs/2026-09-05-v2-runtime-authority-design.md`: desenho aprovado.
- `docs/superpowers/plans/2026-09-05-v2-runtime-authority.md`: este plano.

### Host-local and secret-free

- `/home/ubuntu/AGENTS.md`: roteador de projetos para novas conversas.
- `/home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json`: contrato canônico atual.
- `/home/ubuntu/workspace/agente-v2-control/CURRENT.md`: projeção humana.
- `/home/ubuntu/workspace/agente-v2-control/README.md`: uso e atualização.
- `/home/ubuntu/workspace/agente-v2-control/history/<timestamp>/`: snapshots substituídos.

### Deploy artifacts

- `/home/ubuntu/workspace/agente-v2-canary-deploy/compose-b3173693d6d8.yaml`: GA corrigido.
- `/home/ubuntu/workspace/agente-v2-canary-deploy/v2-candidate-b3173693d6d8.env`: env GA `0600`.
- `/home/ubuntu/workspace/agente-v2-canary-deploy/runtime/ga-runtime-image-metadata-b3173693d6d8.json`: identidade OCI montada read-only.
- `/home/ubuntu/workspace/agente-v2-canary-deploy/compose.test-contact-fresh.yaml`: teste isolado atualizado para a nova imagem.
- `/home/ubuntu/workspace/agente-v2-canary-deploy/history/<timestamp>/INDEX.json`: inventário e SHA-256 dos ponteiros vencidos.

---

### Task 1: Implementar o verificador versionado por TDD

**Files:**
- Create: `scripts/runtime_authority.py`
- Create: `tests/test_runtime_authority.py`

**Interfaces:**
- Produces: `load_manifest(path: Path) -> dict[str, object]`
- Produces: `render_markdown(manifest: Mapping[str, object]) -> str`
- Produces: `verify_manifest(manifest: Mapping[str, object], runner: Runner, http_get: HttpGet) -> list[str]`
- Produces CLI: `python scripts/runtime_authority.py render --manifest PATH --output PATH`
- Produces CLI: `python scripts/runtime_authority.py verify --manifest PATH [--json]`

- [ ] **Step 1: escrever testes de schema fechado e sanitização**

Criar fixtures sem valores reais. Os testes devem exigir os top-levels `schema`, `generated_at`, `repository`, `components`, `legacy`, `generic_pointers` e `verification`, rejeitar campos secretos por chave e rejeitar strings com padrões `subscriber_id`, `api_key`, `secret`, `token`, `email` e `phone` fora de nomes de variáveis documentais.

```python
def test_manifest_rejects_secret_bearing_keys(tmp_path):
    manifest = minimal_manifest()
    manifest["api_key"] = "never-allowed"
    path = tmp_path / "ACTIVE_RUNTIME.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AuthorityError, match="forbidden key"):
        load_manifest(path)


def test_render_never_emits_target_identity():
    manifest = minimal_manifest()
    manifest["components"]["test_contact"]["routing"]["target_hash"] = "sha256:" + "a" * 64
    rendered = render_markdown(manifest)
    assert "sha256:" in rendered
    assert "subscriber" not in rendered.lower()
```

- [ ] **Step 2: executar o teste e observar RED**

Run:

```bash
python -m pytest -q tests/test_runtime_authority.py
```

Expected: falha de import porque `scripts.runtime_authority` ainda não existe.

- [ ] **Step 3: implementar parser, renderer e injeção de runner**

Usar somente stdlib. O runner recebe `list[str]`, retorna `Completed`, aplica timeout e nunca inclui `Config.Env` no diagnóstico. `load_manifest` exige `schema == "agente-v2-active-runtime-v1"`; `render_markdown` itera os três componentes numa ordem fixa: `ga`, `test_contact`, `ops`.

```python
@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str

Runner = Callable[[Sequence[str]], Completed]
HttpGet = Callable[[str], tuple[int, bytes]]

class AuthorityError(RuntimeError):
    pass
```

- [ ] **Step 4: testar verificações Git, Docker, imagem, mounts, hardening, rotas e health com fakes**

Cobrir:

- `git rev-parse <sha>^{tree}`;
- `git show-ref --verify refs/heads/production/<component>`;
- `git ls-remote --heads origin refs/heads/production/<component>`;
- `docker image inspect <ref>` e OCI revision;
- `docker inspect <container>` sem serializar `Config.Env`;
- image ID, compose project/config/service, status/health, usuário, rootfs read-only, `CapDrop=ALL`, `SecurityOpt=no-new-privileges`;
- mounts exatos e modo read-only quando declarado;
- labels Traefik e prioridades;
- `/readyz` ou `/healthz` por `GET`;
- heartbeat JSON e filas saudáveis;
- hashes de arquivos produtivos via `git show SHA:path` e `docker exec CONTAINER python -c ...`;
- ponteiros genéricos por SHA-256.

- [ ] **Step 5: executar unitários, compile e diff check**

```bash
python -m pytest -q tests/test_runtime_authority.py
python -m py_compile scripts/runtime_authority.py tests/test_runtime_authority.py
git diff --check
```

Expected: todos passam e não há saída de `git diff --check`.

- [ ] **Step 6: commit**

```bash
git add scripts/runtime_authority.py tests/test_runtime_authority.py
git commit -m "feat(ops): add fail-closed V2 runtime authority verifier"
```

---

### Task 2: Documentar a cadeia de autoridade para humanos e novos agentes

**Files:**
- Create: `docs/operations/runtime-authority.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Create: `/home/ubuntu/AGENTS.md`

**Interfaces:**
- Consumes: CLI de `scripts/runtime_authority.py`.
- Produces: regra estável de descoberta sem SHAs mutáveis no README/AGENTS versionados.

- [ ] **Step 1: escrever teste documental**

Adicionar em `tests/test_runtime_authority.py` um teste que lê `AGENTS.md`, `README.md` e `docs/operations/runtime-authority.md` e exige, em todos, o caminho `/home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json`, além das frases semânticas “não inferir” e “V3 fora de escopo”.

- [ ] **Step 2: executar o teste e observar RED**

```bash
python -m pytest -q tests/test_runtime_authority.py -k documentation
```

Expected: falha porque os documentos ainda não apontam à autoridade.

- [ ] **Step 3: escrever o runbook e atualizar entradas**

`docs/operations/runtime-authority.md` deve conter:

```text
1. Leia ACTIVE_RUNTIME.json.
2. Execute runtime_authority.py verify.
3. Escolha production/ga ou production/ops conforme o componente.
4. Pare se houver DRIFT.
5. Nunca use main, nome de worktree, tag genérica ou repo legado como prova de produção.
```

No topo de `AGENTS.md` e `README.md`, inserir uma seção “Runtime ativo (obrigatório)” antes da cadeia histórica de refatoração. Criar `/home/ubuntu/AGENTS.md` somente com roteamento de projeto, isolamento V2/V3 e os mesmos dois comandos de leitura/verificação.

- [ ] **Step 4: executar testes e commit versionado**

```bash
python -m pytest -q tests/test_runtime_authority.py
git diff --check
git add AGENTS.md README.md docs/operations/runtime-authority.md tests/test_runtime_authority.py
git commit -m "docs(ops): make V2 runtime authority the canonical entrypoint"
```

O arquivo `/home/ubuntu/AGENTS.md` é host-local e não entra no commit.

---

### Task 3: Qualificar e publicar a imagem GA com identidade correta

**Files:**
- Read-only source: `/home/ubuntu/agente-v2/.worktrees/maya-v2-ops-dashboard`
- Create artifact: local registry tag/digest for `b3173693d6d8`
- Create: build evidence under `/home/ubuntu/workspace/agente-v2-control/history/<timestamp>/build/`

**Interfaces:**
- Consumes: Git SHA `b3173693d6d852ba8bcf8df5f7aa8e7a6d4c10f3`.
- Produces: immutable `IMAGE_REF=127.0.0.1:5000/agente-v2@sha256:<manifest>` and local image ID.

- [ ] **Step 1: confirmar source worktree limpa e SHA/tree**

```bash
git -C /home/ubuntu/agente-v2/.worktrees/maya-v2-ops-dashboard status --porcelain=v1
git -C /home/ubuntu/agente-v2/.worktrees/maya-v2-ops-dashboard rev-parse HEAD
git -C /home/ubuntu/agente-v2/.worktrees/maya-v2-ops-dashboard rev-parse HEAD^{tree}
```

Expected: status vazio; HEAD `b3173693…`.

- [ ] **Step 2: executar lint, contratos operacionais e regressão**

Instalar somente em venv já existente ou criar `.venv-runtime-authority` fora da árvore rastreada. Rodar exatamente os comandos da workflow `phase8.yml`, incluindo `ruff`, boundary checker, compileall, contratos operacionais e regressão com as sete deselections documentadas. Não carregar `.env` de produção nos testes.

Expected: exit `0` em todos os comandos.

- [ ] **Step 3: construir com labels determinísticos**

```bash
SHA=b3173693d6d852ba8bcf8df5f7aa8e7a6d4c10f3
BUILD_DATE=$(git -C /home/ubuntu/agente-v2/.worktrees/maya-v2-ops-dashboard show -s --format=%cI "$SHA")
docker build \
  --build-arg "VCS_REF=$SHA" \
  --build-arg "BUILD_DATE=$BUILD_DATE" \
  --build-arg "VERSION=0.8.0-ga-b3173693d6d8" \
  --tag "127.0.0.1:5000/agente-v2:ga-b3173693d6d8" \
  --file Dockerfile.v2 .
```

- [ ] **Step 4: validar imagem local antes do push**

Exigir OCI revision igual ao SHA, usuário padrão não-root, imports produtivos, `/app` somente com conteúdo esperado e igualdade SHA-256 para todos os arquivos Git copiados por `Dockerfile.v2`; ignorar somente `__pycache__`/`.pyc` criados por compileall.

- [ ] **Step 5: dark smoke sem dados ou rede de produção**

Executar o import/boot smoke equivalente à workflow com credenciais falsas, `--network none`, `--read-only`, tmpfs e metadata temporária. Não iniciar worker com providers reais e não enviar webhook público.

- [ ] **Step 6: push único e captura de digest**

```bash
docker push 127.0.0.1:5000/agente-v2:ga-b3173693d6d8
```

Capturar o digest retornado, inspecionar por digest, repetir OCI/hash smoke e escrever evidência sanitizada. Se o digest ou label divergir, parar antes do deploy.

---

### Task 4: Preparar sucessor e rollback de GA

**Files:**
- Create: `/home/ubuntu/workspace/agente-v2-canary-deploy/compose-b3173693d6d8.yaml`
- Create: `/home/ubuntu/workspace/agente-v2-canary-deploy/v2-candidate-b3173693d6d8.env`
- Create: `/home/ubuntu/workspace/agente-v2-canary-deploy/runtime/ga-runtime-image-metadata-b3173693d6d8.json`
- Preserve: `compose-5cbe0f971130.yaml`, `v2-candidate-5cbe0f971130.env`, old metadata and image.

**Interfaces:**
- Consumes: immutable image ref from Task 3.
- Produces: renderable successor Compose and exact rollback command.

- [ ] **Step 1: copiar o Compose ativo por bytes e criar env protegido**

O novo Compose deve diferir do atual apenas por nome de artefato, se necessário; a identidade vem do env. Copiar o env ativo com modo `0600` e alterar somente:

```text
V2_CANDIDATE_GIT_SHA=b3173693d6d852ba8bcf8df5f7aa8e7a6d4c10f3
V2_CANDIDATE_IMAGE_DIGEST=sha256:<novo-manifest-digest>
V2_IMAGE_REF=127.0.0.1:5000/agente-v2@sha256:<novo-manifest-digest>
V2_IDENTITY_IMAGE_REF=127.0.0.1:5000/agente-v2@sha256:<novo-manifest-digest>
V2_RUNTIME_IDENTITY_METADATA_HOST_PATH=/home/ubuntu/workspace/agente-v2-canary-deploy/runtime/ga-runtime-image-metadata-b3173693d6d8.json
```

Preservar todas as demais linhas byte a byte.

- [ ] **Step 2: gerar metadata a partir de `docker image inspect`**

O metadata deve ter schema existente, OCI revision `b317369…` e `repo_digests` contendo exatamente a ref imutável publicada. Modo `0444`.

- [ ] **Step 3: renderizar Compose e comparar contrato**

```bash
docker compose -p agente-v2-ga \
  --env-file v2-candidate-b3173693d6d8.env \
  -f compose-b3173693d6d8.yaml config --quiet
```

Comparar JSON renderizado com o Compose ativo, normalizando apenas os cinco campos de identidade. Nomes, mounts, networks, ports, security, labels e commands devem ser iguais.

- [ ] **Step 4: congelar preflight e rollback**

Salvar inspect sanitizado, image IDs, rotas, health, heartbeat e backups SQLite consistentes. Registrar rollback exato:

```bash
docker compose -p agente-v2-ga \
  --env-file v2-candidate-5cbe0f971130.env \
  -f compose-5cbe0f971130.yaml up -d --force-recreate
```

---

### Task 5: Cutover controlado de GA e teste isolado

**Files:**
- Use: artifacts da Task 4.
- Modify: `/home/ubuntu/workspace/agente-v2-canary-deploy/compose.test-contact-fresh.yaml`
- Preserve: ambos os diretórios de estado.

**Interfaces:**
- Produces: seis containers de atendimento na imagem imutável nova; Ops inalterado.

- [ ] **Step 1: recriar GA somente após preflight verde**

```bash
docker compose -p agente-v2-ga \
  --env-file v2-candidate-b3173693d6d8.env \
  -f compose-b3173693d6d8.yaml up -d --force-recreate
```

Aguardar health por container e `GET /readyz`; verificar heartbeat e dez filas.

- [ ] **Step 2: validar GA antes de tocar no teste isolado**

Comparar containers, mounts, hardening, redes, portas e labels com preflight. Comparar backups consistentes/contagens de estado e garantir ausência de falha persistente em logs sanitizados. Se falhar, executar rollback da Task 4.

- [ ] **Step 3: atualizar somente a identidade/imagem do Compose de teste**

Criar backup do Compose de teste; substituir a ref imutável antiga pela nova e atualizar metadata esperado para `b317369…`, sem alterar seletor, fallback, prioridades, estado ou secrets.

- [ ] **Step 4: recriar os três serviços de teste e verificar split**

Usar o mesmo project name existente. Validar a rota selecionada e o fallback com requisições autenticadas de corpo estruturalmente inválido, que retornam antes da persistência e não disparam efeitos. Não usar mensagem real.

- [ ] **Step 5: provar Ops invariante**

Comparar ID/criação/imagem/mounts do container Ops com o preflight; `GET /ops/healthz` deve retornar modo read-only.

---

### Task 6: Criar a autoridade canônica e organizar artefatos históricos

**Files:**
- Create all files under `/home/ubuntu/workspace/agente-v2-control/`.
- Archive selected stale files under `/home/ubuntu/workspace/agente-v2-canary-deploy/history/<timestamp>/`.
- Update generic GA pointers.

**Interfaces:**
- Consumes: runtime final das Tasks 3–5.
- Produces: `ACTIVE_RUNTIME.json` que passa no verificador.

- [ ] **Step 1: gerar manifesto sem segredo/PII**

Popular os três componentes com SHAs, trees, digests, IDs, compose project/config/service, mounts, hardening, health e paths. O alvo do teste entra somente como SHA-256 já autorizado; nenhum identificador bruto.

- [ ] **Step 2: renderizar `CURRENT.md` pelo script versionado**

```bash
python /home/ubuntu/agente-v2/.worktrees/v2-runtime-authority/scripts/runtime_authority.py render \
  --manifest /home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json \
  --output /home/ubuntu/workspace/agente-v2-control/CURRENT.md
```

- [ ] **Step 3: arquivar ponteiros vencidos com hashes**

Antes de mover, verificar que nenhum container em execução monta o arquivo. Arquivar cópias de `compose.yaml`, antigo `compose.ga.yaml`, antigo `GENERAL-AVAILABILITY-RELEASE.public.json`, metadata genérico antigo e estados de canary encerrados. `INDEX.json` registra origem, destino, tamanho, modo e SHA-256.

- [ ] **Step 4: criar ponteiros genéricos atuais**

`compose.ga.yaml` e metadata público/genérico devem refletir a release nova. Um `compose.yaml` inseguro que possa reviver canary antigo não permanece como default; sem comando explícito, Docker Compose deve falhar de forma segura. Documentar o comando exato no README.

- [ ] **Step 5: capturar e remover container canary parado**

Salvar `docker inspect` sanitizado e então remover somente `agente-v2-canary-api`, confirmado como `exited`. Não remover imagem ou estado associado.

- [ ] **Step 6: executar o verificador até PASS**

```bash
python /home/ubuntu/agente-v2/.worktrees/v2-runtime-authority/scripts/runtime_authority.py verify \
  --manifest /home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json
```

Expected: uma linha `PASS agente-v2-active-runtime-v1` e exit `0`.

---

### Task 7: Publicar refs Git e integrar documentação em `main`

**Files:**
- Branch: `ops/v2-runtime-authority`
- Remote refs: `production/ga`, `production/ops`
- Immutable tags: release GA e release Ops com data UTC.

**Interfaces:**
- Produces: refs remotas que o verificador exige.

- [ ] **Step 1: criar refs locais sem mover commits ativos**

```bash
git branch production/ga b3173693d6d852ba8bcf8df5f7aa8e7a6d4c10f3
git branch production/ops 039dd1a3c0a93e6b4e2e5192ba9da23c4f2977fb
git tag -a v2-ga-2026-09-05-b3173693 b3173693d6d852ba8bcf8df5f7aa8e7a6d4c10f3 -m "Agente V2 GA active release 2026-09-05"
git tag -a v2-ops-2026-09-05-039dd1a3 039dd1a3c0a93e6b4e2e5192ba9da23c4f2977fb -m "Maya Ops active release 2026-09-05"
```

Falhar se qualquer ref já existir em SHA diferente.

- [ ] **Step 2: rodar testes finais do branch documental/verificador**

```bash
python -m pytest -q tests/test_runtime_authority.py
git diff --check
git status --porcelain=v1
```

Expected: testes verdes; árvore limpa após commit final.

- [ ] **Step 3: publicar branch, refs e tags**

Usar push explícito por refspec, sem force:

```bash
git push origin ops/v2-runtime-authority
git push origin production/ga:production/ga production/ops:production/ops
git push origin v2-ga-2026-09-05-b3173693 v2-ops-2026-09-05-039dd1a3
```

- [ ] **Step 4: fast-forward de `main` somente com commits da autoridade**

Atualizar checkout principal após confirmar `origin/main` igual ao baseline e integrar `ops/v2-runtime-authority` por `--ff-only`; executar testes e push sem force.

- [ ] **Step 5: atualizar manifesto com refs remotas e verificar novamente**

`git ls-remote` deve coincidir; renderizar `CURRENT.md` e exigir PASS.

---

### Task 8: Verificação final e revisão independente

**Files:**
- Read-only all final artifacts.
- Create: evidence sanitizada em `/home/ubuntu/workspace/agente-v2-control/history/<timestamp>/final/`.

**Interfaces:**
- Produces: decisão final GO/NO-GO e relatório breve.

- [ ] **Step 1: executar testes proporcionais e verificador**

Rodar unitários do verificador, contratos operacionais da workflow, `docker compose config --quiet` para GA/teste/Ops e o verificador canônico.

- [ ] **Step 2: auditar runtime final**

Exigir:

- GA e teste na mesma imagem/digest, OCI SHA `b317369…`;
- Ops no SHA `039dd1a…`, container invariante e read-only;
- todos os containers esperados running/healthy;
- filas saudáveis;
- `/readyz` e `/ops/healthz` verdes;
- webhook sem autenticação ainda `401`;
- prioridades e fallback intactos;
- sem container canary antigo;
- nenhuma alteração no V3.

- [ ] **Step 3: revisar logs sanitizados e estado**

Buscar somente marcadores de erro em janelas do cutover, sem imprimir payloads. Confirmar que não houve migração, remoção ou truncamento de banco.

- [ ] **Step 4: revisão independente read-only**

Entregar ao revisor o design, plano, diff Git, manifesto e saída do verificador. Bloqueadores exigem correção e nova rodada; sugestões não bloqueantes entram no relatório.

- [ ] **Step 5: decisão**

Declarar `GO` somente se todos os gates tiverem evidência real. Caso contrário, declarar `NO-GO`, restaurar runtime quando aplicável e listar o blocker exato sem inventar resultados.
