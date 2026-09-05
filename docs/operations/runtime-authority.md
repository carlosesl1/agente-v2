# Autoridade canônica do runtime Agente V2

## Runtime ativo (obrigatório)

Este runbook é a entrada operacional para humanos e novas conversas. A única
fonte canônica dos valores ativos de código, imagem, configuração e rollback é:

`/home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json`

Execute a cadeia abaixo a partir da raiz de um checkout deste repositório. Ela é
fail-closed: somente o exit code `0` do verificador permite continuar.

## Cadeia canônica: read → verify → branch → stop on DRIFT

### 1. READ — ler a autoridade

Leia o manifesto antes de escolher branch, worktree, imagem ou Compose:

```bash
python3 -m json.tool /home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json
```

Confirme que o documento é legível e identifica separadamente `ga`,
`test_contact` e `ops`. Não copie seus valores mutáveis para READMEs, instruções
de agentes ou tickets; volte a lê-lo a cada operação.

### 2. VERIFY — verificar a autoridade observada

Sem editar o manifesto, execute o verificador read-only:

```bash
python3 scripts/runtime_authority.py verify --manifest /home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json
```

O verificador confronta o contrato com Git, imagens, containers, arquivos,
mounts, hardening, rotas, prontidão, heartbeat quando declarado e ponteiros
locais. O stdout não substitui o exit code: só `0` autoriza a próxima etapa.

### 3. BRANCH — escolher o componente

Depois de READ e VERIFY verdes, use a referência e a worktree do componente:

| Componente | Branch de produção | Worktree canônica | Limite |
|---|---|---|---|
| GA | `production/ga` | `/home/ubuntu/agente-v2/.worktrees/production-ga` | API, worker e router GA |
| Teste isolado | `production/ga` | `/home/ubuntu/agente-v2/.worktrees/production-ga` | mesma fonte autorizada de GA, runtime e estado próprios |
| Ops | `production/ops` | `/home/ubuntu/agente-v2/.worktrees/production-ops` | painel web read-only |

O manifesto declara a ref de cada componente; a tabela não fixa commit nem
digest. Pare também se a worktree escolhida estiver suja ou sua branch não
corresponder à ref declarada.

### 4. STOP ON DRIFT — parar diante de divergência

Trate como **DRIFT** qualquer manifesto ausente ou inválido, exit code diferente
de zero, `FAIL`, ref divergente, artefato ausente, container inesperado, hash
incorreto, health/readiness inválido, heartbeat vencido ou ponteiro alterado.

Em DRIFT:

1. pare antes de deploy, restart, rollback, atualização de branch ou publicação
   de ponteiro;
2. preserve a saída sanitizada e identifique o componente afetado;
3. não “corrija” a autoridade para fazê-la coincidir com um runtime não
   autenticado;
4. retome somente com uma correção revisada ou com o rollback autorizado, e
   repita READ → VERIFY desde o início.

Não inferir a produção por `main`, nome de worktree, tag genérica, Compose solto
ou repo legado. Nenhum desses sinais prova o que está ativo. Uma tag imutável
pode registrar uma release, mas não substitui o manifesto e a verificação.

**V3 fora de escopo.** Não ler, editar, testar, reiniciar nem usar V3 como
referência durante este procedimento.

## Modelo dos componentes e do estado

O schema usa `containers` no plural porque a autoridade representa todos os
processos relevantes, não um container simbólico:

- GA possui API, worker e router;
- o teste isolado possui API, worker e router;
- Ops possui um único container web read-only.

`heartbeat opcional` significa que o campo é específico do componente: GA e
teste isolado declaram o heartbeat do worker e o conjunto fechado de filas;
Ops não inventa heartbeat, pois não possui esse worker. A ausência de Docker
healthcheck no worker não pode ser convertida artificialmente em `healthy`.

GA e teste isolado podem compartilhar fonte e imagem, mas nunca identidade
operacional. Cada um mantém projeto Compose, nomes de containers e **estado
separado**, inclusive todo mount gravável. Ops tem fonte/imagem próprias e seus
mounts operacionais permanecem read-only. Não compartilhe SQLite, WAL/SHM,
diretório de estado ou Hermes home gravável entre componentes.

Os `generic_pointers` são ponteiros locais — arquivos regulares ou symlinks sob
raízes autorizadas — e são verificados por caminho, alvo e hash. Eles não são
URLs nem evidência autossuficiente.

## Prontidão e saúde

- Use requisições `GET` para a prontidão declarada; o endpoint de atendimento é
  `/readyz`.
