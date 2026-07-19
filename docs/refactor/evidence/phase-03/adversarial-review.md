# Revisão adversarial — Fase 3

## Escopo

- boundary GET Cloudbeds/Bókun;
- sanitização e classificação `POSITIVE/NEGATIVE/UNCERTAIN`;
- identidade opaca e revalidação;
- TTL, zero/múltiplos matches;
- fixtures, property gate e mutation testing;
- ausência de rede/auth/writes/legado no package.

## Revisão interna

### A01 — ordem de arrays alterava snapshot sem mudança semântica

Severidade inicial: **média**.

A canonicalização inicial ordenava chaves, mas preservava a ordem de arrays de
opções. O digest agora canonicaliza recursivamente arrays sanitizados antes do
hash. Contract tests Cloudbeds/Bókun invertem a ordem e exigem o mesmo
`snapshot_hash` e a mesma ordem final de offers.

Estado: **corrigido e coberto**.

### A02 — entrypoint property não encontrava o package

Severidade inicial: **média**.

O runner funcionava importado, mas falhou quando executado diretamente em RED
com `ModuleNotFoundError`. O script passou a inserir somente a raiz do próprio
repositório em `sys.path`, igual ao padrão dos validadores existentes.

Estado: **corrigido e coberto**.

### A03 — mutante de provider-ref era inalcançável

Severidade inicial: **teste inválido**, não bug do produto.

A primeira mutação adicionava fallback depois do gate de formato `offer:<sha>`,
e por isso sobreviveu sem realmente habilitar provider ref. O mutante foi
corrigido para remover o gate e criar o fallback perigoso; então o teste o
matou. Apenas o mutante válido integra `mutation-result.json`.

Estado: **evidência corrigida**.

### A04 — resposta parcial poderia parecer indisponibilidade

Severidade: **alta**.

HTTP não-2xx, transport exception, metadata divergente, rate plan ausente,
estadia parcial, campo executável ausente, preço não finito e moeda divergente
produzem `UNCERTAIN` com zero offers. Somente response estruturalmente válida e
sem opção bookable produz `NEGATIVE`.

Estado: **coberto**.

### A05 — label/provider ref poderiam voltar a selecionar

Severidade: **alta**.

A seleção valida primeiro formato opaco e depois igualdade exata, sem fuzzy,
índice, label ou provider-ref fallback. Mutantes de provider ref, TTL e
primeiro match duplicado são mortos pelos testes.

Estado: **coberto**.

### A06 — `ReadResponse(frozen=True)` ainda retinha body mutável

Severidade inicial: **alta**.

O DTO impedia reatribuição de `body`, mas mantinha a referência original a
`dict/list`; uma mutação posterior alterava o `response_hash`. O teste RED
reproduziu a mudança do digest. O boundary agora serializa/destaca e deep-freeze
o JSON como mappings imutáveis e tuples. Um décimo primeiro mutante removeu o
deep-freeze e foi morto.

Estado: **corrigido e coberto**.

### A07 — provider, serviço e namespace do provider ref não estavam vinculados

Severidade inicial: **alta**.

Era possível construir manualmente um resultado com provenance Cloudbeds e um
`provider_ref` Bókun, desde que o hash fosse recalculado para a combinação
forjada. O teste RED reproduziu o aceite. `LookupResult` agora vincula
Cloudbeds a lodging e `cloudbeds.*`, e Bókun a activity e `bokun.*`. O property
runner também passou a gerar apenas combinações provider/serviço realizáveis.
Um décimo segundo mutante removeu o binding de namespace e foi morto.

Estado: **corrigido e coberto**.

### A08 — mutation evidence não era regenerada pelo CI

Severidade inicial: **média**.

A primeira evidência listava os mutantes executados manualmente em cópias
temporárias, mas o workflow só validava o JSON. O catálogo fechado agora vive em
`scripts/run_phase3_mutations.py`; target stale, timeout ou sobrevivente falham.
O workflow regenera os 19 resultados e exige diff vazio, e o validador compara o
JSON ao catálogo exato.

Estado: **corrigido e coberto**.

### A09 — `ReadRequest` aceitava dot-segments e delimitadores de query

Severidade inicial: **alta** para um transporte futuro.

