# Fase 8 — Delta mínimo de binding do ToolDispatch

**Status:** CANDIDATE DOCUMENTAL; não executável até review AND 3/3 e aceite humano explícito da identidade deste arquivo.

**Autoridades preservadas:**

- arquitetura: `2889e9ec08f466bbb16a30e4bb5c9a098daf54d3`;
- facts/reads: `6f638234a200a72178dac66705d739a4b597048f`;
- remaining-wire registry: `f9c2e3478f07a06e2754f4fd42a5b21bed2b0fc7`.

## 1. Problema autenticado

A autoridade remaining-wire exige que `NormalizedToolProposal.request_id`,
`sequence`, `request_hash` e `frame_commitment_hash` sejam iguais ao frame
`COMMAND` aceito. O contrato `TranscriptCommitment` já usa sequência positiva,
começando em `1`.

Duas linhas aprovadas impedem essa igualdade:

1. o KAT de `NormalizedToolProposal` fixa `sequence = 0` e seu field registry usa
   `ORDINAL`;
2. a API descritiva da Task 2 recebe somente `tool_name`,
   `typed_arguments_json` e `transcript_binding: str`, portanto não recebe
   `aggregate_turn_id`, `request_id`, `sequence` ou `request_hash` e não pode
   construir o proposal sem estado lateral ou encoding implícito.

Nenhuma implementação pode simultaneamente satisfazer esses contratos. Este delta
corrige somente essa contradição. Não cria capability, effect, owner, tabela, FSM,
gate, provider path ou delivery path.

## 2. Decisão

UDS e transcript permanecem **1-based**. Frames aceitos usam `POSITIVE`; ordinais
0-based continuam reservados a chunks, allocation rows e outras listas que os
contratos declaram como `ORDINAL`.

Consequentemente:

- `NormalizedToolProposal.sequence` muda de `ORDINAL` para `POSITIVE`;
- `LearningProposal.sequence` muda de `ORDINAL` para `POSITIVE`;
- nenhum field é adicionado ou removido;
- schemas, versions e domains permanecem inalterados;
- o KAT de `LearningProposal` permanece byte-idêntico porque já usa `sequence = 1`;
- somente o KAT/hash de `NormalizedToolProposal` muda, de `sequence = 0` para
  `sequence = 1`.

## 3. API literal do normalizador

```python
class ToolDispatch:
    def normalize_proposal(
        self,
        *,
        aggregate_turn_id: str,
        tool_name: str,
        typed_arguments_json: bytes,
        transcript_commitment: TranscriptCommitment,
    ) -> NormalizedToolProposal: ...
```

Invariantes fechadas:

1. `aggregate_turn_id` satisfaz `ID_TOKEN`.
2. `transcript_commitment` é o tipo exato `TranscriptCommitment`.
3. `transcript_commitment.direction is TranscriptDirection.CHILD_TO_PARENT`.
4. `transcript_commitment.kind is TranscriptKind.COMMAND`.
5. `tool_name` deve ser um dos quatro nomes command normalizados:
   `cloudbeds_criar_reserva_v2`, `bokun_agendar_passeio_v2`,
   `cloudbeds_lancar_pagamento_confirmar_reserva` ou
   `bokun_lancar_pagamento_confirmar_reserva`.
6. aliases, reads, state-commit e os três commands `BLOCKED_UNMIGRATED` são
   rejeitados; não são canonicalizados.
7. `arguments_type` é inferido unicamente do nome literal.
8. `typed_arguments_json` decodifica e reencoda byte-identicamente pelo owner
   externo correspondente, sem unknown fields.
9. O proposal recebe diretamente:
   - `request_id = transcript_commitment.request_id`;
   - `sequence = transcript_commitment.sequence`;
   - `request_hash = transcript_commitment.request_hash`;
   - `frame_commitment_hash = transcript_commitment.canonical_hash()`.
10. O método não cria command, não consulta estado e não transporta capability.

Não existe encoding alternativo em `transcript_binding: str`; essa assinatura é
substituída pela assinatura literal acima.

## 4. API literal de verificação pós-kernel

`AuthorizedDispatch` é efêmero, capability-free e não pertence ao wire:

