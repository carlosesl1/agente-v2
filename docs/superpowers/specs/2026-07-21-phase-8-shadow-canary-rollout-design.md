# Fase 8 — Shadow, canary e rollout por digest

## Estado e decisão

Esta spec abre a Fase 8 a partir de `origin/main` no commit
`93682024b4867d3e313324339a7060d5351dcd3d`, tree
`b779e35c671f3050d056c6ef3c8c0700f5b13f35`, após a Fase 7 fechar com
revisão terminal 3/3 e CI remoto `29804123764` verde.

A abordagem escolhida é uma **escada estrita**:

1. fechar baseline, spec e plano;
2. construir uma imagem candidata uma única vez;
3. executar dark canary com reads reais e zero writes/delivery;
4. provar ingress ManyChat real com outbound fechado;
5. habilitar outbound somente para o contato de teste e avisar Carlos para ele
   executar os testes naturais de conversa;
6. após aprovação humana, escolher provider/workflow e pedir autorização
   separada para uma única canary E2E com write;
7. promover o mesmo digest gradualmente;
8. manter a Fase 9 bloqueada até o closeout da Fase 8.

A autorização para avançar até o gate conversacional foi registrada na sessão de
controle em 2026-07-21. Ela não autoriza provider write, pagamento, mensagem fora
do contato de teste, rollout comercial ou remoção do legado.

## Objetivo

Provar que o runtime integrado da Fase 7 pode ser construído, executado e
promovido como um único artefato imutável em condições progressivamente reais,
sem tocar na árvore operacional suja e sem permitir efeitos comerciais antes dos
gates correspondentes.

## Estado de entrada autenticado

### Trilha limpa

- `agente-v2 main` e `origin/main`:
  `93682024b4867d3e313324339a7060d5351dcd3d`;
- integração local pós-merge: 762/762 testes;
- validator terminal da Fase 7: `passed`;
- branch/worktree da Fase 8:
  `phase8-shadow-canary-rollout` em
  `/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout`;
- worktree de entrada limpa e sem divergência contra `origin/main`.

### Artefato funcional aprovado

- candidato funcional `agente-v2`:
  `2c99be11b1bdc1b66d14bd7a19c510ec50d502d4`, tree
  `3a05029f10e9f96ac93d57b71a99f61f686e1971`;
- wheel SHA-256
  `be1bed664f9eb0a9f0af06b31bd55688e4041c81411ee1cc22416282270446dd`,
  214954 bytes;
- réplica sanitizada limpa:
  `/home/ubuntu/workspace/agente-v2-phase7-runtime-candidate10`, commit
  `183fb41d645e1bb04e237c986988309a28e42b34`, tree
  `e546e9d88093c09a245502bcca3d119e2e450672`;
- patch de integração SHA-256
  `4d0ccd5e6dae410abca8da8b555fd0784668eecd5c4e0499e919997be38e0218`,
  96073 bytes.

### Runtime operacional observado e somente leitura

- repositório `/home/ubuntu/chapada-leads-hermes`;
- HEAD `57408d8b2040399bc25ee7957505208079458884`;
- tree `67b5fe18d4685281778e41cd61cd584dd063ea60`;
- 86 entradas locais;
- status `-z` SHA-256
  `e299a15f0336646ef62d5e88a4989d46ef46d6865c5d3163e092969fa9a8ef7a`;
- diff binário SHA-256
  `1b66221d27290ab6eb3c76e7cea2ab6e678fd06d1489f99752a42ee89cd1608b`;
- container live atual saudável, image ID
  `sha256:2dc5f71557b82d4d0646ab1dba0b61edfa7d916320047dd03ce8554dbfa50d53`;
- release live reportada:
  `57408d8b2040399bc25ee7957505208079458884` /
  `v2026.07.13.0545`;
- o runtime live está em `mode=live`, `dry_run=false`, outbound e workers
  habilitados, com gates de provider/payment abertos. Ele não pode servir de
  dark canary nem fornecer seu volume de estado ao candidato.

