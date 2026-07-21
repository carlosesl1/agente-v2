# Registro de riscos

Escala: probabilidade e impacto de 1 (baixo) a 5 (crítico).

| ID | Risco | P | I | Sinal | Mitigação | Owner | Estado |
|---|---|---:|---:|---|---|---|---|
| R01 | Patches continuam sendo aplicados no legado durante refatoração | 4 | 5 | novos guards/status | freeze de mudanças não críticas; RCA antes de exceção | tech lead | aberto |
| R02 | Migração big bang quebra estados ativos | 3 | 5 | conversões sem fallback | dual-read/single-write e shadow | domain | aberto |
| R03 | Kernel novo replica bugs do legado | 3 | 5 | testes portados literalmente | testes por invariantes e incidentes, não implementação | QA/domain | aberto |
| R04 | Provider chamado duas vezes após crash | 3 | 5 | attempt > 1 | comando durável + ledger + outcome incerto; restart/contention bilateral | execution | mitigado |
| R05 | Label volta a controlar identidade | 3 | 4 | matching por nome | `offer_id` opaco, seleção exata e tests metamórficos/mutantes | lookup | mitigado |
| R06 | Canary não corresponde ao deploy | 4 | 5 | rebuild, manifest digest OCI, image ID ou archive hash divergente | build único; promover pelo mesmo manifest digest OCI; image ID/archive são evidências suplementares | release | aberto |
| R07 | Segredo/PII entra no novo repo | 2 | 5 | scanners/grep | `.gitignore`, validator e revisão | security | aberto |
| R08 | Outbox perde mensagem após efeito comercial | 3 | 5 | backlog/stuck lease | store durável, lease/recovery, fault injection e receipt idempotente | messaging | mitigado |
| R09 | E-mail opcional bloqueia handoff | 3 | 3 | tag aplicada sem reply | matriz required/optional por configuração | handoff | aberto |
| R10 | Timeout continua distribuído | 4 | 4 | budgets diferentes | `TurnCoordinator` único; worker independente | runtime | aberto |
| R11 | Estado tipado vira outro metadata bag | 3 | 4 | `dict[str, Any]` em domínio | DTOs fechados e schema versionado | domain | aberto |
| R12 | Testes verdes por mocks permissivos | 4 | 5 | fake aceita payload impossível | provider snapshots reais sanitizados | QA | aberto |
| R13 | Rollout abre mais de um workflow de uma vez | 2 | 5 | gates múltiplos alterados | canary por workflow/provider | release | aberto |
| R14 | Pagamento acopla novamente confirmação da reserva | 3 | 4 | mudança de método reabre draft | workflow financeiro posterior e separado | payments | aberto |
| R15 | Legado nunca é removido | 4 | 4 | dual paths permanentes | Fase 9 obrigatória, métricas de remoção | tech lead | aberto |
| R16 | Disco/capacidade interrompe evidências/workers | 3 | 4 | uso >85%, backlog | quotas, alertas e backend externo | operations | aberto |
| R17 | Corpus de witness é interpretado como E2E do legado | 3 | 4 | claim “runtime reproduzido” | classificação explícita, source map e limites documentados | QA | monitorado |
| R18 | Working tree legado muda e invalida o source map | 3 | 4 | HEAD/status canônico diverge | verificação read-only de HEAD/status e símbolos no closeout | tech lead | monitorado |
| R19 | Trace sintético codifica uma premissa incorreta | 3 | 5 | cenário sem owner/fonte verificável | revisão independente, símbolo validado e futuro replay no boundary real | QA/domain | aberto |
| R20 | Assinatura omite campo executável introduzido por adapter futuro | 3 | 5 | payload de comando contém campo sem projeção canônica | toda extensão de DTO exige teste metamórfico e revisão do canonical subject | domain/adapters | aberto |
| R21 | Estado persistido combina draft/resumo/confirmação/comando inconsistentes | 2 | 5 | round-trip aceita objetos cruzados | invariantes cruzadas, identidade determinística e testes de JSON adulterado | domain | mitigado |
| R22 | event IDs/fingerprints crescem sem limite em workflows anormalmente longos | 3 | 3 | estado serializado cresce linearmente | definir retenção/checkpoint com idempotência durável na Fase 5 | persistence | aberto |
| R23 | Property generator compartilha premissas incorretas com o reducer | 3 | 4 | 100 mil sequências sem mutação adversarial relevante | testes unitários/metamórficos independentes, revisão externa e futura fuzzing de bytes/eventos | QA/domain | monitorado |
| R24 | Schema v1 fica sem estratégia de migração ao surgir schema v2 | 3 | 4 | decoder apenas rejeita versão nova | definir upcaster explícito e fixtures de migração antes da persistência na Fase 5 | persistence | aberto |
| R25 | SHA-256 semântico é interpretado como prova de autenticidade | 2 | 5 | claim de “assinatura segura” sem key/controle de store | documentar digest semântico; autenticidade vem de ACL/transação/ledger na Fase 5 | security/domain | monitorado |
| R26 | Property gate passa sem exercer uma obrigação positiva | 2 | 5 | contadores críticos ficam zero e result segue `passed` | workload mínimo, counters positivos, oráculo bilateral e mutation tests | QA/domain | mitigado |
| R27 | Wire JSON permissivo normaliza payload ambíguo | 2 | 5 | bool/float como versão, chave duplicada ou ISO compacto é aceito | parser de chaves únicas, tipos exatos e escalares canônicos | domain/persistence | mitigado |
| R28 | Matriz estado/evento se autocertifica a partir dos handlers | 2 | 4 | tipo novo recebe `ignore` sem decisão humana | política literal fechada e comparação bidirecional com handlers | domain/QA | mitigado |
| R29 | Schema de leitura muda e falha parcial vira “sem disponibilidade” | 3 | 5 | aumento súbito de `NEGATIVE` após mudança de provider | parsing estrito; erro HTTP/schema/partial sempre `UNCERTAIN`; contract fixtures por versão | adapters/QA | mitigado |
| R30 | Transporte injetado ganha rede/auth dentro do package puro | 2 | 5 | imports de HTTP/env/SDK ou headers nos DTOs | nenhum transporte default; AST/import scan; auth fica na futura fronteira de runtime | adapters/security | mitigado |
| R31 | Fixture sintética diverge do schema provider real | 3 | 4 | contract verde e boundary real falha | source map explícito; replay sanitizado no boundary real obrigatório antes de canary | QA/adapters | aberto |
| R32 | Canonicalização trata array semanticamente ordenado como conjunto | 2 | 4 | ordem futura altera significado, mas digest não | projeção provider-specific antes do hash e teste metamórfico por novo campo/schema | adapters/domain | monitorado |
| R33 | DTO `frozen` retém subestrutura JSON mutável e altera digest após construção | 2 | 5 | `response_hash` muda após mutação do objeto-fonte | detach por JSON, deep-freeze recursivo, teste RED e mutante dedicado | adapters/domain | mitigado |
| R34 | Mutation evidence é preenchida manualmente e pode ficar stale | 2 | 4 | JSON não corresponde ao código/teste atual | catálogo fechado executável em cópias temporárias; CI regenera e exige diff vazio | QA | mitigado |
| R35 | Request DTO permite path traversal ou query injection em transporte futuro | 2 | 5 | path contém dot-segment/controle ou value contém delimitador | alfabetos fechados, path canônico, testes negativos e mutante dedicado | adapters/security | mitigado |
| R36 | Target interno do provider é omitido da identidade da oferta | 2 | 5 | properties/products distintos geram mesmo `offer_id` | provider ref canônico inclui property/product; cross-target property e mutante | adapters/domain | mitigado |
| R37 | Request e response são hashed como conjuntos independentes | 2 | 5 | troca entre endpoints preserva snapshot | pares canônicos request fingerprint/response hash, teste metamórfico e mutante | adapters/domain | mitigado |
| R38 | `lookup_id` coerente localmente, mas não derivado, é aceito | 2 | 5 | evidence/offer compartilham ID arbitrário | recomputação obrigatória em `LookupResult`, property probe e mutante | domain/adapters | mitigado |
| R39 | Property gate constrói DTO abaixo do adapter e não observa IDs internos | 3 | 5 | 50 mil casos passam sem request builder/normalizer | baselines adapter-backed para ambos providers e contadores obrigatórios | QA/adapters | mitigado |
| R40 | Limite temporal inclusivo permite uso exatamente no vencimento | 2 | 4 | seleção em `expires_at` autoriza | intervalo semiaberto, testes Fases 2/3 e mutante inclusivo | domain/QA | mitigado |
| R41 | Classificador semântico escolhe versão, assinatura ou target comercial | 2 | 5 | DTO/prompt contém IDs executáveis | DTO de decisão fechado; trusted binding recompõe target do estado; scan de assinatura pública e mutantes | domain/AI | mitigado |
| R42 | Confirmação é vinculada a texto/locale diferente do resumo persistido | 2 | 5 | hash ou outbox não corresponde ao artefato | rerender determinístico, recomposição dos três IDs, hash/locale no identity material e tamper properties | domain/messaging | mitigado |
| R43 | Pedido de ajuste deixa resumo antigo autorizável | 2 | 5 | aceite posterior ao `ADJUST` cria comando | estado `awaiting_adjustment`, sem handler de confirmação; nova assinatura/versão/resumo obrigatórios | domain/QA | mitigado |
| R44 | Consulta de atividade com janela/sem horário é rejeitada ao retornar ocorrência concreta | 3 | 4 | adapter positivo falha no `OfferedState` | ocorrência dentro da janela inclusiva; `start_time=None` como wildcard; replay/property Bókun desde workflow vazio | domain/adapters | mitigado |
| R45 | Marcador positivo desconhecido ou pergunta é aceito como confirmação | 2 | 5 | “sim, talvez”/“confirmo?” gera `ACCEPT` | conjunto fechado; `?` ambíguo; corpus PT/EN, RED tardio e mutantes independentes | domain/AI/QA | mitigado |
| R46 | Mutante depende da ordem de hash/set e produz evidence intermitente | 2 | 4 | mesmo catálogo alterna killed/survived entre processos | mutante força decisão determinística; testes com múltiplos `PYTHONHASHSEED`; CI regenera JSON | QA | mitigado |
| R47 | Estado queued é persistido sem command/ledger atômicos | 2 | 5 | workflow sem comando ou comando órfão após crash | uma UnitOfWork, transaction rollback, fault injection em cada statement | persistence/domain | mitigado |
| R48 | Lease expirado permite dois workers concluírem o mesmo comando | 2 | 5 | token antigo grava outcome ou segundo dispatch | fencing token monotônico, compare-and-swap e corrida multiprocesso | worker/persistence | mitigado |
| R49 | Crash após socket causa retry comercial | 2 | 5 | dispatch fence sem outcome volta a queued | slot durável antes do dispatch; pós-fence sempre unknown/manual review, sem adapter no reconciler | worker/domain | mitigado |
| R50 | Falha de outbox reabre ledger ou repete provider | 2 | 5 | delivery failure aumenta dispatch/provider calls | tabelas/workers separados; properties e fault injection bilateral | messaging/worker | mitigado |
| R51 | SQLite verde mascara incompatibilidade PostgreSQL | 3 | 4 | DDL futuro falha ou locking diverge | contrato comum/DDL regenerável, claims limitados e prova PostgreSQL obrigatória antes da migração | persistence/QA | aberto |
| R52 | Outcome `effect_confirmed` sem evidência sustenta promessa pública falsa | 2 | 5 | success sem provider reference/evidence | contrato endurecido, projection fechada, serializer hostil e mutante | domain/worker | mitigado |
| R53 | Banco/ledger de teste vaza PII em evidência | 2 | 5 | SQLite/WAL/log entra no Git ou relatório | temp dirs, `.gitignore`, fixtures sintéticas, scan e manifest sem bancos/logs | security/QA | mitigado |
| R54 | Handoff terminal deixa confirmação antiga reaparecer | 2 | 5 | resposta mistura handoff e nova confirmação | precedência terminal no reducer/projection e corpus adversarial | handoff/domain | mitigado |
| R55 | E-mail opcional bloqueia fila ou resposta pública | 3 | 4 | incidente ativo sem acknowledgement porque SMTP está off | efeitos required/optional fechados; fila+ack obrigatórios, e-mail opcional | handoff/messaging | mitigado |
| R56 | Pagamento nasce de reserva incerta ou não executada | 2 | 5 | payment target sem `effect_confirmed` | `ConfirmedReservationAnchor` recomposta de state/command/outcome canônicos | payments/domain | mitigado |
| R57 | Mesmo comprovante/evento paga targets diferentes | 3 | 5 | E2E/event/credit repetido com nova idempotency key | claim global por identidade econômica, independente de target/unidade | payments/security | mitigado |
| R58 | Falha pós-dispatch financeiro retorna a retry | 2 | 5 | settlement/provider count > 1 após timeout | fence permanente, outcome partial/unknown e reconciliação manual | payments/worker | mitigado |
| R59 | Falha de outbox/form/e-mail repete settlement | 2 | 5 | provider delta cresce durante replay de follow-up | ledger financeiro separado e jobs duráveis por efeito | payments/messaging | mitigado |
| R60 | Pix visual é alegado como confirmação bancária | 3 | 4 | mensagem/evidência diz “banco confirmou” sem API | tipo `PixVisualEvidence`, wording fechado e review factual | payments/product | mitigado |
| R61 | Stripe/Wise contornam verificação pelo schema Pix | 2 | 5 | caller escolhe método genérico com booleano | evidence unions fechadas e ports/verifiers específicos | payments/security | mitigado |
| R62 | Troca de método reabre/reexecuta reserva | 2 | 5 | novo `ReservationCommand` após anchor confirmada | workflow financeiro irmão; property counter exige zero reservation commands | payments/domain | mitigado |
| R63 | Réplica não corresponde ao candidato operacional | 3 | 5 | base/diff/untracked divergem | clone `--no-local`, allowlist, manifests e hashes antes/depois | runtime/release | aberto |
| R64 | Importer inventa identidade ausente | 2 | 5 | label/texto vira offer/target ID | proibir inferência, manual review e mutantes de identidade | domain | aberto |
| R65 | Dual-read vira dual-write | 3 | 5 | adapter grava `LeadState` e estado novo | port legado read-only, AST scan e contention de gênese | persistence | aberto |
| R66 | Coordinator vira novo cérebro comercial | 3 | 5 | regra de confirmação/provider no coordinator | `KernelPort` único owner; coordinator só ordena/persiste | runtime/domain | aberto |
| R67 | ToolDispatch preserva provider write no turno | 3 | 5 | executor/provider chamado pela Maya | commands Fases 5/6 ou block; zero provider no turno | runtime/execution | aberto |
| R68 | Comparator se autocertifica | 2 | 5 | oracle importa reducer ou deriva totais do summary | política literal, rows completas, mutantes e totais reconstruídos | QA | aberto |
| R69 | Package testado por checkout diverge do wheel | 2 | 4 | import resolve source local | wheel stdlib duplo, install target e checkout fora do `sys.path` | release | aberto |
| R70 | Validação pesada repete sem nova evidência | 4 | 3 | full/properties rerodam no mesmo tree | uma janela por candidato e matriz de blast radius | QA/release | aberto |
| R71 | Tool ativa não possui command durável publicado | 4 | 5 | Wise/link Stripe alcança executor ad hoc | `BLOCKED_UNMIGRATED`, manual review e rollout `NO-GO` | payments/runtime | aberto |
| R72 | Entry da Fase 8 usa status stale ou predecessor não publicado | 2 | 5 | índice ativo diverge de closeout/review/CI | pins exatos de commit/tree/run, RED de índice único e validators 0–7 | release/QA | mitigado |
| R73 | Canary herda env, perfil, estado ou volume live | 3 | 5 | segredo/gate/mount live aparece na configuração efetiva | allowlist fechada, clone mínimo efêmero e auditoria de mounts/estado | release/security | aberto |
| R74 | Gate humano ou canary E2E é tratado como autorização implícita | 3 | 5 | provider/workflow/contato/janela têm default ou write abre antes da aprovação | Carlos obrigatório no gate conversacional; autorização E2E posterior, explícita e sem defaults | product/release | aberto |
| R75 | Artefato OCI de rollback perde identidade ou disponibilidade | 2 | 5 | referência resolve outro manifest digest ou artefato não autentica | reter referência por manifest digest OCI e registrar image ID/layers/archive SHA como evidência; rollback sem rebuild | release/operations | aberto |

## Processo

- Todo novo risco recebe ID, owner, sinal observável e mitigação.
- Risco não é fechado por opinião; exige evidência.
- Risco crítico aberto bloqueia GO se atingir autorização, duplicidade, evidência ou identidade do artefato.
- A revisão de riscos é obrigatória no encerramento de cada fase.