Os adapters atuais constroem paths constantes, mas o DTO público aceitava
`/../`, backslash, controle e valores contendo `&`, `=` ou `#`. O teste RED
reproduziu o aceite. Paths agora pertencem a um alfabeto fechado, não têm
segmentos vazios/`.`/`..`, e query keys/values também usam alfabetos fechados.
O décimo terceiro mutante remove o dot-segment guard e é morto.

Estado: **corrigido e coberto**.

## Revisão independente tardia

Uma das três frentes read-only entregou parecer após o closeout
`e19d0e571ec4f19f6f3979a88b9ddb559a4994f5`; as outras duas expiraram sem
summary e não contam como evidência. O parecer útil reabriu formalmente a fase.

### A10 — `property_id` Cloudbeds não participava do target executável

Severidade: **bloqueador/alta**.

Dois lookups com responses iguais e properties diferentes produziam o mesmo
`lookup_id`, `provider_ref` e `offer_id`; a revalidação cross-property era
aceita. Cinco reproduções tardias foram registradas em
`red-result-late-review.json`. O `provider_ref` agora inclui
`cloudbeds.property.<property>.room.<room>.rate.<rate>`, e o request fingerprint
também entra no lookup ID. Teste cross-property e mutante dedicado cobrem o
caso.

Estado: **corrigido e coberto**.

### A11 — `lookup_id` podia ser rebindado manualmente

Severidade: **alta**.

`LookupResult` exigia apenas igualdade entre `offer.lookup_id` e
`evidence.lookup_id`. Um ID arbitrário coerente localmente era aceito. O
contrato agora recomputa o ID a partir de provider, query, `observed_at` e
provenance pareada, rejeitando qualquer rebinding. Unit test, property counter e
mutante cobrem a obrigação.

Estado: **corrigido e coberto**.

### A12 — responses não estavam vinculadas aos respectivos endpoints

Severidade: **média**.

Ordenar somente os response hashes fazia uma troca availability↔metadata manter
snapshot/lookup. A projeção agora ordena pares
`(request_fingerprint, response_hash)`: a ordem dos exchanges não importa, mas
trocar a response de endpoint altera ambos os IDs. Há teste metamórfico, 50 mil
probes e mutante específico.

Estado: **corrigido e coberto**.

### A13 — contrato público aceitava total zero em lookup positivo

Severidade: **média**.

Os adapters rejeitavam preço não positivo, mas um `LookupResult` manual podia
reintroduzir `Money(0)`. Positive agora exige `offer.total.amount > 0`; teste,
50 mil probes e mutante preservam a regra no boundary público.

Estado: **corrigido e coberto**.

### A14 — limite de expiração era inclusivo

Severidade: **baixa**, com escolha fail-closed.

Foi fixada a semântica semiaberta `[observed_at, expires_at)`. Exatamente em
`expires_at`, seleção e reducer tratam a evidência como vencida. Testes das Fases
2/3, property gate e mutante inclusivo cobrem o limite.

Estado: **corrigido, documentado e coberto**.

### A15 — property gate fabricava resultados abaixo dos adapters

Severidade: **média/teste**.

O oracle anterior montava DTOs diretamente e não podia observar IDs internos ou
request/response binding. O gate de 50 mil casos agora produz os baselines pelos
adapters in-memory: 18.750 Cloudbeds e 31.250 Bókun. Cada caso também prova
cross-target, rebinding, response swap e total zero. O CLI exige esses contadores
e o catálogo contém mutante que reutiliza o mesmo target.

Estado: **falso verde removido**.

O apontamento adicional sobre número de linha fixo no validador descrevia um
worktree concorrente: o SHA de closeout já comparava arquivo/símbolo, sem fixar
linha, e `validate_phase3.py` estava verde. A remediação mantém essa forma.

## Riscos residuais

1. fixtures são sintéticas e podem divergir de uma versão futura dos schemas;
2. transporte/auth real ainda não foi exercitado;
3. categories Bókun além de adults/children exigem extensão explícita de schema;
4. SHA-256 é identidade semântica, não autenticação;
5. canonicalização trata arrays sanitizados como conjuntos ordenados por valor;
   um schema futuro com arrays semanticamente ordenados exigirá projeção
   provider-specific antes do hash.

Nenhum risco residual autoriza rollout. Estado comercial permanece **NO-GO**.
