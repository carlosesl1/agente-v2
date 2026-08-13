# Maya V2 — dashboard operacional por execução

**Data:** 2026-08-13
**Branch:** `maya-v2-ops-dashboard`
**Worktree:** `/home/ubuntu/agente-v2/.worktrees/maya-v2-ops-dashboard`
**Base:** `9226d1b91cdf6007c8f5ce72d0a572e35c4f8a5b`
**Destino:** `https://hermes.chapadabackpackers.com/ops`

## 1. Decisão

Construir um dashboard operacional somente leitura, inspirado na visualização de execuções do n8n. A tela inicial lista execuções recentes e permite busca exata por Lead ID. Ao abrir uma execução, o operador vê um canvas de nós conectados, atualizado durante o processamento.

O inspetor de cada nó mostra **Input e Output simultaneamente**, sem abas separadas. Resumos estruturais aparecem por padrão; o conteúdo completo é carregado somente quando o operador autenticado solicita.

A unidade de execução do MVP é **uma mensagem/evento recebido**:

```text
Lead ID: manychat:<subscriber_id>
└── Execução: event_id
    ├── Webhook
    ├── Router
    ├── Inbox
    ├── Maya rodada 1
    ├── consulta solicitada pela Maya
    ├── request enviado ao provider
    ├── response recebido do provider
    ├── observation devolvida à Maya
    ├── Maya rodada 2
    ├── reducer
    ├── efeitos condicionais
    └── entrega ManyChat
```

Todas as execuções de um mesmo lead são agrupadas pelo Lead ID. O dashboard não usa nome, telefone ou texto público como identidade técnica.

## 2. Escopo do MVP

### Incluído

1. Login próprio em `/ops/login`, com usuário e senha exclusivos do dashboard.
2. Lista de execuções recentes.
3. Busca exata por Lead ID no formato técnico do V2.
4. Canvas por execução, com estados:
   - `pending`;
   - `running`;
   - `completed`;
   - `failed`;
   - `manual_review`;
   - `not_applicable`.
5. Inspetor de nó com Input e Output na mesma tela.
6. Resumo estrutural por padrão e conteúdo completo sob demanda.
7. Atualização automática por polling curto.
8. Nós de saúde e execução do harness.
9. Nós das consultas da Maya e dos providers.
10. Cloudbeds, Bókun, Stripe, Pix/Wise, ManyChat e handoff quando participarem da execução.
11. Metadados de claim, lease, fencing, idempotência, tentativa, duração e receipts, quando disponíveis.
12. Histórico de execuções anteriores que puder ser reconstruído a partir dos ledgers atuais, marcado como `partial_trace` quando faltarem eventos detalhados.

### Não incluído

- reprodução ou retry de uma execução;
- edição de estado;
- reservas, bookings, Payment Links, cobranças ou mensagens iniciadas pelo dashboard;
- restart de containers;
- alteração de gates ou kill switches;
- acesso direto do navegador aos bancos SQLite;
- analytics comercial, funil de vendas ou relatórios financeiros;
- trabalho ou mudanças no Agente V3.

## 3. Requisito central: consultas e providers

O canvas deve distinguir quatro fatos diferentes. Eles nunca serão condensados em um único “nó de tool”:

```text
MAYA QUERY
O que a Maya pediu semanticamente
        │
        ▼
PROVIDER REQUEST
O que o runtime realmente enviou ao provider
        │
        ▼
PROVIDER RESPONSE
O que o provider realmente devolveu
        │
        ▼
MAYA OBSERVATION
O resultado normalizado e sanitizado devolvido à Maya
```

### 3.1 Consulta solicitada pela Maya

Exibe o `read_request` ou a proposta de efeito emitida pela Maya dentro do contrato fechado.

Exemplos:

- `knowledge`;
- `lodging`;
- `room_description`;
- `activity`;
- `activity_description`;
- seleção de oferta;
- confirmação semântica;
- solicitação de handoff.

**Input:** mensagem/contexto tipado entregue ao modelo, estado, facts e observations anteriores.
**Output:** intenção, facts, `read_requests`, escolhas, `reply_chunks` e proposta estrutural.

### 3.2 Request enviado ao provider

Exibe a operação construída e validada pelo runtime, não uma inferência do texto da Maya.

Campos permitidos incluem:

- provider;
- operação;
- método HTTP;
- path lógico;
- parâmetros tipados;
- datas;
- ocupação;
- produto/rate/start-time públicos ou técnicos permitidos;
- preço e moeda esperados;
- idempotency key truncada ou hash;
- attempt;
- claim/fence;
- timestamp de início.

Nunca exibir:

- headers de autenticação;
- API keys;
- tokens;
- secrets;
- cookies;
- assinatura Bókun;
- bearer token Cloudbeds;
- chave Stripe;
- credencial ManyChat.

