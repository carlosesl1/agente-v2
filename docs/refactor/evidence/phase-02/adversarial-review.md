# Revisão adversarial — Fase 2

Data UTC da revisão principal: `2026-07-18`.

## Escopo

- tipos, assinatura, reducer e serializer;
- testes unitários, metamórficos e property runner;
- contrato, matriz, CI e isolamento arquitetural;
- nenhuma execução live, provider, rede, banco, Docker ou legado.

## Achados da revisão principal

### A01 — combinação persistida inconsistente podia atravessar o decoder

Severidade inicial: **alta**.

Um envelope podia conter objetos individualmente bem formados, mas draft,
resumo, confirmação, comando, meta e outcome divergentes.

Tratamento:

- recomposição da assinatura do draft e payload;
- identidade e idempotency key determinísticas revalidadas;
- bindings draft → resumo → confirmação → comando;
- binding command → outcome/certainty;
- binding meta → workflow/command;
- testes de JSON adulterado.

Estado: **corrigido e coberto**.

### A02 — `customer_facts` ausente do draft/payload/assinatura

Severidade inicial: **alta**.

Isso permitiria que adapters futuros introduzissem campos executáveis fora da
identidade confirmada.

Tratamento:

- `CustomerFacts` fechado;
- customer ref, nome, e-mail, telefone E.164 e país no draft e comando;
- todos os campos cobertos pela assinatura;
- mutações metamórficas obrigatoriamente alteram a assinatura.

Estado: **corrigido e coberto**.

### A03 — `called_no_effect` colapsado com `not_called`

Severidade inicial: **média**.

Tratamento: estado discriminado `failed_no_effect`, distinto de
`failed_before_provider`, com certainty exata no serializer/reducer.

Estado: **corrigido e coberto**.

### A04 — colisão de event ID parecia duplicata legítima

Severidade inicial: **média**.

Tratamento: `StateMeta` mantém fingerprints canônicos paralelos aos event IDs;
payload divergente com ID reutilizado é rejeitado sem mutação ou comando.

Estado: **corrigido e coberto no unit/property runner**.

### A05 — draft direto podia conter oferta indisponível/moeda divergente

Severidade inicial: **alta** no boundary de persistência.

Tratamento: `CommercialDraft` e `CommandPayload` rejeitam componentes
indisponíveis, múltiplas moedas e adicionais em moeda divergente.

Estado: **corrigido e coberto**.

### A06 — gate não exigia o comando obrigatório

Severidade inicial: **alta**.

Um reducer que suprimisse todos os comandos podia manter `violations=()`.
O oráculo agora identifica todo aceite válido e exige exatamente um comando,
`ExecutionQueuedState`, status/reason corretos e payload idêntico ao draft.

Estado: **corrigido, reproduzido em RED e coberto por mutante**.

### A07 — atraso não era obrigação do property gate

Severidade inicial: **alta**.

Eventos atrasados eram gerados, mas o relatório não exigia rejeição nem
preservação do estado comercial. O gate agora conta probes e exige status,
reason, zero comandos e igualdade comercial fora de `meta`.

Estado: **corrigido, reproduzido em RED e coberto por mutante**.

### A08 — workload trivial se apresentava como gate

Severidade inicial: **alta**.

`1 × 1` retornava sucesso. O modo padrão agora exige no mínimo `100000 × 20`;
workload menor só é aceito com `--smoke`, explicitamente sem valor de gate.

Estado: **corrigido e coberto por CLI test/mutante**.

### A09 — decoder aceitava equivalência numérica e duplicatas JSON

Severidade inicial: **média**.

`true` e `1.0` podiam equivaler a schema `1`, e o parser mantinha a última chave
duplicada. O decoder exige inteiro JSON exato e rejeita duplicatas em qualquer
profundidade.

Estado: **corrigido e coberto**.

### A10 — subclasses escapavam do universo fechado

Severidade inicial: **média**.

Os encoders usavam `isinstance`; agora exigem os tipos exatos registrados para
estado/evento e exatamente `ReservationCommand`.

Estado: **corrigido e coberto**.

### A11 — ISO permissivo e cobertura negativa insuficiente

Severidade inicial: **média/baixa**.

Datas/timestamps compactos eram normalizados silenciosamente. O decoder exige
formas canônicas e a suíte cobre malformed JSON, shape, campo ausente, enum,
boolean/int, duplicatas, subclasses e escalares não canônicos.

Estado: **corrigido e coberto**.

### A12 — matriz estado/evento era autocertificada

Severidade inicial: **média**.

A tabela era derivada dos próprios handlers. Há agora uma política literal
fechada, validada contra os handlers; estado/evento novo sem decisão explícita
faz o gate falhar.

Estado: **corrigido e coberto por mutante**.

### A13 — diversidade semântica não era mensurada

Severidade inicial: **média**.

O relatório agora mede lookup positivo, negativo, expirado, indisponível e
multi-oferta. O gate exige cobertura positiva de cada classe, além de aceite
válido e evento atrasado.

Estado: **corrigido e coberto**.

## Riscos residuais aceitos

- event IDs/fingerprints crescem linearmente; retenção/checkpoint pertence à Fase 5;
- SHA-256 é identidade semântica, não MAC/assinatura de autenticidade;
- schema v2 exigirá upcaster explícito antes da persistência live;
- o generator property-based pode compartilhar premissas com o reducer;
- frescor durante fila/execução exigirá política explícita no command store/worker;
- adapters, renderer, persistência e provider continuam não provados nesta fase.

## Mutation testing

Onze mutantes independentes foram aplicados somente em cópias temporárias. Eles
cobrem as três invariantes comerciais originais e os oito falsos verdes da
revisão independente: comando obrigatório, atraso, workload mínimo, tipo de
schema, duplicatas JSON, subclasses, ISO canônico e política explícita.

Todos foram mortos pelos testes-alvo. Resultado estruturado em
`mutation-result.json`.

## Revisão independente

O eixo serializer/property gate concluiu com achados acionáveis A06–A13. O eixo
de invariantes/reducer foi bloqueado pelo safety filter do provider antes de
emitir parecer, e o eixo arquitetura/isolamento expirou sem summary; nenhum dos
dois foi usado como evidência positiva. Os achados recebidos foram reproduzidos
localmente em RED antes das correções.
