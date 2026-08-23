# Maya Ops — refino visual conservador

## Estado

Desenho aprovado por Carlos em 2026-08-23.

## Contexto

A release `c228148e110a6b0f7aeb5ca43758db85e4b64f3e` restaurou corretamente a timeline vertical e preservou os contratos funcionais do `/ops`. A auditoria visual pós-publicação encontrou oportunidades de acabamento, não defeitos de dados ou comportamento: contraste secundário suave demais, seleção da timeline dependente em excesso da bolinha, densidade tipográfica muito pequena em chips e fatos, pouca separação entre timeline e inspector e rolagem interna desnecessária da timeline no mobile.

## Objetivo

Elevar o acabamento visual do dashboard e do drawer sem alterar sua identidade verde/areia, composição, conteúdo factual ou comportamento. O resultado deve parecer uma evolução da interface atual, não um redesign.

## Princípios

1. **Identidade preservada:** manter verdes, marfim e areia como paleta dominante.
2. **Contraste funcional:** textos secundários, bordas e estados precisam ser legíveis sem ficarem pesados.
3. **Hierarquia discreta:** usar espaçamento, peso, superfície e borda antes de adicionar cor ou decoração.
4. **Estado inequívoco:** hover, foco e seleção devem ser diferentes entre si e compreensíveis sem depender apenas de cor.
5. **Densidade operacional:** compacta, mas sem tipografia de 8 px em informações que o operador precisa ler.
6. **Sem deriva funcional:** dados, ordem, seleção, Input/Output, autenticação e read-only permanecem intactos.

## Escopo visual

### 1. Tokens de cor e superfícies

- Manter `--brand-700`, `--brand-800`, `--ivory-50` e as cores semânticas atuais.
- Escurecer moderadamente `--text-muted` para melhorar textos auxiliares em marfim e branco.
- Reforçar `--sand-300` o suficiente para que bordas de cards, campos, timeline e inspector sejam perceptíveis sem dominar a tela.
- Introduzir somente tokens derivados da paleta existente para:
  - superfície secundária muito suave;
  - fundo de hover;
  - fundo da etapa selecionada;
  - rail da timeline.
- Não introduzir azul, roxo, gradientes decorativos ou uma segunda cor de marca.
- Texto normal e controles devem passar por verificação automatizada de contraste; nenhum texto informativo pode depender de opacidade baixa sobre fundo variável.

### 2. Drawer e hierarquia

- Preservar largura, posição, backdrop, transição e estrutura do drawer.
- Tornar a separação entre cabeçalho, fatos e detalhe de execução mais clara por espaçamento consistente e bordas discretas.
- Manter os fatos em cards compactos, mas elevar:
  - rótulos de 9 px para pelo menos 10 px;
  - valores de 11 px para pelo menos 12 px;
  - line-height para evitar aparência comprimida.
- Manter chips semanticamente coloridos, elevando o texto de 8 px para pelo menos 10 px e ajustando padding para conservar proporção.
- Não adicionar títulos, métricas ou dados novos.

### 3. Timeline vertical

Cada linha continua contendo visualmente apenas:

1. círculo numerado;
2. nome factual do passo.

Refinamentos:

- Manter círculos de 36 px e a coluna geométrica atual.
- Tornar o rail ligeiramente mais perceptível, sempre atrás dos círculos e sem ultrapassar o último passo.
- Aumentar o nome do passo de 12 px para 13 px, com line-height confortável.
- A etapa neutra mantém fundo transparente.
- Hover aplica fundo derivado de areia, sem preencher o círculo como se fosse seleção.
- Seleção aplica simultaneamente:
  - círculo verde preenchido;
  - nome em verde forte;
  - fundo verde muito suave na linha;
  - indicador lateral ou borda interna discreta, sem alterar a coluna dos círculos.
- Foco de teclado usa outline visível e distinto do fundo de seleção.
- Não adicionar status, duração, tentativa, horário, ícone, descrição ou tooltip visual à linha.

### 4. Timeline e inspector

- Desktop mantém timeline à esquerda e inspector à direita.
- Timeline e inspector usam superfícies relacionadas, mas distinguíveis:
  - timeline com fundo secundário quente e suave;
  - inspector em superfície branca/marfim;
  - bordas com a mesma intensidade.
- O cabeçalho `Execução` da timeline e o rótulo `INSPETOR` devem compartilhar uma escala coerente de espaçamento e contraste.
- Painéis Input/Output preservam conteúdo e controles; seus cabeçalhos usam fundo secundário menos saturado e borda consistente.
- Sombras permanecem suaves e uniformes; não criar elevação diferente para cards do mesmo nível hierárquico.

### 5. Mobile

- Drawer continua ocupando exatamente a largura do viewport.
- Timeline continua acima do inspector.
- Em `390×844`, remover o `max-height` interno da timeline e deixar o drawer controlar a rolagem vertical. Todos os passos devem participar do fluxo normal, sem uma segunda área de scroll curta dentro do drawer.
- Preservar alvo de toque mínimo de 44 px; a implementação atual de 48 px pode ser mantida.
- Manter nomes com quebra natural, sem clipping, overflow horizontal ou empilhamento palavra por palavra.
- Ajustar padding lateral da timeline para preservar alinhamento e espaço útil sem encostar conteúdo nas bordas.
- Cabeçalho, botão de fechar, fatos e inspector permanecem dentro do viewport durante e após a transição do drawer.

