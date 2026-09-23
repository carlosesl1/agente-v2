# Stripe → ManyChat: unidade recebedora e idioma

O adaptador seleciona o fluxo pelo par `(business_unit, customer_language)` da obrigação e oferta Stripe autenticadas. A URL Stripe é opaca: domínio, fragmentos da URL, prosa da Maya e ordem dos serviços não identificam o CNPJ. Não há fallback para o fluxo genérico da Agência.

| Unidade | Idioma | Fluxo | Campo URL | Campo descrição |
|---|---|---|---:|---:|
| Hostel | `pt-BR` | `content20260317150732_004280` | 14385975 | 14389398 |
| Hostel | `en` | `content20260317175037_863148` | 14385975 | 14389398 |
| Agência | `pt-BR` | `content20260316210707_801647` | 14385973 | 14389400 |
| Agência | `en` | `content20260317181131_597245` | 14385973 | 14389400 |

Namespaces fornecidos pelo operador e corroborados por `GET /fb/page/getFlows`; IDs e nomes dos campos corroborados por `GET /fb/page/getCustomFields`. Esse inventário não expõe a configuração interna dos nós CTA: a prova visual do destino final continua sendo um gate WhatsApp separado.

## Contrato

1. O `CompletionProjector` mantém os IDs públicos existentes. Na entrega, resolve `source_message_id` contra a oferta concluída do store autenticado, encontra a obrigação e verifica lead, conta recebedora, método Stripe, idioma e conteúdo exato da ação de pagamento.
2. O adaptador escolhe a rota tipada. Grava **somente** URL/descrição da unidade correspondente em uma chamada `setCustomFields`, com a URL exata da oferta, e depois chama `sendFlow` com o namespace dessa rota.
3. Uma divergência é rejeitada antes de escrever campos. Sem oferta/owner/idioma/rota, não envia pagamento por outro fluxo nem converte a URL em prosa.
4. Os campos Hostel e Agência são disjuntos. Os quatro fluxos são distintos. Valores ausentes, unidades/idiomas desconhecidos, rotas repetidas ou campos compartilhados entre unidades são inválidos.
5. Nenhuma reserva, link, valor ou conta é recalculado por esta camada. Preservam-se autoria da Maya, IDs duráveis, receipts e fence contra reenvio de efeito incerto, inclusive após restart.

## Configuração e compatibilidade

O mapa acima é o default explícito de `V2Settings`. Pode ser configurado integralmente com `V2_MANYCHAT_PAYMENT_ROUTES_JSON`, uma lista com quatro objetos:

```json
[{"business_unit":"hostel","customer_language":"pt-BR","link_field_id":14385975,"description_field_id":14389398,"flow_ns":"content20260317150732_004280"},
 {"business_unit":"hostel","customer_language":"en","link_field_id":14385975,"description_field_id":14389398,"flow_ns":"content20260317175037_863148"},
 {"business_unit":"agency","customer_language":"pt-BR","link_field_id":14385973,"description_field_id":14389400,"flow_ns":"content20260316210707_801647"},
 {"business_unit":"agency","customer_language":"en","link_field_id":14385973,"description_field_id":14389400,"flow_ns":"content20260317181131_597245"}]
```

`V2_MANYCHAT_PAYMENT_FLOW_NS`, `V2_MANYCHAT_PAYMENT_LINK_FIELD_ID` e `V2_MANYCHAT_PAYMENT_DESCRIPTION_FIELD_ID` continuam parseáveis para compatibilidade de configuração/rollback, mas **não são usados no envio**. Alterar esses valores antigos não pode substituir o mapa de quatro rotas.

Não há migração nem regravação de SQLite. Linhas já aceitas não são reenviadas. Linhas incertas continuam fechadas pelo fence existente. Uma linha antiga sem proveniência autenticável não recebe roteamento presumido.

## Validação antes de ativar

- Rodar `tests/test_v2_manychat_account_routing.py`, testes de flow delivery/completion e composição, e a suíte integral com ambiente isolado.
- Revalidar runtime authority antes de qualquer promoção, promovendo somente o contato isolado quando autorizado.
- No WhatsApp, conferir cada CTA recebido e seu destino final contra a URL Stripe exata, valor e conta da obrigação — não apenas HTTP 200 do ManyChat.
- Não promover GA enquanto a jornada integral tiver bloqueios independentes, incluindo a apresentação do estado Bókun `RESERVED`.
