# Registro de riscos

Escala: probabilidade e impacto de 1 (baixo) a 5 (crítico).

| ID | Risco | P | I | Sinal | Mitigação | Owner | Estado |
|---|---|---:|---:|---|---|---|---|
| R01 | Patches continuam sendo aplicados no legado durante refatoração | 4 | 5 | novos guards/status | freeze de mudanças não críticas; RCA antes de exceção | tech lead | aberto |
| R02 | Migração big bang quebra estados ativos | 3 | 5 | conversões sem fallback | dual-read/single-write e shadow | domain | aberto |
| R03 | Kernel novo replica bugs do legado | 3 | 5 | testes portados literalmente | testes por invariantes e incidentes, não implementação | QA/domain | aberto |
| R04 | Provider chamado duas vezes após crash | 3 | 5 | attempt > 1 | comando durável + ledger + outcome incerto | execution | aberto |
| R05 | Label volta a controlar identidade | 3 | 4 | matching por nome | `offer_id` opaco, seleção exata e tests metamórficos/mutantes | lookup | mitigado |
| R06 | Canary não corresponde ao deploy | 4 | 5 | rebuild/diff de hash | promover mesmo digest e manifesto | release | aberto |
| R07 | Segredo/PII entra no novo repo | 2 | 5 | scanners/grep | `.gitignore`, validator e revisão | security | aberto |
| R08 | Outbox perde mensagem após efeito comercial | 3 | 5 | backlog/stuck lease | store durável, lease/recovery, fault injection | messaging | aberto |
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

## Processo

- Todo novo risco recebe ID, owner, sinal observável e mitigação.
- Risco não é fechado por opinião; exige evidência.
- Risco crítico aberto bloqueia GO se atingir autorização, duplicidade, evidência ou identidade do artefato.
- A revisão de riscos é obrigatória no encerramento de cada fase.
