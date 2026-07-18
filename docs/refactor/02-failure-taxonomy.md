# Taxonomia de falhas conhecidas

Esta taxonomia separa sintoma local de causa sistêmica. A Fase 1 transformará cada classe em cenário reproduzível.

| ID | Sintoma observado | Causa sistêmica | Prevenção arquitetural |
|---|---|---|---|
| F01 | Plugin perdeu `reservation_confirmation_phase` | Estado de autorização projetado por fronteiras diferentes | Schema tipado único e kernel compartilhado |
| F02 | Cliente precisou confirmar duas vezes | Estado conversacional e estado mecânico evoluíam em paralelo | Uma FSM e evento `SummaryPresented` |
| F03 | Alias legado/write sem estado podia contornar v2 | Compatibilidade possuía mais capacidade que o caminho canônico | Capability allowlist e remoção de aliases de write |
| F04 | Assinatura de pacote omitiu pagamento/upsell | Autorização cobria representação parcial do efeito | Assinatura do objeto econômico completo |
| F05 | Lookup antigo podia ser reutilizado | Consulta modelada por booleano/cópia sem provenance forte | `LookupEvidence` tipado, versionado e expirável |
| F06 | Commit concorreu com tools | Commit, preflight e efeito não eram uma transação por turno | Coordenador/lock único por lead |
| F07 | Metadados aninhados vazaram pela fronteira | DTO livre e filtro apenas no topo | DTO comercial allowlisted e sanitização recursiva |
| F08 | Fase armada antes/depois do resumo incorretamente | Apresentação e autorização não eram um evento atômico | Persistir `SummaryPresented` antes da entrega |
| F09 | Modelo escolheu Cloudbeds/Bókun errado | Operação técnica era decisão livre da LLM | Derivar adapter do tipo de componente selecionado |
| F10 | Bootstrap falhou por ownership de log | Protocolo dependia de efeito colateral do filesystem | Session ID e readiness explícitos |
| F11 | Canary divergiu em env, mount e Python | Validação não atestava o artefato promovido | Mesmo digest OCI e manifesto de runtime |
| F12 | `dry_run` alterou leitura | Leitura, escrita e entrega comprimidas em uma flag | Flags ortogonais: reads, writes e delivery |
| F13 | Opção sintética passou no fake e falhou no provider | Fixture validava shape, não aceitabilidade real | Replay de snapshots read-only sanitizados |
| F14 | Maya prometeu ação futura sem continuação | Texto futuro não estava ligado a workflow durável | `ContinuationCommand`/outbox antes da promessa |
| F15 | `n°` versus `nº` impediu seleção | Label pública participou da identidade técnica | `offer_id` opaco; nome somente apresentação |
| F16 | Timeout 120s e mínimo de write 302s | Configurações relacionadas sem invariante cruzado | Startup fail-fast e write fora do turno da LLM |
| F17 | Evidência composta reportou provider incorretamente | Booleans achatavam sequência e incerteza | `ExecutionOutcome` monotônico tipado |
| F18 | Handoff não respondeu quando e-mail estava desativado | Notificação interna opcional virou requisito público | Workflow com efeitos obrigatórios/opcionais |
| F19 | Disco/readiness oscilou | Estado, logs, outbox e build disputavam volume local | Quotas, alertas, backend durável e histerese |
| F20 | Segunda tool no mesmo turno bateu budget | Correção do modelo ocorria dentro do loop técnico | Um intent por turno e comandos derivados pelo reducer |
| F21 | Working tree, imagem e commit não eram vinculados | Deploy construía checkout mutável | Commit limpo, digest e manifesto assinável |
| F22 | Teste verde com estado pré-carregado | Teste construía a condição que deveria provar | Replay desde payload bruto e estado vazio |

## Cinco causas dominantes

### 1. Múltiplos donos da mesma decisão

Runner, plugin, executor e guard tomam decisões semelhantes de confirmação, budget e tool order.

**Remoção:** `ReservationKernel` é o único owner; demais camadas delegam.

### 2. Dicionários e strings usados como tipos

`metadata`, aliases e status textuais permitem combinações contraditórias.

**Remoção:** DTOs discriminados, enums e reducer total.

### 3. Tempo e continuidade fora do domínio

Timeout, crash e retry são tratados como detalhes do request.

**Remoção:** comando durável, outcome incerto e reconciliação explícita.

### 4. Testes na fronteira errada

Mocks refletem a implementação e casos começam com seleção/fase prontas.

**Remoção:** contratos reais, replay bruto, fault injection e mesmos backends/schema.

### 5. Drift de artefato e flags sobrecarregadas

Canary e produção podem usar ambientes semanticamente diferentes.

**Remoção:** digest único e configuração ortogonal atestada.

## Regra de encerramento

Um incidente só é considerado prevenido quando:

1. existe teste RED que o reproduz;
2. a prevenção está no owner arquitetural correto;
3. o teste passa no boundary real aplicável;
4. fault injection correspondente passa;
5. a evidência está vinculada ao commit/digest candidato.
