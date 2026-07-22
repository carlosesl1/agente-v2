# Phase 8 Sandbox Cloudbeds Read Design

**Status:** desenho aprovado conceitualmente por Carlos em 2026-07-22; este arquivo é o registro escrito para revisão final antes da implementação.

## Objetivo

Evoluir o sandbox conversacional do candidato `cbcb261dbcc6d9a65b437d1379a17dcbf9330572` para consultar preço e disponibilidade reais de hospedagem via Cloudbeds, preservando bloqueio mecânico de reserva, cobrança, pagamento, mensagem e qualquer outro efeito externo.

## Escopo

Esta evolução cobre somente hospedagem:

- interpretar, ao longo da conversa, `check_in`, `check_out`, `adults` e `children`;
- solicitar uma leitura estruturada quando esses campos estiverem completos;
- executar exclusivamente `cloudbeds_consultar_hospedagem_v2` no runtime Chapada;
- devolver ao modelo somente um DTO sanitizado de opções, preço e disponibilidade;
- produzir resposta pública natural e persistir a observação no diário privado do sandbox.

FAQ/Cérebro, passeios/Bókun, reservas, pagamentos, ManyChat, e-mail, Supabase, Redis, deploy e rollout ficam fora do escopo.

## Arquitetura

O processo pai continua sendo o único owner do diário SQLite e da máquina de conversa. O modelo não recebe ferramentas. Ele pode retornar uma solicitação fechada de leitura de hospedagem, mas não executá-la.

O pai valida a solicitação e chama, sem shell, um child efêmero no container `chapada-leads-hermes`. Esse child importa somente a consulta v2 de hospedagem e força o ambiente de segurança antes do import/uso:

- `HERMES_LEADS_MODE=shadow`;
- `HERMES_LEADS_DRY_RUN=false`, necessário para leitura real;
- `HERMES_LEADS_ALLOW_LIVE_SENDS=false`;
- todos os gates Cloudbeds/Bókun de cart, reserva, pagamento, Stripe, Wise e confirmação definidos como falsos;
- credenciais ManyChat, Supabase e Redis removidas da visão do child quando não forem necessárias à leitura.

O child possui apenas uma operação permitida: `cloudbeds_consultar_hospedagem_v2`. Não aceita nome de ferramenta do modelo e não possui dispatcher genérico.

## Protocolo conversacional

A resposta canônica do modelo ganha o campo obrigatório `read_requests`, uma lista inicialmente vazia ou com exatamente uma solicitação:

```json
{
  "kind": "lodging_availability",
  "arguments": {
    "check_in": "2026-08-10",
    "check_out": "2026-08-12",
    "adults": 2,
    "children": 0
  }
}
```

Regras:

- `kind` deve ser exatamente `lodging_availability`;
- datas devem ser ISO `YYYY-MM-DD`, e `check_out > check_in`;
- `adults` deve ser inteiro entre 1 e 20;
- `children` deve ser inteiro entre 0 e 20;
- chaves extras, valores booleanos como inteiros, duplicatas e mais de uma leitura são rejeitados;
- se faltarem dados, o modelo pergunta ao lead e retorna `read_requests=[]`.

Quando uma leitura é solicitada, o pai não publica a primeira resposta. Ele executa a consulta e faz uma segunda chamada ao modelo na mesma submissão, incluindo uma mensagem privada `READ_OBSERVATION` canônica. A segunda resposta deve ter `read_requests=[]`; nova solicitação no mesmo turno falha fechada para impedir loop.

## DTO sanitizado

O resultado do child contém somente:

- `status`: `ok`, `no_bookable_options` ou `provider_error`;
- `availability_confirmed` e `price_confirmed`;
- até cinco opções, cada uma limitada a nome público do quarto, datas, noites, ocupação, unidades disponíveis, valor total, moeda e confiabilidade do preço;
- `public_summary` sanitizado;
- `raw_provider_payload_returned=false`.

IDs de quarto, rate plan, room rate, option ID, payload bruto, erro interno, segredo e credencial não atravessam a fronteira para o modelo ou diário.

## Persistência e atomicidade

A chamada ao modelo e a consulta Cloudbeds ocorrem fora de transações SQLite. Somente após a resposta final canônica o pai grava, em uma transação:

- mensagem do lead;
- resposta pública final;
- observação sanitizada e seu SHA-256;
- efeitos propostos, sempre como `sandbox_effects_disabled`.

Falha do provider ou ausência de opção é uma observação válida e segura. Timeout, JSON inválido ou violação de protocolo não grava turno parcial.

## Segurança

- O modelo não recebe ferramentas Hermes ou Chapada.
- O child Cloudbeds não recebe comando/tool name do modelo.
- Nenhuma API de escrita é importada no sandbox pai.
- Pedidos de reserva/cobrança continuam em `effect_proposals` e são apenas registrados como bloqueados.
- Nenhuma operação toca ManyChat, WhatsApp, pagamento ou o estado operacional do lead.
- Erros retornam linguagem conservadora; preço ou disponibilidade nunca são inventados.

## Validação proporcional

1. RED/GREEN do contrato fechado `read_requests` e da regra de no máximo uma leitura por turno.
2. RED/GREEN do adaptador child sem shell, ambiente fechado e DTO sanitizado.
3. RED/GREEN do ciclo modelo → leitura → modelo, sem transação aberta durante I/O.
4. Conversa real de três turnos:
   - coleta datas e hóspedes;
   - apresenta leitura Cloudbeds real quando disponível;
   - pedido de reserva/cobrança permanece bloqueado.
5. Regressão apenas do sandbox e entry gate; sem suíte pesada, Docker build ou deploy.

## Critérios de aceite

- Uma conversa natural obtém resposta fundamentada em leitura Cloudbeds real ou recusa conservadora rastreável.
- O diário contém a observação sanitizada e hash correspondente.
- Nenhuma reserva, cobrança, mensagem ou outro efeito externo é executado.
- O candidato fica em commit limpo com evidência privada da conversa de validação.