## Princípios e invariantes

1. **Build a partir da réplica, nunca do legado.** O contexto de build deve ser a
   réplica sanitizada limpa e autenticada. O caminho
   `/home/ubuntu/chapada-leads-hermes` é rejeitado pelo preflight.
2. **Build once, promote same bytes.** Canary e rollout usam o mesmo image ID e
   o mesmo arquivo OCI; não existe rebuild após o freeze.
3. **Produção permanece online e imutável durante o dark canary.** O container
   `chapada-leads-hermes` não é recriado, reiniciado nem reconfigurado.
4. **Estado isolado.** Canary não acessa Supabase/Redis live, volume
   `/home/ubuntu/chapada-leads-hermes-state`, ledger/outboxes live nem sessões
   Hermes do profile live.
5. **Segredos mínimos.** O ambiente canary recebe apenas credenciais necessárias
   a Cloudbeds/Bókun read-only e uma cópia efêmera do auth/profile Hermes. Não
   recebe ManyChat API key, SMTP, Stripe, Wise, Supabase ou Redis.
6. **Zero delivery no dark/ingress gate.** Muitos caminhos podem produzir
   `reply_text`, mas nenhum pode enviar ManyChat, e-mail, imagem, formulário,
   flow, payment link ou outbox pública.
7. **Zero write comercial antes da autorização E2E.** Cart/reserva/pagamento,
   claims financeiros, settlement, provider confirmation e workers ficam
   mecanicamente fechados.
8. **Identidade sem PII.** Evidência usa hashes/IDs internos; texto bruto,
   subscriber ID, telefone, e-mail, payload provider e conversa não são
   versionados.
9. **Stop fail-closed.** Qualquer divergência de digest, estado, rota, tentativa
   ou efeito torna o resultado `NO-GO`; não se reroda comercialmente antes de
   RCA e novo candidato quando necessário.
10. **Gate humano obrigatório.** Carlos recebe instruções somente quando o
    candidate está pronto para conversa real. Nenhuma simulação interna conta
    como aprovação humana.

## Arquitetura de release

### Fonte e build

O build usa o `Dockerfile` já presente na réplica sanitizada, com:

- contexto exato da réplica commit `183fb41...`;
- `HERMES_RELEASE_COMMIT=183fb41...`;
- versão de release Phase 8 derivada do commit do manifesto, sem alterar o
  conteúdo do app;
- tag local única que nunca sobrescreve a imagem live;
- captura do base image resolvido, image ID, RootFS layer digests, tamanho,
  build args não secretos e hash do arquivo `docker image save`/OCI.

O `Dockerfile` usa uma base por tag e `apt-get`, portanto a Fase 8 não promete
rebuild byte-reprodutível da imagem. A garantia é construir uma vez, salvar o
artefato, registrar seu digest e reutilizar esses mesmos bytes.

### Manifesto de release

`docs/refactor/evidence/phase-08/release-manifest.json` deve vincular, com schema
fechado:

- commits/trees `agente-v2`, candidato funcional e réplica runtime;
- wheel e patch da Fase 7;
- Dockerfile, `pyproject.toml`, `uv.lock`, HERMES, SOUL, config, skills e plugin;
- versão do Hermes, provider/model/reasoning atestados no profile clonado;
- fingerprint não secreto do env canary;
- image ID, layers e archive/OCI SHA-256;
- imagem live anterior para rollback;
- comandos de build/load/tag sem segredos;
- `rollout=NO-GO`, `phase9_started=false`.

### Promoção e rollback

O host atual é único e não possui autenticação de registry configurada. Nesta
fase, a identidade primária é o image ID content-addressed no daemon local mais
o hash do archive OCI. A promoção:

- referencia a tag imutável criada para esse image ID;
- verifica o image ID antes de cada `docker create/up`;
- usa `pull_policy: never` e nunca `build:`;
- falha se a tag resolver para outro ID;
- preserva a imagem live anterior e seu compose/env para rollback;
- reverte pelo image ID anterior, sem rebuild.