### 3.3 Response recebido do provider

Exibe:

- código HTTP;
- duração;
- certeza do resultado;
- referência externa permitida;
- quantidade de ofertas/opções;
- preço/moeda;
- status normalizado;
- receipt/hash;
- erro técnico seguro, quando houver.

O payload bruto do provider não será enviado ao navegador. O conteúdo completo disponível no painel é a **representação tipada e permitida** persistida pelo runtime.

### 3.4 Observation devolvida à Maya

Exibe exatamente a estrutura aceita pelo `V2ReadService` e entregue na segunda rodada do modelo:

- `provider`;
- `request_hash`;
- `observed_at`;
- `expires_at`;
- `public_payload`;
- `choice_ref`;
- estado de frescor/aceitação.

Não inclui binding privado nem credencial.

## 4. Catálogo de nós

O catálogo é fechado no backend. A UI não inventa nós a partir de texto.

| Família | Nós possíveis |
|---|---|
| Ingresso | `manychat_webhook`, `router_validation`, `inbox_accept`, `inbox_claim` |
| Maya | `maya_request`, `maya_response`, `maya_correction`, `maya_review` |
| Leitura | `maya_read_request`, `provider_read_request`, `provider_read_response`, `maya_observation` |
| Decisão | `conversation_reducer`, `turn_commit`, `boundary_relay` |
| Reserva | `cloudbeds_reservation_request`, `cloudbeds_reservation_response`, `bokun_booking_request`, `bokun_booking_response` |
| Pagamento | `stripe_product`, `stripe_price`, `stripe_payment_link`, `pix_instruction`, `wise_instruction`, `settlement` |
| Saída | `public_outbox`, `manychat_delivery_request`, `manychat_delivery_response`, `handoff_request`, `handoff_delivery` |
| Recuperação | `provider_reconciliation`, `stripe_reconciliation`, `manual_review` |

Nós não aplicáveis podem ser omitidos. O canvas mostra somente o caminho realmente percorrido e os ramos condicionais já decididos.

## 5. Modelo de dados de observabilidade

### 5.1 Fontes existentes

O projetor read-only consulta os bancos já existentes:

- `inbox.sqlite3`;
- `v2-boundary.sqlite3`;
- `v2-execution.sqlite3`;
- `v2-payment-initiation.sqlite3`;
- `v2-followup.sqlite3`;
- `v2-public-outbox.sqlite3`;
- `v2-cloudbeds-audit.sqlite3`;
- `v2-bokun-audit.sqlite3`;
- `v2-private-customer.sqlite3`;
- `v2-worker-heartbeat.json`;
- `ga-runtime-image-metadata.json`.

Os bancos são abertos por URI SQLite em modo read-only. O navegador nunca recebe caminho de arquivo nem SQL arbitrário.

### 5.2 Trace operacional novo

Os ledgers atuais não preservam, para todos os caminhos, cada request do modelo e cada request/response do provider como uma sequência visual completa. O worker passará a produzir um trace operacional dedicado:

`/data/v2-ops-trace.sqlite3`

O dashboard monta esse arquivo somente como leitura. O worker é o único writer.

Tabelas conceituais:

```text
ops_executions
├── execution_id = event_id
├── lead_id
├── received_at
├── completed_at
├── status
├── current_node_id
├── trace_completeness
└── terminal_reason

ops_nodes
├── node_id determinístico
├── execution_id
├── node_type
├── ordinal
├── parent_node_id
├── status
├── started_at
├── completed_at
├── attempt
├── input_summary_json
├── output_summary_json
├── input_full_encrypted
├── output_full_encrypted
├── error_json
├── technical_metadata_json
└── content_hash
```

A gravação é append/update monotônica por `node_id` determinístico. Replays idempotentes atualizam a mesma identidade; não criam uma segunda execução visual.

### 5.3 Compatibilidade histórica

Execuções anteriores ao trace dedicado são projetadas a partir dos ledgers atuais. O backend marca explicitamente:

- `complete_trace`: todos os nós relevantes estão no trace;
- `partial_trace`: jornada reconstruída, mas alguns Inputs/Outputs completos não existem;
- `ledger_only`: somente marcos de ledger podem ser apresentados.

A UI nunca fabrica um request ou response ausente.

## 6. Instrumentação

A instrumentação é adicionada nas fronteiras onde os objetos tipados já existem:

1. antes e depois de cada chamada à Maya;
2. quando a Maya produz `read_requests`;
3. antes da chamada do adapter de leitura;
4. depois da resposta normalizada do adapter;
5. antes e depois de Cloudbeds/Bókun writes;
6. em cada etapa Stripe: Product, Price e Payment Link;
7. ao produzir instrução Pix/Wise;
8. antes e depois da entrega ManyChat/handoff;
9. durante reconciliação e promoção para manual review.

