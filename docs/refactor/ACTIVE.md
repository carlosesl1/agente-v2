# Controle de execução ativo — Fast-track Agente V2

## Autoridade

- Estado: `MAYA_OWNED_HOLDER_INTERPRETATION_LOCALLY_QUALIFIED`
- Branch obrigatória: `maya-v2-operational-readiness`
- Worktree obrigatória: `/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout`
- Especificação ativa: `docs/superpowers/specs/2026-08-03-maya-owned-reservation-holder-interpretation-design.md`
- Plano ativo: `docs/superpowers/plans/2026-08-03-maya-owned-reservation-holder-interpretation.md`
- Base funcional do reparo: `1219ff2c12efa989f44f5caa7364363011ea281d`
- Autoridade: solicitação explícita de Carlos em 2026-08-03 para Maya receber a mensagem original integral, atribuir semanticamente nome/e-mail/país ao titular e preservar telefone exclusivamente autenticado pelo binding ManyChat/WhatsApp
- Rollout: `LOCAL_FAKE_ONLY_IMPLEMENTATION`
- Provider writes reais: `BLOQUEADOS POR GATES INDEPENDENTES`
- ManyChat público real: `BLOQUEADO ATÉ NOVA AUTORIDADE ASSINADA`

## CORREÇÃO DO GATE DE CONSULTAS READ-ONLY

Carlos autorizou explicitamente em 2026-08-04 simplificar o processo após a conversa natural provar que Maya emitiu consultas válidas de hospedagem e passeio, mas o controlador as descartou porque o perfil não tinha `country_code`. Consulta read-only não é seleção nem efeito comercial: com telefone ManyChat/WhatsApp autenticado e fresco, disponibilidade, preço e descrição permanecem elegíveis mesmo que nome, e-mail ou país ainda estejam incompletos. `select`, `confirm`, comandos, reservas e provider writes continuam exigindo o perfil efetivo completo nos boundaries existentes.

- correção funcional: `d2f5dc2c7947c3be440b7e4ee5df8bd0686bcb71`, tree `739304b20b4622f5482ec93572172fc80aeb5f1b`;
- RED causal: dois selectors falharam porque `private_profile_complete=false` suprimiu lodging/activity reads e a segunda chamada da Maya;
- GREEN focado: `2 passed`; irmãos de telefone ausente/futuro/expirado, privacidade, read normal e package: `9 passed`;
- regressão proporcional de executor, perfil efetivo, reducer, split-origin E2E e superfície comercial: `122 passed`;
- gate oficial clean-env: `1489 passed, 7 deselected, 2940 subtests passed`;
- Ruff, compileall, `fasttrack-boundaries` e `git diff --check`: verdes;
- o witness com país ausente executa uma leitura Cloudbeds fake e uma Bókun fake, entrega duas observations e mantém zero command/relay;
- o witness post-read `select` continua retornando `profile_completion`, sem `AwaitingConfirmationState`, command ou relay;
- nenhum provider real, ManyChat, pagamento, deploy ou rollout foi chamado.

- NEXT: obter revisão independente sobre o SHA final que contém código e este ledger; parar antes de push, CI, imagem, runtime, provider real ou delivery.

## CONTINUIDADE DURÁVEL DE CONSULTAS ENTRE TURNOS

O teste real de 2026-08-04 comprovou que a correção do gate sem `country_code` leva as consultas até Cloudbeds/Bókun, mas também revelou que disponibilidade, preços, opções e indisponibilidade existiam somente nas observations do próprio turno. Carlos solicitou corrigir a continuidade completa. O owner permanece o artifact/receipt autenticado do boundary: o turno seguinte recebe uma projeção pública, limitada a oito observations commitadas do mesmo `lead_key`, com uso fixo `recap_only`.

- `ModelRequest.observations` continua exclusivo para provider reads do turno atual;
- `consultation_history` permite resumo/comparação posterior e preserva resultados positivos e negativos;
- histórico nunca autoriza `select`, `confirm`, reserva, pagamento, handoff ou qualquer efeito; ação sobre opção histórica exige read fresco no turno atual e todos os gates tipados existentes;
- wire não inclui offer ID, request/evidence hash, private binding hash ou payload bruto do provider;
- adulteração do artifact falha como `DataCorruption`, e consulta para outro `lead_key` retorna vazio;
- RED causal: a conversa de dois turnos falhou porque `ModelRequest` não tinha `consultation_history`;
- GREEN focado: `2 passed`; regressão proporcional de adapter/executor/boundary/reads: `130 passed, 84 subtests passed`;
- gate oficial clean-env: `1491 passed, 7 deselected, 2940 subtests passed`;
- Ruff isolado nas superfícies do workflow, `fasttrack-boundaries`, compileall e `git diff --check`: verdes.

