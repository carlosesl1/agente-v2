# Maya V2 — reservas Bókun com múltiplos passageiros

## Status

Desenho aprovado por Carlos Eduardo em 2026-07-30. Esta especificação substitui somente a limitação temporária de escrita Bókun a uma pessoa. Ela não abre provider writes, entrega pública, pagamentos ou rollout.

## Problema

A V2 consulta disponibilidade para qualquer total positivo de participantes, mas perde a composição adulto/criança e usa a primeira categoria de preço para todo o grupo. Na escrita, há três bloqueios adicionais:

1. o reducer transforma qualquer passeio com mais de uma pessoa em handoff operacional;
2. o reparo semântico de seleção exige `adults + children == 1`;
3. o transport Bókun cria, valida e responde exatamente um `pricingCategoryBooking` e um passageiro.

A implementação V1 de referência já preserva a capacidade correta: cria uma entrada de categoria por participante, exige uma lista de passageiros do mesmo tamanho, liga cada passageiro ao booking retornado pelo carrinho e monta o submit por pessoa. O V1 é somente fonte de comportamento; nenhum código, estado ou runtime legado será importado.

## Objetivo

Permitir que a Maya V2 prepare e execute, pelos gates já existentes, uma reserva Bókun para qualquer `Party(adults >= 1, children >= 0)` que o produto e a disponibilidade do provider suportem, incluindo uma pessoa, grupos de adultos e grupos mistos.

A reserva só pode avançar quando preço, categorias, manifesto de passageiros, autorização, carrinho, checkout e read-back concordarem exatamente.

## Não objetivos

- Abrir qualquer gate real ou criar uma reserva durante a implementação.
- Reaproveitar o agente, planner, `LeadState`, banco ou executor do V1.
- Duplicar os dados do titular para acompanhantes.
- Inventar categoria de preço quando o Bókun não publicar uma categoria compatível.
- Remover handoff por desconto, pedido humano, restrição de segurança ou resultado ambíguo.
- Expor nomes de providers, IDs privados, dados pessoais ou estruturas internas em mensagens públicas.

## Invariantes

1. A LLM propõe estrutura; somente o pai valida, persiste, assina e executa.
2. O manifesto completo participa da assinatura do assunto comercial. Qualquer alteração invalida o resumo pendente e exige nova leitura, novo resumo e nova confirmação.
3. O número e os tipos dos passageiros devem coincidir com `OfferSnapshot.party` em todas as fronteiras.
4. A consulta de atividade deve preservar adultos e crianças separadamente; `participants` é sempre a soma verificável, nunca a única identidade da party.
5. Uma pessoa continua compatível com o fluxo atual: o titular completo pode formar o único passageiro.
6. Para grupos, cada passageiro é individual e possui seus próprios dados. Repetir o titular não é fallback.
7. Falta de dados, categoria ou binding é `NOT_CALLED`; ambiguidade depois de uma chamada segue a álgebra de certeza existente e exige reconciliação.
8. Idempotência, fencing, deadline, autorização de write, ledger e read-back permanecem obrigatórios e independentes.
9. Respostas públicas mostram apenas composição da party e resultado normalizado; não ecoam nascimento, gênero cadastral ou dados pessoais dos passageiros.

## Modelo de passageiros

### Domínio assinado

Adicionar `PassengerFacts` imutável ao domínio com os campos:

- `position: int`, contígua e iniciada em 1;
- `participant_type: "adult" | "child"`;
- `full_name: str` normalizado;
- `birth_date: date`;
- `gender: "m" | "f"`;
- `country_code: str` ISO alpha-2.

`CustomerFacts` ganha `passengers: tuple[PassengerFacts, ...] = ()` como campo opcional e retrocompatível. A ordem canônica é `position`. Posições repetidas, lacunas, tipos inválidos ou nomes vazios falham na construção.

Para uma atividade com uma pessoa, `effective_passengers` pode derivar o passageiro 1 dos dados já autenticados do titular quando `CustomerFacts.passengers` estiver vazio. Para party maior que um, ou party com criança, um manifesto explícito completo é obrigatório.

`canonical_subject` inclui `passengers` somente quando houver manifesto explícito. Assim, documentos históricos sem esse campo mantêm seus hashes; novos grupos ficam criptograficamente vinculados a todas as pessoas.

Antes de criar `DraftRequested`, a aplicação valida:

- quantidade total igual a `adults + children`;
- exatamente `adults` entradas `adult` e `children` entradas `child`;
- posições `1..N` sem repetição;
- todos os campos obrigatórios presentes.

### Coleta incremental parent-owned

O protocolo do child avança para `v2-model-proposal-v6` e adiciona a chave obrigatória `passengers`, sempre uma lista. Cada item possui as chaves exatas:

- `position`;
- `participant_type`;
- `full_name`;
- `birth_date`;
- `gender`;
- `country_code`.

