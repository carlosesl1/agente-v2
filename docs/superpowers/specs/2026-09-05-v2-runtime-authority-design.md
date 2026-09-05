# Agente V2 — autoridade única de código e runtime

> **Documento histórico de execução.** A autoridade normativa atual é
> [`docs/operations/runtime-authority.md`](../../operations/runtime-authority.md)
> junto com
> `/home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json`. O addendum deste
> documento e o código atual prevalecem sobre quaisquer comandos históricos
> abaixo.

**Data:** 2026-09-05  
**Estado:** registro histórico; consultar a autoridade normativa acima
**Escopo:** somente Agente V2; Agente V3 permanece isolado e fora de escopo

## Problema

O projeto possui três componentes ativos que não compartilham necessariamente o mesmo commit:

1. atendimento GA (API, worker e router);
2. runtime temporário e isolado de teste para um assinante autorizado;
3. Maya Ops read-only.

A organização atual permite conclusões incorretas porque:

- `main` representa uma trilha anterior da refatoração, não o código publicado;
- nomes de worktrees e feature branches não indicam se uma árvore está ativa;
- o diretório de deploy mantém candidatos históricos ao lado de ponteiros genéricos;
- o manifesto público e o metadata genérico de GA estão vencidos;
- os bytes do runtime de atendimento correspondem a `b3173693d6d852ba8bcf8df5f7aa8e7a6d4c10f3`, enquanto o ambiente e o metadata montado ainda declaram `5cbe0f971130b80ec040a0b451d2dd52b9ceb5ad`;
- o Maya Ops está corretamente autenticado pelos bytes no commit `039dd1a3c0a93e6b4e2e5192ba9da23c4f2977fb`.

O resultado desejado é que uma nova conversa descubra a verdade ativa por um único procedimento verificável, sem inferir por nomes ou documentos históricos.

## Invariantes

1. O repositório canônico do V2 é `/home/ubuntu/agente-v2`, remoto `carlosesl1/agente-v2`.
2. `/home/ubuntu/chapada-leads-hermes` é legado/inativo para o V2 atual e nunca é fonte de deploy.
3. O V3 não é lido, editado, testado, reiniciado ou usado como referência neste trabalho.
4. GA, teste isolado e Ops são componentes distintos; a autoridade não os achata em um SHA único.
5. Código ativo é comprovado por Git + bytes da imagem/container + configuração observada, não por variável de ambiente isolada.
6. Bancos, WAL/SHM, Hermes home, logs de auditoria e rollback permanecem preservados.
7. Nenhum segredo, PII, payload bruto ou identificador de contato entra no manifesto público ou no Git.
8. A reconstrução de GA deve preservar comportamento: mesma árvore funcional já observada no container, com identidade OCI correta.
9. Qualquer divergência futura faz o verificador falhar; ele nunca atualiza silenciosamente a autoridade.

## Arquitetura da autoridade

### Diretório operacional canônico

Criar `/home/ubuntu/workspace/agente-v2-control/`:

- `ACTIVE_RUNTIME.json`: contrato canônico, público e sem segredos;
- `CURRENT.md`: projeção humana gerada do mesmo contrato;
- `verify_active_runtime.py`: verificador read-only que compara contrato e runtime;
- `README.md`: regra de uso, atualização e rollback;
- `history/`: snapshots substituídos da autoridade, nunca candidatos ativos.

`ACTIVE_RUNTIME.json` contém, separadamente:

- repositório canônico, remoto e papel de `main`;
- componente GA: source SHA/tree, branch de referência, imagem por digest, image ID, Compose/env exatos, containers, mounts relevantes, domínio e prioridade de rota;
- componente de teste isolado: source SHA/tree, mesma imagem de GA, estado separado, alvo apenas por hash, fallback GA e prioridade;
- componente Ops: source SHA/tree, imagem, Compose/env, container, mounts read-only, domínio e rollback;
- repositórios/diretórios classificados como legado ou histórico;
- comandos canônicos de inspeção, deploy e rollback;
- estado da última verificação e lista fechada de divergências.