O primeiro teste real da candidata de continuidade (`3bde613`) passou nos quatro turnos informativos, inclusive os dois recaps sem novo read, mas revelou um segundo defeito no turno de pressão para reservar package: após reads atuais positivos/negativos, a Maya repetiu `read_requests`; o adapter iniciou reparo e o deadline/lease, dimensionados para uma única completion, expiraram antes do commit. A candidata foi desmontada com 4 eventos commitados, o 5º sem receipt, zero em todos os owners de efeito, zero delivery, autoridade vazia, credenciais removidas, zero contêineres e legado HTTP 200.

A correção substituta fecha a classe:

- com `observations` atuais, read recursivo vira fallback determinístico imediato, `read_requests=()`, `selection_requested=false` e nenhum novo I/O; observation negativa produz resposta verdadeira de indisponibilidade/nada reservado;
- o budget produtivo cobre até três completions, duas tentativas de protocolo por completion e overhead de provider; o lease do inbox vence somente após esse budget;
- RED/GREEN focal: recursive read exigia duas inferências e agora usa uma; o budget produtivo falhava em `50s` contra `300s` esperados e agora está vinculado ao contrato;
- regressão proporcional atual: `155 passed, 84 subtests passed`;
- suíte oficial clean-env atual: `1492 passed, 7 deselected, 2940 subtests passed`.

Os dois revisores read-only expiraram sem veredito terminal. Um witness de handoff não era causal ao histórico: pedido humano de equipe deve continuar possível, e o gate externo permanece fechado. O segundo witness era válido: 33 ofertas commitadas excediam o bound de 32 no próximo `ModelRequest`. A projeção agora envia as primeiras 32, preserva `offer_count=33`, marca `offers_truncated=true` e instrui a Maya a não alegar conjunto completo. O witness 33→32 passou; regressão e suíte oficial mantiveram os mesmos totais verdes.

O revisor final do SHA `818705e` encontrou um finding Important adicional: no fallback recursivo, Cloudbeds e Bókun negativos juntos citavam apenas o passeio devido a `if/elif`. O witness foi reproduzido em RED e o fallback agora produz uma resposta combinada para hospedagem+passeio, ainda com uma única inferência, zero reads, zero selection e zero effects. O focal passou `2 passed`; regressão e suíte oficial permaneceram `155 passed, 84 subtests` e `1492 passed, 7 deselected, 2940 subtests`.

- NEXT: autenticar a imagem do SHA final e concluir relatório/cleanup; push, CI remoto, publicação, deploy e delivery continuam não autorizados.

## REPARO SPLIT-ORIGIN DE PERFIL ATIVO

Carlos autorizou em 2026-08-03 que Maya receba a mensagem original integral e atribua semanticamente nome completo, e-mail e país ao titular da reserva. O controlador valida, canonicaliza e persiste os fatos estruturados da Maya antes de qualquer read, sem extrator regex nem marcadores; ManyChat é fallback nesses três campos e fonte exclusiva do telefone autenticado. PII permanece fora de projeção, history, artifacts/evidence, logs, exceções e `repr`. Coleta/correção pode produzir resumo no mesmo turno, mas nunca command/relay; confirmação posterior revalida o cliente efetivo e o material selecionado.

| Task | Estado | Commit |
|---|---|---|
| 1. Autoridade, spec e plano | `DONE` | `1329eeec5d5afe7729756d61ff181febaa99ec6d` |
| 2. Owner SQLite privado e canonicalização | `DONE` | `0b7ac7e0d9d51a33581988852d631d1fd05b656c` |
| 3. Resolver parent-owned de cliente efetivo | `DONE` | `2e3fb81559268761b423055bc991b933d8797ee3` |
| 4. Protocolo collection-only e privacidade de artifacts | `DONE` | `f08a0af949464ff9426056122ab002b3cc6e0fe5` |
| 5. Wiring/settings/close do store privado | `DONE` | `b0283b6fea67f9e6e6dd4b7ad2dad69de975ed57` |
| 6. E2E Cloudbeds fake, replay e regressões | `DONE` | `f3f143b883adf7c02c74e75356286e245ca63938` |
| 7. Qualificação, revisão, push e CI exatos | `SUPERSEDED BY AUTHORITY OVERRIDE` | `314376d4a946dcc8523015bf1da1d8aed1699d7d` |
| 8. Conversa-first para nome/e-mail/país | `DONE — SUPERSEDED BY SAME-TURN CONTINUATION` | `8c5db0724a74a52f5d2319f80787a9a2a4f8a0e9` |
| 9. Continuar até resumo após coleta/correção privada | `DONE — INPUT PATH SUPERSEDED` | `b110662461dca1ce5d2f15c0d6bfe366b9ce00a2` |
| 10. Maya interpreta titular a partir da mensagem original | `LOCALLY QUALIFIED — REVIEW NEXT` | `a07d638d0cea7962efabd1d9ffb68a1660645170` |

