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
O workflow regenera os 12 resultados e exige diff vazio, e o validador compara o
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

## Revisão independente

Três frentes read-only foram disparadas durante a implementação. Nenhum summary
utilizável retornou à sessão até o closeout; portanto, elas não são citadas como
evidência positiva nem como ausência de achados. A revisão direta completa,
testes RED, property gate, catálogo de mutantes, validador e CI sustentam os
claims deste documento.

## Riscos residuais

1. fixtures são sintéticas e podem divergir de uma versão futura dos schemas;
2. transporte/auth real ainda não foi exercitado;
3. categories Bókun além de adults/children exigem extensão explícita de schema;
4. SHA-256 é identidade semântica, não autenticação;
5. canonicalização trata arrays sanitizados como conjuntos ordenados por valor;
   um schema futuro com arrays semanticamente ordenados exigirá projeção
   provider-specific antes do hash.

Nenhum risco residual autoriza rollout. Estado comercial permanece **NO-GO**.