### Entrada para novas conversas

Criar `/home/ubuntu/AGENTS.md` com uma regra estável:

1. em tarefa V2, ler `ACTIVE_RUNTIME.json` e executar o verificador antes de escolher checkout;
2. usar o componente indicado para o trabalho (`production/ga` ou `production/ops`);
3. não inferir produção por `main`, nome de pasta, data de arquivo, tag mutável ou container parado;
4. não tocar no V3;
5. tratar o repositório legado como somente leitura e não ativo.

Atualizar `AGENTS.md` e `README.md` do repositório para repetir a mesma cadeia de autoridade, sem duplicar valores mutáveis.

## Referências Git

Criar e publicar branches ponteiro no remoto:

- `production/ga` no SHA exato reconstruído e publicado para atendimento;
- `production/ops` no SHA exato do painel publicado.

Criar tags imutáveis e datadas para as releases efetivamente publicadas. As branches `production/*` podem avançar somente como parte de um deploy validado; as tags nunca se movem.

`main` permanece como trilha de desenvolvimento/refatoração. Sua documentação passa a declarar explicitamente que o estado de produção vem do manifesto operacional, não do HEAD de `main`.

## Correção da identidade de GA

1. Usar uma worktree limpa no commit `b3173693d6d852ba8bcf8df5f7aa8e7a6d4c10f3`.
2. Provar que o único delta funcional em relação ao SHA declarado `5cbe0f…` presente na imagem é o conteúdo atual de `v2_adapters/provider_http.py`, correspondente a `b317369…`.
3. Executar os gates do runtime e a regressão proporcional ao candidato congelado.
4. Construir `Dockerfile.v2` com:
   - `VCS_REF=b317369…`;
   - `BUILD_DATE` derivado do commit;
   - versão explícita;
   - tag temporária imutável.
5. Validar imports, boot, OCI labels e hashes de todos os caminhos copiados pelo Dockerfile.
6. Publicar uma única imagem no registry local e capturar seu manifest digest e image ID.
7. Gerar metadata e Compose versionados com o mesmo SHA/digest, sem renomear artefato anterior.

A imagem anterior permanece disponível como rollback.

## Rollout

### Dark gate

Subir a imagem reconstruída sem rota pública e sem compartilhar bancos ativos. Exigir:

- API/router saudáveis;
- worker e dez filas saudáveis;
- imports e prompt/config presentes;
- OCI revision igual ao SHA;
- zero efeitos externos no smoke;
- bytes copiados iguais à árvore Git.

### GA

Antes da mutação:

- congelar hashes de SQLite/WAL/SHM e identidade dos containers;
- capturar Compose/env/metadata ativos;
- provar que a imagem de rollback existe localmente;
- verificar ausência de evento em estado ambíguo que torne a troca insegura.

Recriar somente API, worker e router GA com os mesmos mounts, redes, nomes, gates e estado. A troca é de identidade/imagem, não de dados ou comportamento.

### Teste isolado

Depois de GA verde, recriar API, worker e router do teste isolado com a mesma imagem reconstruída, preservando:

- estado fresco separado;
- escopo de um único alvo representado apenas por hash nos documentos;
- fallback para o router GA;
- prioridade superior da rota;
- ausência de prazo automático, conforme decisão anterior do operador.

### Maya Ops

Não reconstruir nem reiniciar o Ops neste trabalho. Verificar que os bytes continuam iguais a `039dd1a…`, os mounts permanecem read-only e o health continua verde.

## Organização do deploy histórico

No diretório existente de deploy:

- preservar arquivos versionados pelo SHA usados por containers ativos e rollback;
- arquivar os ponteiros genéricos vencidos, manifests de canary encerrado e metadata genérico substituído em `history/<timestamp>/` com índice e hashes;
- atualizar somente os ponteiros genéricos documentados para a release corrente;
- remover apenas o container canary antigo já parado, depois de salvar seu `docker inspect` sanitizado;
- não apagar bancos, imagens de rollback, archives, evidências ou diretórios de estado.