Evidência Task 2:

- RED causal: import do owner ausente;
- GREEN focado: `6 passed`;
- blast radius de perfil/modelo/coleta: `44 passed`;
- Ruff, compileall e `git diff --check`: verdes;
- zero cliente HTTP/provider/ManyChat e zero efeito externo nos arquivos da task.

Evidência da candidata de qualificação `314376d4a946dcc8523015bf1da1d8aed1699d7d`:

- três revisores read-only expiraram sem veredito, mas seus transcripts produziram witnesses causais que foram reproduzidos em RED antes das correções;
- hardlinks tardios entre owners SQLite, inclusive audit lazy, são rejeitados antes de schema write; bootstrap incompatível e facts adulterados/unbacked falham fechados;
- a revisão `deleg_7214bfd0` bloqueou a candidata anterior com dois witnesses adicionais: row privada com `value_hash` recalculado divergia silenciosamente do journal, e schema `STRICT` sem os `CHECK` obrigatórios era aceito;
- os dois witnesses foram reproduzidos em RED; o journal agora autentica hashes por campo e seu conteúdo completo, e o bootstrap compara também o SQL canônico integral das tabelas;
- a revisão `deleg_f7863e12` bloqueou a candidata sucessora porque `turn_supplied_fact_names()` não autenticava journals de turnos que reapresentavam valor igual e por isso não eram referenciados por rows atuais;
- o witness de crash/retry foi reproduzido em RED; a API de presença agora valida evento, material por campo, hash integral e timestamp antes de retornar somente os nomes;
- a regra anterior em que valores conversacionais perdiam para ManyChat válido foi explicitamente revogada por Carlos; nome/e-mail/país conversacionais válidos agora vencem;
- telefone ausente/futuro/expirado bloqueia todas as leituras de provider e todo command; somente `KNOWLEDGE` local permanece elegível;
- binding ManyChat é reautenticado na decisão e imediatamente antes de commit com command; mudança material aborta sem command/relay;
- regressão proporcional após os reparos: `94 passed`;
- gate oficial local após os reparos: `1482 passed, 7 deselected, 2940 subtests passed`;
- Ruff, `fasttrack-boundaries`, compileall e `git diff --check`: verdes;
- nenhum POST Cloudbeds/Bókun, pagamento, entrega ManyChat, deploy ou alteração de reserva real foi executado.

Evidência Task 8:

- autoridade corrigida e aprovada por Carlos: nome/e-mail/país conversacionais válidos vencem ManyChat divergente; telefone permanece ManyChat-only;
- spec/ledger de autoridade: `f33f52b`; plano executável: `506d2f3`;
- RED do resolver: os dois witnesses falharam causalmente porque a política antiga retornava conflito/not-ready;
- GREEN do resolver/source-aware identity: `2 passed`; arquivo completo: `10 passed`;
- commit funcional do resolver: `2c89e013a4ec27d168483ee90f978c9811e93c60`;
- RED do fence pré-commit: troca de telefone autenticado e e-mail ManyChat selecionado continuaram bloqueando, enquanto mutação apenas de nome/e-mail/país ManyChat não utilizados falhou causalmente no hash bruto antigo;
- GREEN dos fences: mutações ManyChat não utilizadas são aceitas tanto entre leitura/decisão quanto imediatamente antes do commit; troca de telefone ou de campo ManyChat efetivamente selecionado segue bloqueada com zero command/relay;
- commit funcional de reautenticação source-aware: `8c5db0724a74a52f5d2319f80787a9a2a4f8a0e9`;
- E2E split-origin completo: `6 passed`;
- regressão proporcional de perfil, coleta, SQLite privado, reducer, executor, Cloudbeds, Bókun e pagamento: `264 passed`;
- gate oficial local com os sete exclusions históricos autenticados no workflow: `1486 passed, 7 deselected, 2940 subtests passed`;
- Ruff, `fasttrack-boundaries`, compileall e `git diff --check`: verdes;
- `runtime=dark_read_only`, `kill_switch=true`, `post_budget_armed=false`, `SAFE_FOR_BROAD_ROLLOUT=false`;
- nenhum POST Cloudbeds/Bókun real, pagamento, entrega/reset ManyChat, deploy ou alteração de reserva real foi executado.