Publicação futura em registry pode ser acrescentada somente se preservar o mesmo
manifest digest e não reabrir a imagem; não é requisito para iniciar o canary no
mesmo host.

## Ambiente do dark canary

O candidate roda como `chapada-leads-hermes-phase8-canary`, sem Traefik e sem
porta pública. Health e webhook são exercitados por `docker exec` contra
`127.0.0.1:8000` dentro do container.

### Isolamento obrigatório

- diretório de estado efêmero dedicado ou `tmpfs` em `/app/state`;
- cópia efêmera do Hermes home/profile, nunca mount RW do profile live;
- CLI, checkout Hermes e runtime Python apenas read-only;
- sem mount de Gmail/Wise e sem volume do app live;
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` e `REDIS_URL` ausentes;
- ManyChat API key e todos os IDs/flows de delivery ausentes;
- webhook secret sintético e efêmero;
- `HERMES_LEADS_PROFILE_CWD=/app`;
- sem restart automático e sem rede Traefik.

### Override fechado

O preflight rejeita o canary se qualquer valor efetivo divergir de:

```text
HERMES_LEADS_MODE=shadow
HERMES_LEADS_DRY_RUN=true
HERMES_LEADS_ALLOW_LIVE_SENDS=false
HERMES_AUTO_FLUSH_ENABLED=false
HERMES_PUBLIC_OUTBOX_AUTO_FLUSH_ENABLED=false
HERMES_POST_PAYMENT_OUTBOX_WORKER_ENABLED=false
HERMES_SIDE_EFFECT_LEDGER_ENABLED=false
HERMES_CLOUDBEDS_READONLY_ENABLED=true
HERMES_CLOUDBEDS_WRITE_ENABLED=false
HERMES_CLOUDBEDS_UPSELL_WRITE_ENABLED=false
HERMES_CLOUDBEDS_PAYMENT_CONFIRMATION_WRITE_ENABLED=false
HERMES_BOKUN_READONLY_ENABLED=true
HERMES_BOKUN_CART_WRITE_ENABLED=false
HERMES_BOKUN_RESERVATION_WRITE_ENABLED=false
HERMES_BOKUN_PAYMENT_CONFIRMATION_WRITE_ENABLED=false
HERMES_STRIPE_PAYMENT_LINK_WRITE_ENABLED=false
HERMES_CLOUDBEDS_STRIPE_PAYMENT_LINK_WRITE_ENABLED=false
HERMES_WISE_PAYMENT_MATCHER_ENABLED=false
HERMES_WISE_PAYMENT_MATCHER_SETTLEMENT_ENABLED=false
HERMES_WISE_PAYMENT_VALIDATION_ENABLED=false
HERMES_WISE_CLOUDBEDS_HOSTEL_PAYMENT_VALIDATION_WRITE_ENABLED=false
```

Aliases ou gates adicionais encontrados na implementação devem ser incluídos
fail-closed; a lista acima não autoriza ausência de um gate novo.

## Gates progressivos

### Gate A — preflight e imagem

Passa somente quando:

- as três árvores Git estão nas identidades esperadas;
- a réplica está limpa e a árvore operacional mantém seu fingerprint;
- o build context não é o runtime operacional;
- a imagem existe, health inicia e release metadata aponta para a réplica;
- o archive e o image ID estão registrados;
- a imagem live e o container live não mudaram;
- o manifesto e o scan de segredos/PII estão verdes.

### Gate B — dark canary com reads reais

Executar ao menos três fluxos completos, cada um com identidade sintética
isolada:

1. hospedagem: consulta Cloudbeds read-only positiva, progressão até resumo e
   confirmação que produz comando bloqueado;
2. passeio: consulta Bókun por ID canônico, progressão até resumo e confirmação
   que produz comando bloqueado;
3. replay/duplicata ou pacote: reapresentação/correção/segunda confirmação não
   produz segundo comando nem efeito.

Cada fluxo deve provar:

- LLM/profile real e modelo/provider/reasoning atestados;
- lookup real normalizado sem raw provider payload;
- state anterior/posterior no store isolado;
- resumo sem claim de sucesso;
- comando no máximo uma vez;
- `provider_call_executed=false` para writes;
- zero ledger/claim/outbox/delivery comercial;
- zero ManyChat/e-mail/Stripe/Wise;
- logs/evidência sanitizados.

Resultado read-only negativo ou incerteza de provider não pode ser reclassificado
como sucesso. Se não houver opção positiva, o gate para e escolhe outra
janela por nova leitura, sem write.

### Gate C — ingress ManyChat real, outbound fechado

Somente depois do Gate B:

- expor a mesma imagem em rota canary separada e mínima (`/health` e
  `/webhook/manychat`);
- usar fluxo ManyChat de teste separado, limitado ao contato autorizado;
- manter delivery e todos os writes fechados;
- enviar um evento real novo e provar webhook, auth, debounce, sessão nova,
  profile/modelo esperado, rota nova e estado isolado;
- provar que nenhuma rota legada respondeu e nenhuma mensagem pública foi
  enviada pela canary.

O serviço live continua separado. O mesmo `message_id` não pode ser processado
pelos dois runtimes com side effects.

### Gate D — teste conversacional por Carlos

Antes de abrir este gate, o controlador deve:

1. verificar health, image ID, release commit e hashes do candidate;
2. confirmar outbound habilitável somente para o contato autorizado;
3. manter provider/payment writes fechados;
4. limpar apenas o estado canary desse contato e sua sessão Hermes clonada;
5. provar zero pendências em state/outbox/shadow/Redis/Supabase canary;
6. avisar Carlos explicitamente que chegou o momento do teste;
7. fornecer cenários e stop condition, sem pedir confirmação de reserva real.

Carlos executa conversas naturais pelo fluxo ManyChat/WhatsApp. O conjunto deve
cobrir ao menos:

- hospedagem com dados distribuídos em vários turnos;
- passeio com seleção interna por ID canônico, sem expor ID;
- pacote ou mudança de ideia/correção;
- uma confirmação natural no máximo, sem efetuar reserva;
- pergunta de pagamento/handoff sem side effect financeiro.

A aprovação exige proveniência do webhook, sessão `leads`, respostas públicas e
estado isolado. Falha de comportamento retorna a RCA; não se abre write E2E.

### Gate E — uma canary E2E autorizada

Somente após Carlos aprovar o Gate D. O provider, workflow, período e plano de
cancelamento são escolhidos nesse momento. Uma autorização separada deve fixar:

- um subscriber;
- um workflow;
- um provider;
- uma reserva;
- uma janela temporal;
- o único gate de write aberto;
- stop/rollback/cancelamento.

Sequência obrigatória:

1. conversa natural;
2. resumo;
3. uma confirmação posterior;
4. um command;
5. um dispatch;
6. read-back da reserva;
7. uma mensagem via outbox;
8. redelivery do webhook com zero novos efeitos;
9. auditoria e cancelamento planejado.

Pagamento real, Pix, Wise, Stripe e múltiplos providers ficam fora da primeira
canary E2E, salvo autorização futura que altere explicitamente o escopo.

### Gate F — rollout gradual

Usa exatamente o image ID do Gate A e somente começa após Gate E verde:

```text
1% / até 100 conversas / mínimo 24 h
→ 5% / até 300 / mínimo 24 h
→ 25% / até 1000 / mínimo 48 h
→ 100%
```

Se o volume real for menor, o teto pode ser reduzido, nunca o tempo mínimo. Cada
estágio exige GO explícito registrado, métricas sanitizadas e rollback testado.

## Stop conditions e rollback

Rollback imediato e `NO-GO` diante de:

- image ID/archive/hash divergente;
- qualquer mudança no runtime operacional durante dark canary;
- write antes da confirmação;
- mais de um command/attempt/provider call;
- outcome incerto com retry automático;
- chamada Stripe/Wise/payment no escopo read-only;
- ManyChat/e-mail/outbox no dark ou ingress fechado;
- uso de state/profile/ledger/outbox live pela canary;
- raw provider payload, PII ou segredo na evidência;
- ausência de read-back E2E;
- ledger ou outbox sem recuperação;
- promessa pública sem evidência;
- aumento anormal de handoff, timeout, repetição ou reset de contexto;
- rota legada ativa para o contato canary;
- impossibilidade de restaurar a imagem live anterior sem rebuild.

O rollback para a imagem anterior não apaga registros comerciais. Uma canary E2E
que possa ter alcançado o provider exige reconciliação/read-back antes de nova
tentativa ou cancelamento.

## Evidência

A Fase 8 cria `docs/refactor/evidence/phase-08/` com, no mínimo:

- `entry-baseline.json`;
- `release-manifest.json`;
- `build-result.json`;
- `dark-canary-result.json`;
- `ingress-result.json`;
- `conversation-readiness.json`;
- `conversation-result.json` somente após o teste de Carlos;
- `e2e-canary-result.json` somente após autorização/execução;
- `rollout-result-<stage>.json` por estágio iniciado;
- raw reports sanitizados, manifest e `SHA256SUMS`;
- validator e workflow de CI da Fase 8.

Cada resultado usa schema fechado, comandos, exit codes, timestamps, commit/tree,
image ID, hashes, capabilities executadas, `rollout` e `phase9_started`.
Artefato ausente por gate ainda não aberto é `blocked`, não `failed`. Artefato
existente stale/inválido é `failed`.

## Estratégia de testes

### Antes de Docker/provider

TDD focused para:

- rejeitar source suja/incorreta e o caminho operacional;
- fechar e validar o env canary;
- proibir mounts/volumes/segredos live;
- bind do image ID/archive/commits/trees;
- garantir `build:` ausente na promoção;
- rollback pelo image ID anterior;
- schema/aggregate dos resultados;
- diagnosticar artefatos blocked/stale sem retorno antecipado.

### Janela pesada econômica

- reutilizar propriedades/faults/mutations da Fase 7 somente se kernel/schema/
  wheel não mudarem;
- rodar suíte integral do runtime candidate uma vez depois da última mudança que
  afete runtime/canary tooling;
- construir a imagem uma vez e nunca repetir sem mudança material;
- executar dark canary uma vez por snapshot de imagem;
- corrigir falha causal com RED/GREEN estreito; mudança na imagem invalida os
  gates subsequentes.

### Revisão independente

Antes do primeiro ingress real e antes da canary E2E, três lanes independentes
cobrem:

1. identidade/digest/rollback;
2. isolamento/zero effects;
3. conversa/proveniência/non-drift.

Timeout, summary ausente, identidade errada ou verdict não conclusivo valem zero.
Não existe aprovação com ressalvas.

## Não objetivos

- não remover legado, aliases ou metadata; isso é Fase 9;
- não corrigir comportamento/prompts fora de finding causal do canary;
- não migrar dados live em massa;
- não usar a árvore operacional como build context;
- não abrir pagamentos reais na primeira canary;
- não testar múltiplos subscribers/providers/workflows no write E2E;
- não promover rebuild ou tag mutável;
- não solicitar a Carlos teste de conversa antes do Gate D;
- não declarar rollout por health/config sem evidência do mesmo digest.

## Gate de fechamento da Fase 8

A Fase 8 fecha somente com:

- entry/design/plan publicados;
- release manifest e image identity autenticados;
- dark canary e ingress real verdes;
- teste conversacional executado por Carlos e autenticado;
- uma canary E2E autorizada, read-back e redelivery verdes;
- rollout gradual concluído ou encerrado em estágio explicitamente aceito pelo
  owner com razão registrada;
- rollback do mesmo host testado sem rebuild;
- revisão terminal 3/3 do snapshot final;
- CI remoto verde e commit remoto autenticado;
- riscos atualizados;
- `phase9_started=false` até decisão explícita posterior.
