# Maya Ops — alinhamento visual e biblioteca de ícones

## Estado

Aprovado por Carlos em 2026-08-23.

## Objetivo

Corrigir os defeitos visuais observados no dashboard `/ops`: controles do cabeçalho desalinhados e com alturas divergentes, indicador de atividade duplicado, textos de KPI quebrando palavra por palavra, cards com distribuição interna inconsistente e pictogramas improvisados.

## Escopo

### Cabeçalho

- Seletor de período, botão Atualizar e botão do operador usam uma altura-base comum de 48 px.
- O rótulo “Período” continua visível, mas não desloca a base dos três controles.
- Ícone, texto e avatar ficam centralizados verticalmente.
- O layout continua responsivo e sem overflow horizontal.

### Indicador de atividade

- Exibir exatamente um ponto verde.
- Alinhar o ponto ao bloco com estado e timestamp.
- Manter duas linhas legíveis, padding uniforme e altura determinada pelo conteúdo.

### KPIs

- Os oito cards mantêm altura, padding e hierarquia uniformes.
- O rodapé usa uma área textual flexível e uma área de série/sparkline estável.
- Textos como “Execuções no período” não podem quebrar palavra por palavra.
- Labels longos podem quebrar apenas em linhas naturais.
- Valor, contexto e série permanecem contidos no card em desktop e mobile.

### Ícones

- Usar Lucide como biblioteca única.
- Empacotar localmente somente os ícones necessários; nenhuma CDN ou dependência de rede em runtime.
- Não desenhar paths SVG ad hoc.
- Os SVGs oficiais preservam `currentColor`, dimensões consistentes e `aria-hidden=true` quando decorativos.
- Substituir caracteres improvisados no shell, ações, busca e KPIs.

## Restrições

- Sem mudança em API, backend, autenticação, SQLite, agente, runtime, providers ou V3.
- A aplicação permanece autenticada e read-only.
- Sem `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write` ou handlers inline.
- Não introduzir chamadas externas de fonte/ícone.
- Preservar startup singleton, EventSource único e listener responsivo único.

## Verificação

1. Teste causal RED deve reproduzir os defeitos atuais de geometria e o uso de pictogramas improvisados.
2. Testes estáticos devem provar o carregamento local da biblioteca e ausência de CDN/ícones ad hoc nos componentes abrangidos.
3. Chromium real deve verificar desktop e mobile:
   - controles com altura uniforme e alinhamento de base;
   - exatamente um indicador de atividade;
   - oito KPIs de altura uniforme;
   - nenhum texto palavra por palavra;
   - nenhum conteúdo ou sparkline fora do card;
   - zero overflow horizontal.
4. Smoke autenticado do serviço publicado deve confirmar release SHA, assets locais, console sem erros e layout corrigido.
5. Deploy deve recriar somente o serviço `v2-ops`, preservar a fonte SQLite em modo read-only e manter rollback para `616b926`.