Regras:

- a instrumentação não interpreta linguagem natural;
- summaries são derivados de campos tipados, contagens e estados;
- falha no trace não muda decisão comercial, não provoca retry de provider e não reescreve a Maya;
- uma falha do trace aparece como `trace_degraded` no dashboard e nos logs;
- nenhuma captura ocorre interceptando headers HTTP brutos;
- requests são registrados a partir do DTO permitido imediatamente antes do transporte;
- responses são registrados após validação/normalização do adapter.

## 7. API do dashboard

Todos os endpoints, exceto login e health interno, exigem sessão autenticada.

```text
GET  /ops/login
POST /ops/login
POST /ops/logout
GET  /ops/
GET  /ops/api/snapshot
GET  /ops/api/executions?lead_id=&status=&limit=&cursor=
GET  /ops/api/executions/{execution_id}
GET  /ops/api/executions/{execution_id}/nodes
GET  /ops/api/executions/{execution_id}/nodes/{node_id}/full
GET  /ops/api/harness
GET  /ops/api/release
```

### Resposta resumida de um nó

```json
{
  "node_id": "identificador-tecnico",
  "node_type": "provider_read_request",
  "provider": "cloudbeds",
  "status": "completed",
  "started_at": "timestamp",
  "completed_at": "timestamp",
  "duration_ms": 842,
  "attempt": 1,
  "input_summary": {},
  "output_summary": {},
  "has_full_input": true,
  "has_full_output": true,
  "error": null,
  "metadata": {}
}
```

O endpoint `/full` exige o mesmo login e somente retorna a representação completa permitida para aquele nó.

## 8. Autenticação

O dashboard terá credenciais próprias, diferentes do WebUI e do webhook.

Configuração por ambiente:

- `V2_OPS_USERNAME`;
- `V2_OPS_PASSWORD_HASH`;
- `V2_OPS_SESSION_KEY_HEX`;
- `V2_OPS_SESSION_TTL_SECONDS`.

Decisões:

- senha não é armazenada em texto claro;
- comparação constante;
- cookie `Secure`, `HttpOnly`, `SameSite=Strict`;
- sessão assinada e expirada no servidor;
- logout invalida o cookie do navegador;
- respostas de login não distinguem usuário inexistente de senha incorreta;
- nenhuma credencial é compartilhada com API, worker, Cloudbeds, Bókun, Stripe, ManyChat ou Hermes.

## 9. Roteamento e deploy

O dashboard será um serviço separado do Hermes WebUI e do V2 ingress.

```text
Internet
  → Traefik
      ├── Host(hermes.chapadabackpackers.com) + /ops
      │      → agente-v2-ops-dashboard
      └── Host(hermes.chapadabackpackers.com) + /
             → hermes-webui atual
```

O router de `/ops` terá prioridade maior que o router genérico do WebUI. A regra será limitada a:

```text
Path(`/ops`) OR PathPrefix(`/ops/`)
```

O serviço:

- usa usuário não-root;
- filesystem read-only;
- monta `ga-state` em modo read-only;
- monta metadata de release em modo read-only;
- usa tmpfs mínimo;
- remove capabilities;
- não monta Docker socket;
- não recebe credenciais de providers;
- não compartilha as credenciais do WebUI;
- não expõe porta diretamente à internet fora do Traefik.

O deploy do dashboard não altera a rota pública comprovada do webhook V2 em `leads-hermes.chapadabackpackers.com`.

## 10. Interface

### 10.1 Tela inicial

- busca exata por Lead ID;
- execuções recentes ordenadas por `received_at DESC`;
- filtro por status;
- estágio atual;
- duração;
- indicador de completude do trace;
- clique abre o canvas.

### 10.2 Canvas

- nós em sequência e ramos condicionais;
- animação discreta no nó `running`;
- cores por estado, não por interpretação comercial;
- zoom e enquadramento;
- auto-refresh preserva nó selecionado e posição do canvas;
- nenhuma ação de replay, retry ou edição.

### 10.3 Inspetor

Input e Output permanecem visíveis na mesma tela:

```text
[Nó selecionado]
[status] [duração] [tentativa]

┌──────────────────────────┐
│ INPUT                    │
│ resumo                   │
│ JSON estruturado         │
│ ver conteúdo completo    │
└──────────────────────────┘

┌──────────────────────────┐
│ OUTPUT                   │
│ resumo                   │
│ JSON estruturado         │
│ ver conteúdo completo    │
└──────────────────────────┘

[metadados recolhíveis]
[erro recolhível]
```

### 10.4 Atualização

O MVP usa polling a cada dois segundos:

- lista de execuções;
- execução aberta;
- heartbeat do harness.