Evidência Task 9:

- decisão de produto: remover exclusivamente o turno intermediário após coleta/correção válida de `full_name`, `email` ou `country_code`, mantendo confirmação/comando para lote posterior;
- spec: `5b1116f0aaad28ab9b3d1814eed0c759d9c5a895`; plano TDD: `c96d0282fdb740a50284c69895bb4d09ea1a5d2d`;
- RED causal `fe2f903c905ddaf76a24fece6946eb3b3c0acd13`: persistência privada ocorreu, mas o executor respondeu `Obrigado. Guardei esses dados...` e suprimiu read/resumo;
- GREEN funcional `232ceea8e7e1af3425cfc1aa8b48e67c114839f4`, tree `ffaf90f4dac3ebc0db728057bc0ad03b20bd5b4c`: coleta parent-owned válida segue para read/summary; inválida/model-only permanece collection-only;
- correção válida substitui o workflow pendente por novo `AwaitingConfirmationState` ligado ao snapshot corrigido; tentativa `confirm` no mesmo lote é rebaixada antes de derived reads e produz zero command/relay;
- handoff explicitamente solicitado permanece regido pela política existente; replay committed não repete modelo/read;
- E2E split-origin completo: `9 passed`; executor completo: `57 passed`;
- regressão proporcional de owner/coleta/perfil/reducer/executor/providers/pagamento: `234 passed`; modelo/settings/composition/host: `74 passed`; total distinto: `308 passed`;
- gate oficial local com sete exclusions históricos: `1489 passed, 7 deselected, 2940 subtests passed`;
- Ruff, `fasttrack-boundaries`, compileall e `git diff --check`: verdes;
- `runtime=dark_read_only`, `kill_switch=true`, `post_budget_armed=false`, `SAFE_FOR_BROAD_ROLLOUT=false`;
- nenhum POST Cloudbeds/Bókun real, pagamento, entrega/reset ManyChat, deploy ou alteração de reserva real foi executado.

Evidência Task 10:

- spec aprovada pela solicitação numerada: `990eafd80a5f2a768e7a3a2bd7b6ccf2a13ebbc1`; plano TDD: `a51dbb00b158265f3bf4804b59e23d5bc657c3f5`;
- RED causal no wire: a mensagem original já chegava ao campo `message`, mas o prompt ainda ordenava markers/collection-only e não definia titular versus esposa/terceiro;
- contrato Maya: `32b619692bbc8b859cfdb713a4457b5cc1418024`; executor/store sem extrator determinístico: `2ee10b598185bbe77166eb5e96146baa9aee050c`; E2E e correção sem confirmação obsoleta: `52e11730f0ce31eef5d75e4b4c6eb65dba6ce395`;
- `v2_application/private_customer_collection.py` e seus testes foram removidos; scan estático confirma ausência de `collect_private_customer_facts`, `PrivateCustomerCollection` e `sanitized_message` em produção;
- Maya recebe `combined_text` integral em todas as chamadas; fatos `full_name`, `email` e `country_code` estruturados são canonicalizados/persistidos antes de reads e removidos do proposal público/artifacts; telefone conversacional é descartado como identidade e o draft usa o binding ManyChat autenticado;
- matriz semântica cobre lead versus esposa/hostel incidental, terceiro explicitamente nomeado titular, ambiguidade com pergunta natural e telefone digitado; executor+adapter `80 passed`; E2E split-origin `9 passed`; regressão proporcional integrada `214 passed`;
- correção válida pode produzir novo resumo no mesmo turno; correção junto de confirmação exige `adjust/revoke`; zero command/relay no lote da atualização; confirmação posterior, replay e exatamente um POST Cloudbeds simulado permanecem provados;
- os três revisores finais da candidata anterior expiraram sem veredito; seus transcripts não foram tratados como aprovação e produziram três witnesses reproduzidos em RED: `adjust+preserve` conservava resumo obsoleto, reply model-owned podia ecoar PII em artifacts e regex legado ainda promovia DOB/gênero do texto bruto;
- hardening funcional `ffa0cad6ef902fb0372d8fe1f83b8def7518a9ea`, tree `c0dffa1ceac81d839c01312a08a882ef6b61dcdb`: toda atualização privada pendente revoga o resumo salvo quando chega a novo `select`; coleta/correção/handoff aceitos usam resposta parent-owned; DOB/gênero não são criados pelo parent;
- novos `kernel_decision` artifacts persistem somente commitment de state/version/command hashes; commit e semantic scan autenticam o conteúdo contra rows reais e continuam aceitando artifacts históricos com decisão completa;
- a revisão final `deleg_f88def25` ficou `CLEAR` para correção/no-effect e para o commitment do kernel, mas bloqueou a candidata `5c26bfdba5ae0aceb3e90fddb32e255ad7d0e44b`: com perfil ainda parcial, Maya podia aceitar `full_name`, ecoá-lo no reply e propor um provider read que o controlador filtrava para zero; o `read_requests` obsoleto ainda preservava o eco no reply público e nos artifacts;
- o witness foi reproduzido em RED e corrigido no chokepoint funcional `a07d638d0cea7962efabd1d9ffb68a1660645170`, tree `ffaa44497702807e3180f7693f86e835847099a5`: a proposta é normalizada para o conjunto efetivo de reads antes da seleção final; fato privado permanece no owner, provider calls ficam em zero e reply/artifacts usam texto parent-owned sem o valor;
- regressão final focada de executor/adapter/E2E/artifact graph: `93 passed, 11 subtests passed`;
- regressão proporcional ampla: `543 passed, 39 subtests passed`; gate oficial clean-env da candidata corrigida com as sete exclusions históricas do workflow: `1487 passed, 7 deselected, 2940 subtests passed`;
- Ruff, `fasttrack-boundaries`, compileall, `git diff --check` e scan do extrator: verdes;
- `runtime=dark_read_only`, `kill_switch=true`, `post_budget_armed=false`, `SAFE_FOR_BROAD_ROLLOUT=false`;
- nenhum push, deploy, POST real, pagamento, entrega/reset ManyChat ou alteração de reserva real foi executado.

- NEXT: congelar o SHA que contém este ledger e obter parecer independente `CLEAR` sobre esse SHA exato; parar antes de push/deploy/WhatsApp real conforme a autoridade atual.

## REPAROS OPERACIONAIS AUTORIZADOS

Carlos autorizou em 2026-07-27 a correção de conversa pré-reserva, relógio de consultas, fallback de protocolo, conhecimento comercial e controle operacional, além da preparação de todas as funções sob testes limitados.

O candidato está qualificado localmente. O próximo avanço autorizado é: revisão final, push do branch, CI no SHA exato, imagem imutável e dark canary com todos os efeitos externos fechados. Provider writes e entrega ManyChat continuam bloqueados até qualificação e autoridade separadas.

## CONFIRMAÇÃO BÓKUN POR BOOKING ID ATIVA

Carlos autorizou em 2026-07-31 que um submit Bókun V2 aceito por HTTP 2xx sem marcador explícito de falha em qualquer branch de reserva do envelope, com um único booking ID principal e sem aliases conflitantes, seja evidência monotônica de criação. O caminho síncrono V2 não executa mais GET depois de obter esse ID: retorna `confirmed`, o adapter grava somente o fingerprint opaco como `EFFECT_CONFIRMED`, o ledger consome um único slot e o completion projector materializa `Seu passeio foi confirmado.` uma única vez.

Permanece fail-closed:

- resposta sem booking ID, IDs principais conflitantes, status não-2xx mesmo com ID, `success:false` no root ou em branch de reserva mesmo com HTTP 2xx + ID, timeout ou erro ambíguo após submit continuam `CALLED_UNKNOWN`, sem retry;
- activity/passenger booking IDs não contam como booking ID principal;
- produto, data, horário, rate, composição adulto/criança, manifesto e economia BRL continuam vinculados antes do submit;
- o caminho Bókun legado e Cloudbeds não mudaram;
- ManyChat, pagamento, cancelamento, e-mail e handoff continuam fechados;
- auditoria GET posterior, se adicionada, será observador read-only separado e nunca poderá rebaixar uma criação já confirmada nem repetir submit.

Evidência local sobre o código funcional `09afb0b21b455f69a4137d174daabfe5c46a558b` e as provas duráveis no HEAD sucessor:

- RED reproduziu o quarto GET indevido depois do submit com `booking-123`;
- revisão do primeiro candidato reproduziu `409/422 + booking ID` sendo aceito indevidamente; o SHA foi invalidado e o novo RED exige status HTTP 2xx;
- escrutínio subsequente reproduziu `HTTP 200 + booking ID + success:false`; o SHA sucessor também foi invalidado e o RED exige ausência de marcador explícito de falha;
- revisão terminal reproduziu `HTTP 200 + booking.success:false + booking ID`; o classificador agora percorre uma vez o envelope de reserva e exclui branches locais de activity/passenger;
- transporte Bókun completo: `52 passed`;
- jornada transporte/reservas/outcome/completion: `83 passed`;
- regressão causal de Bókun, conversa, horário, 2+1 e produção: `217 passed`;
- catálogo comercial fechado: `1 passed`;
- comando oficial de CI local: `1328 passed, 7 deselected, 2940 subtests passed`;
- Ruff, `fasttrack-boundaries` e `git diff --check`: verdes;
- nenhum provider, ManyChat, pagamento, cancelamento, e-mail ou handoff foi chamado para qualificar esta correção.

- NEXT: obter `APPROVE` independente sobre o SHA exato do HEAD; depois publicar o mesmo SHA, exigir CI remoto verde, produzir imagem OCI imutável e atestar dark canary com efeitos fechados antes de qualquer tráfego de clientes.

## REPARO BÓKUN MULTI-PASSAGEIRO ATIVO

Carlos aprovou em 2026-07-30 paridade completa com o V1 para grupos de adultos e crianças, com dados individuais por passageiro. O reparo preserva manifesto privado, proposta assinada, cotação por categoria, idempotência, submit único e booking ID principal inequívoco. Não está autorizado nenhum write real de provider durante a implementação.

| Task do reparo | Estado | Commit |
|---|---|---|
| 1. Ativar cadeia de autoridade | `DONE` | `63dffe5cee86dd2117fcd2b6703f47ded181dca3` |
| 2. Domínio assinado de passageiros | `DONE` | `4adbdf43222ca0851dcd16c4e80585ad61bd7b61` |
| 3. Protocolo v6 e manifesto privado | `DONE` | `8dccad1b33aab7135498ba2cfcab2b5b2265e9ec` |
| 4. Reads por composição adulto/criança | `DONE` | `4883bd2f46ad5026bac6a2f710bc0e45cd054071` |
| 5. Coleta e seleção de grupos | `DONE` | `6f39fcf7bf76feff9f43a94c6f3d98b783e9b7c6` |
| 6. Dispatch Bókun v2 | `DONE` | `9f38fb7f616b368d97d40e6ef819889936d948e3` |
| 7. Transport e read-back exato | `DONE` | `d9935f28049b19316de38dbf0973cea91e236c7a` |
| 8. Regressões e qualificação local | `DONE` | `3cb84200aaab3d76344cba06623a7eaa70790865`, `c5ee69d83345901b9ab16a36b5d18d0904f1ba50` |
| 9. Revisão terminal e candidata | `CANDIDATE_FROZEN` | código `5b5dc5c88783e1c286d64b3fc915b600ea74442a`; evidência e controle no HEAD |
| 10. Prova conversacional de grupo e boundary 2+1 | `DONE` | `373221f1326701bea9e1581720ffe95ab6f886be` |

- NEXT: obter `APPROVE` independente sobre o SHA exato do HEAD, incluindo o reparo conversacional `373221f1326701bea9e1581720ffe95ab6f886be`; somente então publicar o sucessor, executar CI, produzir nova imagem OCI imutável e repetir o dark canary sem relay.

### Candidata multi-passageiro congelada

A candidata incorpora os findings reproduzíveis das revisões independentes e do primeiro dark canary, sem executar writes reais:

- correção explícita de passageiro revoga resumo e autoridade assinada anteriores;
- intents não corretivos não podem persistir mutações do manifesto;
- valores privados persistidos não retornam ao modelo; somente presença allowlisted é exposta;
- `DispatchRequest`, argumento externo e comando assinado exigem a mesma idempotency key antes do provider;
- categorias adulta/infantil exigem moeda BRL completa e a moeda, o valor e o `rate_id` publicados vêm da mesma rate selecionada;
- categorias públicas do Bókun são selecionadas somente quando há um único ID elegível: aliases internos com `ageQualified=false` são ignorados, valores malformados ou múltiplas categorias públicas falham fechados;
- quote/cart/read-back rejeitam aliases e bindings conflitantes de oferta, status, valor, moeda, composição e booking ID;
- IDs internos de activity/passenger booking não são confundidos com o ID principal da reserva;
- `409` e qualquer ambiguidade após submit sem booking ID inequívoco permanecem `CALLED_UNKNOWN`; não há retry otimista;
- no caminho V2 atual, um submit aceito com booking ID principal inequívoco confirma a criação imediatamente; read-back não bloqueia a confirmação.