Arquivos `.env` permanecem `0600` e nunca são copiados para o diretório público de autoridade.

## Verificador — contrato histórico retificado

O nome `verify_active_runtime.py` pertence ao desenho inicial. A implementação
corrente é `scripts/runtime_authority.py`, read-only, e retorna exit code
diferente de zero para qualquer divergência. Ela verifica:

1. schema e campos fechados de `ACTIVE_RUNTIME.json`;
2. existência do repo e dos SHAs/trees;
3. refs locais e remotas `production/ga` e `production/ops`;
4. imagem/digest/image ID e OCI revision;
5. hashes dos caminhos produtivos da imagem contra o commit;
6. containers esperados, Compose files, services, mounts, usuário, rootfs, capabilities e redes;
7. rota Traefik e precedência do teste isolado sobre GA;
8. health/readiness e heartbeat do worker;
9. ponteiros genéricos e manifests públicos;
10. ausência de containers antigos classificados como removidos;
11. separação V2/V3 e classificação do legado.

Sucesso é definido por exit `0`. Para parsing, execute `verify --manifest PATH
--json` e exija o objeto `{"ok": true, "errors": []}`. No modo humano, o stdout
atual é `runtime authority: OK`, mas essa frase não é API estável. Em falha, a
CLI retorna exit diferente de zero e mantém o diagnóstico sanitizado.

## Falhas e rollback

- Falha antes de publicar imagem: nenhuma mudança no runtime.
- Falha no dark gate: remover somente containers/estado dark; GA permanece.
- Falha após recriar GA: restaurar Compose/env/metadata e imagem anteriores, recriar os três serviços e verificar saúde.
- Falha ao atualizar teste isolado: remover sua rota nova e restaurar a composição anterior; GA permanece proprietário dos demais contatos.
- Falha documental após runtime verde: manter a release ativa, marcar a autoridade como incompleta e não declarar conclusão até regenerar e validar o manifesto.

Não há retry automático de provider nem webhook real de teste durante o rollout.

## Verificação de conclusão

A implementação está concluída somente quando:

- testes e lint do candidato passam;
- imagem tem OCI revision igual ao commit e bytes conferidos;
- `production/ga` e `production/ops` existem no remoto nos SHAs declarados;
- GA, teste isolado e Ops estão saudáveis;
- hashes dos bancos ativos são invariantes antes/depois, salvo sidecars que mudem por atividade normal, caso em que a verificação usa backup SQLite consistente e contagem/semântica;
- roteamento exato e fallback são comprovados com payloads estruturalmente inválidos que não criam eventos;
- manifests genéricos apontam às releases ativas;
- o verificador canônico retorna exit `0` e, com `--json`, o objeto
  `{"ok": true, "errors": []}`;
- uma revisão independente read-only não encontra blocker material.

## Fora de escopo

- alterar regras comerciais ou prompt da Maya;
- migrar ou apagar dados;
- cancelar reservas ou pagamentos;
- modificar o V3;
- limpar todo o acervo histórico;
- tornar `main` uma imagem composta artificialmente de GA e Ops;
- remover branches remotas históricas.

## Addendum pós-implementação e reviews — 2026-09-05

### Preservação do histórico

As seções anteriores registram o desenho inicialmente aprovado como histórico da
decisão; somente os avisos e as retificações explícitas de contrato foram
acrescentados para impedir execução enganosa. Este addendum não finge que a
primeira implementação já continha o contrato final: onde houver conflito, as
conclusões abaixo e o código atual substituem a descrição técnica histórica.
Valores ativos continuam pertencendo exclusivamente a `ACTIVE_RUNTIME.json`;
este documento não cria um segundo ponteiro de produção.

