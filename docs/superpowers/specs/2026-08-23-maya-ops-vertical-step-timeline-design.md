# Maya Ops — timeline vertical de passos da execução

## Estado

Desenho aprovado por Carlos em 2026-08-23.

## Objetivo

Substituir, no drawer de detalhes de uma execução do `/ops`, o canvas atual de nós em quadrados conectados por uma timeline vertical simples, equivalente ao visual inicial lembrado pelo usuário: bolinhas empilhadas, número dentro de cada bolinha e nome do passo ao lado.

## Escopo visual

### Timeline

- Exibir os nós da execução em uma única coluna vertical, na ordem factual já recebida da API.
- Cada nó é uma linha clicável composta por:
  - uma bolinha circular;
  - o número ordinal do passo dentro da bolinha;
  - o nome do passo ao lado.
- Uma linha vertical discreta liga o centro das bolinhas consecutivas.
- O primeiro e o último item não exibem linha além dos limites da sequência.
- O passo selecionado recebe destaque verde coerente com a paleta atual.
- Os demais passos mantêm aparência neutra e legível.
- O nome é derivado exclusivamente de `node_type`, usando a normalização visual já existente; nenhum nome é inventado.
- A bolinha usa `ordinal`; a posição no array só pode ser fallback visual se o ordinal recebido não for um inteiro positivo.

### Conteúdo deliberadamente omitido

A timeline mostra somente:

1. número do passo;
2. nome do passo.

Não exibir na linha tentativa, status, duração, horário, descrição auxiliar, ícone ou card retangular. Esses dados continuam disponíveis apenas onde já são apresentados factualmente no inspector.

### Elementos removidos

- Quadrados posicionados em zigue-zague.
- Curvas SVG entre os quadrados.
- Fundo quadriculado do canvas.
- Coordenadas absolutas dos nós.
- Escala e controles de zoom, pois não têm função numa lista vertical.
- Largura mínima artificial que força rolagem horizontal.

## Comportamento preservado

- Abrir uma execução continua carregando os nós pelo endpoint read-only atual.
- O primeiro nó continua selecionado inicialmente quando houver nós.
- Clicar em uma etapa continua chamando a seleção existente e atualizando o inspector.
- O inspector mantém título, Input, Output, metadados, erro e ações de carregar conteúdo completo.
- Fechar pelo botão, backdrop ou `Escape`, restaurar foco, atualizar uma execução aberta e tratar respostas assíncronas continuam inalterados.
- O drawer, autenticação, EventSource, filtros, tabela, cards mobile, backend, API e SQLite não mudam.
- Uma execução sem nós mantém estado vazio factual e não cria etapas sintéticas.

## Acessibilidade e DOM seguro

- Cada etapa permanece um `button` nativo.
- A etapa selecionada expõe `aria-current="step"`; as demais não expõem esse estado.
- Foco de teclado deve ser claramente visível.
- Número e nome permanecem legíveis sem depender apenas de cor.
- A implementação continua usando criação segura de elementos e `textContent`.
- Permanecem proibidos `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, handlers inline e estilos inline.

## Responsividade

### Desktop

- Timeline ocupa a área esquerda do drawer sem overflow horizontal.
- Inspector permanece à direita, com a geometria atual preservada.
- A lista pode rolar verticalmente quando houver muitos passos.

### Mobile

- Timeline aparece acima do inspector, seguindo o empilhamento atual do drawer.
- Bolinhas, linha e nomes cabem em `390×844` sem clipping ou overflow horizontal.
- Cada botão oferece alvo de toque mínimo de 44 px de altura.
- Nomes longos quebram naturalmente ao lado da bolinha, nunca dentro dela nem palavra por palavra.

## Restrições

- Mudança limitada a `v2_ops/static/ops.js`, `v2_ops/static/ops.css` e provas diretamente relacionadas.
- Nenhuma mudança em contratos backend/API, autenticação, dados, runtime do agente, providers ou V3.
- Aplicação permanece autenticada e read-only.
- Nenhum dado demonstrativo, banco, screenshot, credencial ou artefato de browser será versionado.
- A biblioteca Lucide e o restante do polimento visual publicado permanecem intactos.

## Verificação

1. Um teste causal RED deve rejeitar a estrutura atual de quadrados, arestas SVG, coordenadas absolutas e zoom, e exigir a timeline numerada.
2. Testes estáticos devem provar:
   - botão seguro por etapa;
   - número e nome como únicos conteúdos visíveis da linha;
   - `aria-current="step"` apenas no selecionado;
   - ausência de sinks DOM inseguros e estilos inline;
   - preservação do fluxo de seleção e inspector.
3. Chromium real, com exatamente um worker e zero skips, deve validar desktop `1440×1000` e mobile `390×844`:
   - uma única coluna vertical;
   - bolinhas alinhadas e numeradas;
   - linha contínua somente entre etapas;
   - nome de cada passo ao lado;
   - seleção e atualização de Input/Output funcionando;
   - foco visível e touch target mobile;
   - zero clipping, overlap, overflow horizontal, `console.error` ou request falho.
4. Revisão independente deve confirmar que o delta não altera API, read-only, startup/EventSource/MediaQuery, autenticação ou os demais componentes do drawer.
5. Uma eventual publicação deve ser reversível, recriar somente `v2-ops` e passar por smoke público autenticado antes de atualizar os ponteiros operacionais.

## Critério de aceite

Ao abrir uma execução, Carlos vê uma sequência vertical simples de bolinhas numeradas com o nome de cada passo ao lado — sem quadrados conectados — e consegue selecionar qualquer passo para consultar os mesmos dados reais de Input/Output existentes hoje.