Campos ainda não informados podem ser `null`; `position` e `participant_type` são sempre obrigatórios. O child nunca autoriza efeito com essa lista.

O pai converte as entradas em um manifesto parcial canônico, mescla somente por posição e persiste o resultado na projeção. Conflito entre valor anterior e novo é mudança material: revoga proposta pendente e exige correção explícita. Uma mudança na party limpa entradas que não possam mais ser ligadas de forma inequívoca ao novo conjunto.

Valores pessoais já coletados não retornam em `state_facts` para o child. `ModelRequest` leva apenas um marcador público de progresso com:

- adultos/crianças requeridos;
- posições completas;
- campos faltantes por posição.

A mensagem atual continua sendo processada normalmente; o pai não reinjeta dados pessoais de turnos anteriores. O manifesto persistido nunca aparece em reply, logs públicos ou observations.

O prompt orienta coleta natural após a escolha. Para mais de uma pessoa, a Maya pede os dados juntos em formato humano por passageiro e pode completar posições faltantes em turnos posteriores sem repetir valores já recebidos.

## Consulta e binding privado

### ReadRequest

Para `ReadKind.ACTIVITY`, a forma canônica nova inclui:

- `product_id`;
- `activity_date`;
- `adults`;
- `children`;
- `participants`.

O construtor exige `adults >= 1`, `children >= 0` e `participants == adults + children`. A forma histórica somente com `participants` permanece legível para artefatos antigos, mas novos turnos e re-reads sempre produzem a forma completa.

O hash da consulta nova vincula o mix adulto/criança. Uma cotação para duas pessoas adultas não pode autorizar uma reserva para um adulto e uma criança.

### Seleção de categorias

O adapter Bókun cruza:

- `pricingCategories` do produto;
- `pricePerCategoryUnit` da disponibilidade/rate selecionada;
- `ticketCategory` ou equivalente normalizado.

A saída privada associa explicitamente:

- `adult_pricing_category_id` quando `adults > 0`;
- `child_pricing_category_id` quando `children > 0`.

A categoria infantil só é aceita quando publicada e precificada na rate atual. A aplicação não converte criança em adulto silenciosamente. Se o produto não oferecer categoria compatível, a opção não é executável para aquele mix e a Maya deve explicar que precisa de revisão, sem afirmar disponibilidade executável.

O cálculo de preço soma `adult_price × adults + child_price × children`. A checagem de capacidade usa o total. O carrinho de quote inclui uma categoria por pessoa e a validação exige a mesma cardinalidade e o mesmo multiconjunto de categorias.

`PrivateOfferBinding` preserva os IDs privados necessários ao mix. Bindings históricos de adulto único continuam aceitos; bindings novos com criança exigem o campo infantil.

## Seleção conversacional

Remover a política temporária que classifica `party > 1` como não suportada. Party maior que um, isoladamente, não é handoff.

O reparo estruturado de seleção passa a aceitar qualquer party positiva quando:

- produto, data, party e pagamento estão completos;
- a leitura fresca corresponde exatamente a adultos e crianças;
- o perfil de contato está completo;
- o manifesto efetivo está completo;
- a oferta informa preço final com taxa e categorias executáveis.

Se a party estiver completa mas faltarem passageiros, a decisão é `profile_completion`, com zero comandos e uma solicitação humana dos campos faltantes. `request_handoff` permanece disponível por pedido do cliente, desconto, segurança, restrição etária/produto ou falha não automatizável.

Criança não causa handoff apenas por existir. Uma restrição independente de segurança, idade, saúde ou produto continua prevalecendo. O nascimento individual permite calcular idade no dia do passeio sem inferência textual.

Package usa o mesmo manifesto para o componente de atividade. A hospedagem continua usando a party do quarto e não envia manifesto ao Cloudbeds.

## Dispatch de reserva

Introduzir `v2-reservation-dispatch-v2` para Bókun multi-passageiro. O payload canônico inclui:

- binding público e privado da oferta;
- party adulto/criança;
- contato principal;
- lista completa e ordenada de passageiros;
- termos já autorizados.

O schema v1 continua válido somente para adulto único. Grupos e qualquer reserva com criança exigem v2.

A permit/fence continua vinculando `request_hash`, `payload_hash`, `command_id`, `idempotency_key`, autorização e token. O manifesto faz parte do payload hash; um retry só pode repetir os mesmos passageiros.

## Transport Bókun

### Carrinho

Construir `pricingCategoryBookings` com uma entrada por passageiro, usando o ID adulto ou infantil conforme `participant_type`.

A resposta do carrinho deve conter:

- uma única activity ligada ao produto esperado;
- um booking de passageiro para cada posição;
- IDs de booking não vazios e distintos;
- multiconjunto de categorias idêntico ao solicitado.

A ligação é determinística: manter ordem por categoria/posição apenas quando a resposta a preservar; se o provider reordenar, ligar por categoria e ordem estável dentro da categoria. Cardinalidade ou categoria divergente falha antes do submit.