Evidência local no código `5b5dc5c88783e1c286d64b3fc915b600ea74442a`:

- transporte/reservas focados: `81 passed`;
- suíte V2 e contrato do prompt v6: `368 passed`;
- comando oficial de CI: `1314 passed, 7 deselected, 2940 subtests passed`;
- Ruff, compileall, `fasttrack-boundaries`, `git diff --check`, ausência de `uv.lock` e árvore limpa: verdes;
- witness real somente leitura: metadata com um `ADULT ageQualified=true` e três aliases internos `ageQualified=false`; o código corrigido selecionou a categoria pública com `3 GET`, sem POST ou write;
- a imagem do SHA `400e0f4ae2a5c76a1899b7f95490f170fd1690bf` passou CI, mas seu dark canary falhou fechado nesse witness; os containers foram removidos e o audit registrou zero eventos, comandos, outbox ou efeitos;
- branch remoto permanece em `400e0f4ae2a5c76a1899b7f95490f170fd1690bf` e o runtime dark anterior permanece em `71ff137e9d9d35cc8f8cd1211ceca0744dd0d6dc` até nova aprovação e qualificação operacional.

### Evidência conversacional de grupo 2+1

O teste com modelo real encontrou e fechou duas lacunas antes de qualquer efeito externo: o extrator explícito zerava crianças ao reconhecer adultos, e o boundary de leitura legado colapsava a composição para participantes adultos. O reparo `373221f1326701bea9e1581720ffe95ab6f886be` preserva `adults=2`, `children=1` num wire interno aditivo, sem alterar o catálogo público fechado da Fase 7, e expõe essa composição no resumo confirmado pelo lead.

Evidência local sanitizada sobre os mesmos bytes do commit funcional:

- conversa natural de dois turnos com o `HermesModelAdapter` real: resumo e confirmação;
- resumo público explícito: `2 adultos e 1 criança`;
- comando durável: um `book_activity`, três passageiros tipados, uma relay row e idempotency key estável;
- replay da confirmação: mesmo receipt, sem nova chamada ao modelo e sem nova leitura;
- provider write adapter ausente, `0` submit Bókun, `0` ManyChat e `0` pagamento; todos os gates reais continuaram fechados;
- comando oficial de CI local: `1319 passed, 7 deselected, 2940 subtests passed`;
- Ruff, compileall, `fasttrack-boundaries`, `git diff --check` e ausência de `uv.lock`: verdes;
- as sete deselections históricas foram reproduzidas sem o patch no SHA base `08f00b3a601f86d7e4fe5171f28b82ad61ef0cb4`.

### Decisão de topologia da Task 7

Task 7 implementa somente `CompletionPolicy` pura e outbox público local/durável. Task 9 correlaciona os receipts dos stores existentes no composition root. Não será criado store global paralelo de completion.

## Regra para não confundir novo e antigo

### Único produto novo

O produto é o host próprio do V2 nesta worktree. O caminho produtivo começa em `v2_host` e usa `v2_application`, `v2_contracts`, `v2_adapters` e a cápsula existente `reservation_*`.

### Fonte antiga somente leitura

`/home/ubuntu/chapada-leads-hermes` pode ser lido apenas para extrair comportamento técnico e testes sanitizados dos providers. É proibido:

- editar ou commitar nesse repositório durante o fast-track;
- importá-lo via pacote, caminho, `PYTHONPATH`, subprocesso ou container;
- chamar seu `app`, planner, agente, `LeadState`, orchestrator ou executor genérico;
- usar seu banco como estado do V2;
- delegar a ele decisão comercial, reserva, pagamento ou mensagem.

### Documentos históricos

`docs/refactor/04-phased-delivery-plan.md`, documentos antigos da Fase 8, manifests e evidências continuam preservados, mas não são planos executáveis. Só esta cadeia autoriza trabalho:

```text
AGENTS.md
→ docs/refactor/ACTIVE.md
→ especificação ativa
→ plano ativo
```

Se houver conflito, o documento anterior nessa cadeia vence. O agente não tenta conciliar por conta própria.

## Loop obrigatório de execução