```python
@dataclass(frozen=True, slots=True)
class AuthorizedDispatch:
    proposal: NormalizedToolProposal
    command: BoundaryCommand
```

```python
class ToolDispatch:
    def verify_authorized(
        self,
        *,
        proposal: NormalizedToolProposal,
        state: BoundaryState,
        decision: KernelDecision,
    ) -> AuthorizedDispatch: ...
```

Invariantes fechadas:

1. os três argumentos usam tipos exatos; subclasses e DTOs handmade são
   rejeitados;
2. o proposal é revalidado pelo catálogo literal e pelo codec owner;
3. `state` é o estado autoritativo usado para verificar offer/version/signature ou
   anchor/evidence/amount/currency/receiver/status;
4. a verificação reconstrói ou seleciona o command canônico já autorizado pelo
   estado sem chamar provider;
5. o command esperado aparece exatamente uma vez em `decision.commands`;
6. o retorno contém o mesmo proposal exato e o command exato de
   `decision.commands`;
7. command sem proposal, proposal sem command, proposal duplicado ou command
   divergente são rejeitados pela bijeção de conjunto no coordinator; este método
   prova uma aresta proposal→command e nunca autoriza a cardinalidade global;
8. reads, state-commit, aliases e `BLOCKED_UNMIGRATED` não entram nesta API;
9. nenhum provider, ManyChat, network, send ou executor é importado ou invocado.

## 5. KAT substituto

O `canonical_utf8` de `NormalizedToolProposal` permanece byte-idêntico ao KAT de
`f9c2e347…`, exceto por:

```json
"sequence":1
```

Com domain `phase8-normalized-tool-proposal-v1`, o novo canonical hash é:

```text
d38e50cee26acaedd150edc9b4aa6d332dbf44cc0e7adf445c83d8cd864ccac7
```

O fixture candidato regenerado tem identidade:

```text
path   = tests/fixtures/phase8_remaining_wire_registry_v1.json
sha256 = e19dc46583023564ef6092c235bc8aba04d828a043154aabeaae8f7d334672b6
bytes  = 124288
lines  = 1
```

A comparação estrutural com o fixture de `f9c2e347…` prova exatamente cinco leaf
deltas:

1. `NormalizedToolProposal.fields[sequence].type`: `ORDINAL → POSITIVE`;
2. `NormalizedToolProposal.known_answer.canonical_utf8`: `sequence 0 → 1`;
3. `NormalizedToolProposal.known_answer.canonical_hash`: antigo →
   `d38e50cee26acaedd150edc9b4aa6d332dbf44cc0e7adf445c83d8cd864ccac7`;
4. `LearningProposal.fields[sequence].type`: `ORDINAL → POSITIVE`;
5. a entrada correspondente no `known_answer_catalog` recebe o mesmo novo hash.

A regeneração determinística prova:

- os mesmos 39 contratos, 60 enums e 11 external refs;
- nenhum schema, version, domain, field list adicional, enum ou artifact preimage
  mudou;
- `LearningProposal` manteve canonical hash
  `11926681ffaf17906cf6a7214e056e15505a996a289b9b11df71dee9119c4fb6`.

Este par spec/fixture candidato somente pode substituir o recorte correspondente de
`f9c2e347…` após review AND 3/3 e aceite humano explícito da identidade única que os
versionar.

## 6. RED/GREEN permitido após aceite

O RED da Task 2 deve provar, separadamente:

1. `sequence=0` rejeitado por ambos os proposal types;
2. normalização a partir de um `TranscriptCommitment` `COMMAND` aceito produz o
   proposal exato e nenhum command;
3. frame de outra direção/kind, alias ou command bloqueado é rejeitado;
4. `verify_authorized` rejeita handmade DTO, stale reservation binding, stale
   payment evidence e ausência/duplicação/divergência em `decision.commands`;
5. AST/import scan encontra zero provider, ManyChat, network, send ou executor em
   `dispatch.py`.

GREEN e regressão permanecem focados em dispatch/kernel. A suíte pesada, wheel,
build, Docker, canary, conversa, E2E e rollout continuam proibidos por seus gates
independentes.