### 6. Dashboard fora do drawer

O refino deve manter o layout publicado e aplicar apenas consistência de tokens:

- textos auxiliares usam o novo contraste de `--text-muted`;
- bordas de KPIs, analytics, operações e controles usam o mesmo token reforçado;
- chips e labels operacionais seguem o piso tipográfico definido;
- sombras de cards permanecem uniformes;
- nenhum card, gráfico, ícone Lucide, controle ou grid muda de posição ou cardinalidade.

## Estados interativos

### Timeline

- **Neutro:** círculo com borda, fundo claro, nome em tinta principal.
- **Hover:** fundo areia suave; círculo pode reforçar a borda, mas não receber o preenchimento reservado à seleção.
- **Selecionado:** fundo verde suave, círculo verde preenchido, nome verde forte e indicador lateral discreto.
- **Foco:** outline claramente visível, inclusive quando a etapa já está selecionada.

### Controles e cards

- Hover de botões e campos permanece coerente com a paleta e não reduz contraste.
- Focus-visible global continua presente.
- Disabled continua visualmente distinto e não é confundido com estado ativo.
- Chips mantêm significado semântico por texto e cor; o ponto decorativo não é a única indicação.

## Comportamento preservado

- Mesmos endpoints e payloads.
- Mesma ordem e cardinalidade de nodes.
- Mesmo fallback ordinal one-based.
- Primeiro passo selecionado inicialmente.
- Clique e foco continuam atualizando `aria-current`, Input e Output.
- Full-value, metadados e erro sanitizado permanecem.
- Drawer fecha por botão, backdrop e `Escape`, restaurando foco.
- Startup, EventSource, MediaQuery, filtros, tabela, cards mobile e overview permanecem.
- Autenticação, CSP, safe DOM, SQLite e read-only permanecem.
- Subconjunto Lucide local permanece byte-identificável e sem CDN.

## Restrições de implementação

- Mudança de produto limitada a `v2_ops/static/ops.css`, salvo necessidade executável demonstrada por teste antes de tocar HTML ou JavaScript.
- Testes podem mudar em `tests/test_v2_ops_ui.py` e `tests/browser/ops_dashboard_smoke.py`.
- Proibidos estilos inline, handlers inline, sinks HTML inseguros, dependências novas e assets remotos.
- Nenhum backend, API, DB, runtime, provider, V3, autenticação ou deploy entra no delta funcional.
- Nenhum screenshot, banco, WAL/SHM, cache, credencial, env ou relatório operacional será versionado.

## Estratégia de verificação

### RED estático causal

Antes da alteração CSS, testes devem falhar por exigir:

- piso tipográfico de chips e fatos;
- distinção entre hover e seleção da timeline;
- fundo selecionado e foco preservado;
- token de rail/superfície derivado da paleta;
- remoção do `max-height` interno da timeline no mobile;
- ausência de alteração em HTML/JS quando o refinamento puder ser resolvido somente por CSS.

### GREEN e regressão

- Suite estática completa do `/ops`.
- `compileall` e `git diff --check`.
- Ausência dos sinks e estilos proibidos.
- Pathset funcional estritamente limitado.

### Chromium real

Executar com exatamente um worker e zero skips em:

- desktop `1440×1000`;
- mobile `390×844`.

Provar:

- contraste computado adequado para textos secundários, chips, fatos e nome da etapa;
- hover, seleção e foco visualmente distintos;
- único `aria-current="step"`;
- círculo/rail/nome alinhados;
- rail não ultrapassa o último passo;
- timeline e inspector sem overlap;
- no mobile, timeline sem scroll interno e todos os passos no fluxo do drawer;
- drawer dentro do viewport após a transição;
- zero clipping ou overflow horizontal;
- Input/Output atualizados após seleção;
- zero `console.error`, `pageerror` e request falho.

### Revisão e publicação

- Revisão independente deve comparar o candidato com esta especificação e confirmar ausência de deriva funcional.
- Publicação, se aprovada, deve construir imagem ligada ao SHA, executar dark smoke autenticado, recriar somente `v2-ops`, preservar rollback para `c228148e110a` e passar por Chromium público antes de promover os ponteiros.

## Critérios de aceite

1. A interface continua imediatamente reconhecível como o Maya Ops atual.
2. Textos secundários, chips, fatos e nomes da timeline ficam mais legíveis.
3. Hover, foco e seleção são inequívocos e não dependem apenas da cor do círculo.
4. Timeline e inspector têm hierarquia mais clara sem nova decoração.
5. Mobile exibe os passos no fluxo normal do drawer, sem segunda rolagem interna curta.
6. Nenhum dado, comportamento, endpoint, contrato read-only ou superfície V3 muda.
7. Desktop e mobile passam pelos gates executáveis e pela inspeção visual independente.