Polling é preferido ao SSE no MVP por simplicidade operacional. A API usa cursor e ETag/hash para evitar transferir o canvas inteiro quando nada mudou.

## 11. Comportamento de erro

| Situação | Comportamento |
|---|---|
| Heartbeat velho | banner `harness stale`; último timestamp permanece visível |
| SQLite bloqueado/indisponível | resposta 503 sanitizada; sem fallback de escrita |
| Trace ausente | execução marcada `ledger_only` |
| Nó incompleto após crash | nó `running_stale` ou `unknown`; nunca convertido em sucesso |
| Provider outcome desconhecido | `manual_review`/`reconciliation`, sem inferir sucesso |
| Sessão expirada | retorno ao login |
| Conteúdo completo indisponível | resumo permanece e botão explica `not_recorded` |
| Poll falha | UI mantém snapshot anterior com aviso de desconexão |

## 12. Testes

### Unitários

- schema e idempotência do trace;
- transições monotônicas de nós;
- summaries derivados somente de tipos;
- exclusão de headers/secrets;
- projeção de Cloudbeds, Bókun, Stripe e ManyChat;
- correlação Lead ID → execution ID → nodes;
- compatibilidade `partial_trace` e `ledger_only`;
- autenticação, expiração e logout;
- paginação, filtro e busca exata.

### Integração

- bancos temporários reais em modo read-only;
- worker escreve trace e dashboard lê sem obter acesso de escrita;
- Maya rodada 1 → read request → provider request/response → observation → rodada 2;
- reserva Cloudbeds;
- booking Bókun;
- Product → Price → Payment Link Stripe em adapter falso/teste;
- entrega ManyChat falsa;
- crash entre request e response aparece como desconhecido, não concluído;
- nenhum teste chama provider real.

### UI

- login;
- lista recente e busca por Lead ID;
- abertura de canvas;
- Input e Output simultâneos;
- conteúdo completo sob demanda;
- atualização de nó `running` para `completed`;
- estado de falha/manual review;
- layout de desktop e fallback linear em tela pequena.

### Operacional

- compose renderiza;
- serviço não recebe secrets de provider;
- mount de `ga-state` é `:ro`;
- rota `/ops` exige login;
- `/`, sessões e APIs existentes do WebUI continuam atendidas pelo WebUI;
- webhook V2 continua saudável;
- nenhuma reserva, pagamento, handoff ou mensagem é executada pelo smoke do dashboard.

## 13. Sequência de implantação

1. Implementar trace e projetor com adapters falsos.
2. Implementar API e login.
3. Implementar UI do canvas.
4. Rodar testes focados e suite afetada.
5. Construir imagem imutável do dashboard/trace.
6. Subir `/ops` em dark read-only, sem alterar worker ativo.
7. Verificar login, WebUI e rotas existentes.
8. Atualizar worker para a imagem instrumentada somente depois dos gates, preservando os mesmos contratos e efeitos.
9. Gerar uma solicitação controlada sem efeito comercial para confirmar trace ao vivo.
10. Publicar evidência sanitizada e decisão GO/NO-GO.

O passo 8 não autoriza testes de reserva, Payment Link, cobrança, handoff ou mensagem artificial. A instrumentação será comprovada primeiro com consulta read-only e com os efeitos existentes não acionados.

## 14. Critérios de aceite

O MVP é aceito quando:

1. `/ops` abre uma tela de login própria.
2. Usuário não autenticado não acessa HTML operacional nem APIs.
3. A tela inicial lista execuções e busca por Lead ID.
4. Uma execução mostra o canvas real, sem dados sintéticos.
5. Input e Output aparecem juntos em cada nó.
6. As consultas solicitadas pela Maya aparecem separadas dos requests enviados aos providers.
7. Cloudbeds, Bókun, Stripe e ManyChat aparecem quando usados.
8. Request e response de provider mostram somente campos permitidos, sem credenciais.
9. O operador pode abrir conteúdo completo quando ele existe.
10. O nó ativo muda de estado durante uma execução controlada.
11. O dashboard não possui endpoint ou botão de escrita operacional.
12. WebUI, V2 webhook, containers e V3 permanecem independentes.
13. Testes e smoke comprovam zero efeitos externos produzidos pelo dashboard.

## 15. Decisões de produto consolidadas

- Visual: canvas de execução inspirado no n8n.
- Unidade: uma execução por mensagem/evento.
- Agrupamento: Lead ID.
- Entrada: execuções recentes + busca por Lead ID.
- Inspetor: Input e Output na mesma tela.
- Conteúdo: resumo por padrão; completo sob demanda.
- Acesso: login próprio com usuário e senha.
- URL: `https://hermes.chapadabackpackers.com/ops`.
- Operação: somente leitura.
- Live update: polling de dois segundos no MVP.
- Isolamento: serviço separado do WebUI, do ingress V2 e do V3.