### Checkout e respostas

O checkout deve expor exatamente o mesmo número de grupos de perguntas de passageiro. Cada grupo é ligado ao respectivo booking ID.

Para cada passageiro, responder somente IDs de perguntas publicados pelo checkout. Campos obrigatórios desconhecidos ou perguntas especiais obrigatórias sem resposta suportada falham antes do submit. O contato principal continua separado e usa os dados autenticados do titular.

O submit envia uma entrada por passageiro com:

- booking ID;
- pricing category ID;
- respostas individuais permitidas.

Notificação do provider continua desabilitada; entrega ao cliente permanece no outbox separado.

### Read-back

`EFFECT_CONFIRMED` exige que o read-back prove cumulativamente:

- mesma referência de booking;
- mesmo produto;
- mesma data;
- mesma quantidade total;
- mesma composição de categorias quando o provider a retornar.

Ausência de referência é ambígua conforme regra atual. Referência presente com party/produto/data divergente nunca é sucesso e abre reconciliação.

## Compatibilidade e migração

- `CustomerFacts.passengers=()` preserva estados históricos.
- Serialização lê a forma antiga sem `passengers` e escreve o campo somente quando não vazio.
- Propostas v1–v5 permanecem parseáveis para replay, mas não podem criar um grupo novo; o runtime produz e exige v6 para coleta multi-passageiro.
- Dispatch Bókun v1 permanece aceito para adulto único.
- Cloudbeds, pagamentos, delivery e handoff não ganham dependência nova.
- Nenhum `uv.lock` será criado.

## Falhas e classificação

| Condição | Resultado |
|---|---|
| Manifesto incompleto antes do fence | `NOT_CALLED`, coleta continua |
| Party e manifesto divergentes | `NOT_CALLED`, proposta revogada |
| Categoria necessária ausente antes de qualquer chamada de escrita | `NOT_CALLED`, opção não executável/revisão |
| Validação local do payload falha antes de qualquer chamada HTTP | `NOT_CALLED` |
| Carrinho ou checkout é rejeitado/diverge depois da primeira chamada HTTP, sem submit | `CALLED_NO_EFFECT`, com evidência da fase alcançada |
| Submit rejeitado explicitamente sem booking | `CALLED_NO_EFFECT` |
| Submit sem referência conclusiva | `CALLED_UNKNOWN`, reconciliação |
| Read-back divergente | `CALLED_UNKNOWN`, reconciliação |
| Read-back exato | `EFFECT_CONFIRMED` |

## Estratégia de testes

### RED/GREEN focado

1. Domínio e serialização de `PassengerFacts`, incluindo retrocompatibilidade.
2. Assinatura muda com qualquer dado de passageiro e permanece estável para shape histórico vazio.
3. Parser v6 e manifesto incremental, sem reinjeção de PII em `ModelRequest`.
4. Party/manifesto divergente bloqueia seleção; manifesto completo libera grupo.
5. Grupo não abre handoff apenas por quantidade; motivos independentes continuam prevalecendo.
6. Activity read liga adultos/crianças e rejeita soma divergente.
7. Quote com dois adultos e quote misto adulto/criança usam categorias e totais corretos.
8. Private binding exige categoria infantil apenas quando necessário.
9. Dispatch v2 contém manifesto e hash/idempotência mudam quando ele muda.
10. Cart valida cardinalidade, categorias e IDs distintos.
11. Checkout responde perguntas por passageiro e rejeita shape ambíguo.
12. Read-back prova party/produto/data.
13. Package usa manifesto na atividade sem alterar payload Cloudbeds.

### Regressão

Executar:

- testes focados de domínio, boundary, model adapter, turn executor, reducer, reads, private offers, reservations e provider HTTP;
- toda a suíte `tests/test_v2_*.py`;
- Ruff, `compileall`, boundary guard e static source gate;
- revisão independente do SHA final.

Nenhum teste de implementação chama provider real. Depois de commit, CI e imagem por digest, a validação operacional recomeça em dark-read-only. Um canário real multi-passageiro exige autorização separada, janela finita, allowlist, read-back e cleanup.

## Critérios de aceite

1. Uma reserva de adulto único continua produzindo o contrato anterior e passa todas as regressões.
2. Dois ou mais adultos podem chegar a um único comando Bókun autenticado com um passageiro distinto por pessoa.
3. Grupo misto usa categorias e preços distintos quando publicados pelo provider.
4. Nenhum grupo é enviado se faltar qualquer dado individual ou categoria.
5. A confirmação assina o manifesto; alteração posterior nunca reutiliza o comando.
6. Carrinho, checkout, submit e read-back provam a mesma party.
7. Handoff automático por `party > 1` deixa de existir; handoffs independentes permanecem.
8. Todos os gates reais ficam fechados durante desenvolvimento e testes.
9. O V1 permanece somente leitura e não é dependência da V2.