Para cada task do plano:

1. confirmar branch, worktree e `git status`;
2. ler apenas a task `NEXT` e suas interfaces de entrada;
3. escrever o teste RED indicado;
4. executar somente o selector focado e confirmar a falha causal;
5. implementar o mínimo para GREEN;
6. executar selector focado, regressões diretamente afetadas e guard de fronteiras;
7. revisar o diff da task e executar `git diff --check`;
8. commitar a implementação da task isoladamente;
9. atualizar neste arquivo: task concluída, SHA do commit funcional e nova `NEXT`;
10. commitar essa atualização de controle separadamente, sem código funcional;
11. continuar automaticamente para a próxima task.

A execução só pode parar quando:

- um teste/gate material permanece vermelho;
- o contrato aceito é insuficiente ou contraditório;
- falta credencial/configuração indispensável que não pode ser descoberta;
- o próximo passo realizaria provider write, mensagem pública, deploy ou rollout sem autorização específica;
- existe outcome incerto que exige reconciliação/handoff.

Não parar apenas para narrar progresso entre tasks verdes.

## Perfil de velocidade

- execução inline pelo controller;
- sem subagentes de mapeamento;
- RED/GREEN focado por task;
- uma regressão proporcional por task;
- uma suíte integral e uma revisão final no candidato congelado;
- sem repetir gates pesados quando os bytes relevantes não mudaram;
- nenhum atalho em idempotência, receipts, claims, fencing ou separação LLM/kernel.

## Baselines históricas excluídas dos gates ativos

- `tests/test_phase7_package.py` já falha no commit-base `8f73ee8b4bf40d6ea458a7fac3394aab756c1d88`: o artefato histórico exige metadata `0.7.0`, enquanto a branch já declarava `0.8.0`. O fast-track preserva os seis pacotes de `[tool.phase7-wheel]` e usa `[tool.v2-fasttrack]`; não altera esse teste histórico.
- `tests/test_phase7_closeout.py::Phase7EntryContractTests::test_wheel_bootstrap_is_closed_and_stdlib_only` mantém o mesmo pin histórico `0.7.0`.
- `tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_evidence_validator_reflects_current_terminal_artifacts` exige `closed_imports` e `package_version` da Phase 7 anterior à composição V2.
- `tests/test_phase8_entry.py::Phase8EntryTests::test_phase_index_keeps_slice_zero_and_rollout_closed` exige a frase de índice pré-fast-track `design aprovado`, substituída pelo estado canônico de fast-track ativo.

## Estados de task

| Task | Estado | Commit |
|---|---|---|
| 1. Control plane e pacotes | `DONE` | `c9f19c131ce9ff80020e1c0c0a8d8262a821cbfb` |
| 2. ManyChat ingress e inbox | `DONE` | `a2d57c4fa2938345c5ba745cdbb79c26bc292eec` |
| 3. Turno canônico e consultas | `DONE` | `b495b7c919046192210988ff5e5749cfa063c80b` |
| 4. Reservas Cloudbeds/Bókun | `DONE` | `f2e6d3bd309381319dd6f2a2dd78b6aa14c14014` |
| 5. Iniciação Stripe/Wise/Pix | `DONE` | `728403a64a3e590c24a5c4840a7009959101c359` |
| 6. Evidência e settlement | `DONE` | `73a40bb0f3717d30a51bc2dced7c4c870b9e0ea6` |
| 7. Pós-pagamento e conclusão | `DONE` | `85a2eab93b9b6d812226f3ec1c9e526563c3ef4d` |
| 8. Pacote, recuperação e handoff | `DONE` | `d948717533f4081239ebf91c948db1ebbb9f0f83` |
| 9. Composição, E2E e qualificação | `DONE` | `45b2fe4c488653c9ea0d3dd7432bdd3c3bca39cd`; imagem `sha256:f3247f69bd16bccc623fbc0508db2acb1ab44e23631a11dc07737282ba742ece` |

## Gate de publicação

Nenhuma task individual autoriza uso público. A primeira liberação externa exige cumulativamente:

- Tasks 1–9 concluídas;
- suíte ativa integral verde no mesmo commit, com exclusões históricas exatas registradas acima;
- imagem única construída do commit aprovado;
- writes e delivery fechados no dark canary;
- conversas completas de hospedagem, passeio e pacote com providers fake;
- reads reais verdes;
- autorização separada para provider write controlado;
- autorização separada para ManyChat allowlisted;
- teste humano de Carlos;
- decisão explícita de rollout.