- Um `200` público em `/readyz` não prova sozinho qual router venceu. Confira
  também labels, prioridade, container e hash de roteamento declarados, pois o
  teste isolado pode ter prioridade superior a GA.
- Para API/router, confira Docker health quando declarado. Para workers, confira
  a atualidade e o conteúdo fechado do heartbeat.
- Para Ops, use o endpoint declarado no manifesto e confirme tanto a saúde
  quanto o modo read-only; não fabrique um heartbeat de worker.
- Smoke de prontidão não pode produzir webhook, mensagem ou write comercial.

## Deploy atômico

“Deploy atômico” aqui é uma transação operacional fail-closed, não uma promessa
de atomicidade entre Git, OCI e Docker:

1. obtenha exclusividade operacional e repita READ → VERIFY;
2. confirme a worktree/branch canônica limpa e o componente exato;
3. preserve um snapshot sanitizado da autoridade corrente, o bundle exato de
   rollback e um backup consistente do estado, sem imprimir conteúdo;
4. valide previamente o bundle versionado completo e a imagem imutável indicada
   pelo processo de release; não use tag genérica nem Compose solto;
5. altere somente os containers do componente alvo, preservando mounts, redes,
   hardening, roteamento e estado;
6. verifique containers, bytes, `/readyz`, heartbeat/filas quando aplicável,
   roteamento e separação de estado;
7. publique refs e autoridade nova somente depois desses gates. Se qualquer gate
   falhar, não publique estado parcial: entre imediatamente no rollback.

GA e teste isolado são cutovers separados, mesmo quando usam a mesma imagem.
GA deve ficar verde antes de tocar no teste isolado. Um deploy de GA não reinicia
Ops; uma mudança de Ops exige seu próprio procedimento e preserva o caráter
read-only.

## Rollback atômico

1. pare novas mutações do componente afetado e preserve a evidência sanitizada;
2. selecione o bundle anterior pela autoridade/history autenticados, nunca por
   nome genérico ou arquivo “mais recente”;
3. restaure em conjunto a imagem imutável, o Compose/env/metadata correspondentes
   e somente os containers do componente afetado;
4. não restaure, copie, migre ou apague bancos/WAL/SHM manualmente;
5. repita os gates de containers, bytes, `/readyz`, heartbeat, roteamento, modo
   read-only de Ops e estado separado;
6. faça a atualização atômica da autoridade para a release restaurada e execute
   o verificador canônico novamente.

Se o rollback não verificar, permaneça em DRIFT/NO-GO. Não avance branches nem
reescreva evidência para declarar sucesso.

## Atualização atômica da autoridade

A atualização de `ACTIVE_RUNTIME.json` tem um único escritor e ocorre somente
após o runtime alvo ter sido validado:

1. adquira o lock operacional e copie o manifesto anterior para `history/`, com
   modo, tamanho e hash, sem alterar o original;
2. gere um candidato completo no mesmo filesystem, valide schema e conteúdo e
   execute o verificador contra esse arquivo candidato;
3. gere a projeção humana a partir do mesmo candidato;
4. faça `flush`/`fsync`, publique o manifesto com rename/replace atômico e faça
   `fsync` do diretório; nunca edite o arquivo ativo in-place;
5. publique a projeção derivada atomicamente. Se ela falhar, o JSON continua a
   fonte de verdade, mas a operação permanece incompleta/DRIFT até a projeção ser
   regenerada;
6. solte o lock apenas depois de repetir READ → VERIFY no caminho canônico.

Não existe atualização silenciosa para “aceitar o observado”. Branches
`production/*`, runtime e manifesto avançam somente dentro da mesma mudança
revisada, com caminho de rollback previamente autenticado.

## Sem segredos, PII ou payloads

`ACTIVE_RUNTIME.json`, sua projeção e toda evidência deste procedimento são **sem
segredos**. Nunca registre ou imprima:

- `.env`, `Config.Env`, credenciais, tokens ou connection strings;
- identidade bruta do alvo isolado, telefone, e-mail, subscriber ID ou mensagem;
- payload de webhook/provider, banco, WAL/SHM, log bruto ou conteúdo de auditoria;
- valores encontrados apenas para facilitar uma correção de DRIFT.

O alvo isolado entra somente como hash permitido pelo schema. Diagnósticos devem
nomear a superfície divergente, não seu valor sensível. Preserve permissões dos
arquivos locais e mantenha qualquer env fora da autoridade pública.

## Regra terminal

Sem manifesto lido, verificador verde, branch correta e ausência de DRIFT, a
decisão é **NO-GO**. Não há exceção por urgência, familiaridade com a máquina ou
aparência de um checkout.