### Findings de integração e evolução do schema

A inspeção da topologia real invalidou a primeira modelagem singular. GA e teste
isolado têm API, worker e router; Ops tem somente o web read-only. Workers não
possuem necessariamente Docker healthcheck, Ops não possui heartbeat de worker,
o teste isolado compartilha a fonte de GA e os ponteiros genéricos reais são
arquivos/symlinks locais, não endpoints HTTP.

O contrato implementado passou então a exigir:

- `repository.path` e `repository.remote`, para todo Git executar no repositório
  declarado;
- `components.{ga,test_contact,ops}.source.ref` explícita, permitindo que GA e
  teste isolado usem `refs/heads/production/ga`;
- `containers` no plural, com papéis fechados `api/worker/router` para GA e teste
  e `web` para Ops;
- health por container como `healthy` ou `null`, sem fabricar health para worker;
- `heartbeat opcional` por componente: objeto fechado para GA/teste e `null` para
  Ops, com schema e universo ordenado das filas quando presente;
- arquivos com caminho Git, caminho no container e papel proprietário distintos;
- roteamento exclusivo do teste isolado, comprovado somente por hash;
- `public_endpoints` separados do health de container;
- `generic_pointers` como **ponteiros locais**, com path, hash e alvo de symlink
  opcional sob raízes autorizadas.

### Sequência registrada dos reviews

1. O primeiro spec review falhou porque o schema não representava a topologia
   real e ainda aceitava UID zero, normalização de bytes, filas, Traefik, refs e
   chaves sensíveis de forma insuficiente.
2. A correção de topologia/schema e o witness executável de hash de roteamento
   fecharam os findings funcionais; o spec re-review terminou em PASS.
3. O code-quality review seguinte encontrou alias de ownership entre
   componentes, TOCTOU nos ponteiros locais e publicação não atômica da projeção.
4. A primeira correção fechou ownership e publicação, mas um novo review manteve
   aberto o race por symlink em diretório intermediário e registrou os riscos de
   modo do arquivo e `fsync` do diretório.
5. O review final confirmou travessia ancorada por descritores, hash no mesmo FD,
   revalidação de identidades, modo novo/preservado e a ordem
   `file-fsync → replace → directory-fsync`; encerrou os findings sem blocker.

Essa sequência é parte do histórico auditável. Um PASS posterior fecha findings,
mas não apaga os FAILs que causaram as correções.

### Referências e worktrees canônicas

Os nomes estáveis para escolher uma árvore depois de verificar o manifesto são:

| Componente | Ref | Worktree canônica |
|---|---|---|
| GA e teste isolado | `production/ga` | `/home/ubuntu/agente-v2/.worktrees/production-ga` |
| Ops | `production/ops` | `/home/ubuntu/agente-v2/.worktrees/production-ops` |

As worktrees `production-ga` e `production-ops` são checkouts convenientes das
refs, não prova autônoma de produção. O manifesto verificado continua sendo a
fonte dos commits, trees, imagens e digests ativos. `main`, nomes de diretório,
tags genéricas, Compose solto e repo legado continuam sem autoridade.

GA, teste isolado e Ops permanecem identidades operacionais separadas. Mesmo
quando GA e teste compartilham código/imagem, seus projetos Compose, containers,
roteamento e estado gravável não podem colidir. Ops permanece read-only, sem
heartbeat inventado.

**V3 fora de escopo.** O addendum não autoriza leitura, edição, teste, restart ou
uso do V3 como referência.

### Bootstrap das entradas operacionais

A cadeia READ → VERIFY descreve o estado publicado, não um pré-requisito
circular para as tarefas que criam seus próprios artefatos. O roteador
host-local só deve ser ativado depois de manifesto, runbook e script existirem no
checkout canônico. Antes disso, apenas um handoff explícito e limitado pode
selecionar a worktree de bootstrap; ele não confere autoridade de runtime, não
autoriza produção e não relaxa o isolamento de V3.
