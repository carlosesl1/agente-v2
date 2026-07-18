# Plano de testes RED da Fase 2

## Owner por contrato

| Contrato | Owner | Teste RED |
|---|---|---|
| comando somente após resumo + aceite posterior da mesma versão | reducer | `test_valid_flow_emits_exactly_one_command_after_posterior_confirmation` |
| resumo sem confirmação não autoriza | reducer | `test_summary_without_confirmation_emits_no_command` |
| confirmação precoce, simultânea ou divergente falha fechada | reducer | testes `confirmation_*` |
| duplicata ou segundo aceite não reemite comando | reducer/idempotência | `test_duplicate_and_second_confirmation_do_not_reemit_command` |
| lookup vencido não oferece | evidência temporal | `test_stale_lookup_cannot_offer_or_select` |
| seleção somente por `offer_id` | identidade canônica | `test_offer_is_chosen_only_by_opaque_offer_id` |
| evento fora de ordem não produz comando | reducer | `test_out_of_order_event_is_rejected_without_command` |
| `called_unknown` é monotônico | outcome | `test_called_unknown_is_monotonic_and_requires_manual_review` |
| label/ordem não altera assinatura | assinatura | `test_public_label_and_input_order_do_not_change_signature` |
| toda mutação executável altera assinatura | assinatura | `test_every_execution_relevant_mutation_changes_signature` |
| estados/eventos/comandos têm round-trip | serializer | `test_*_round_trips` |
| versão/tag/campo desconhecido falha fechado | serializer | testes `unknown_*` |
| toda combinação estado/evento tem política | reducer/table | `test_transition_matrix_is_total` |
| sequências arbitrárias preservam invariantes | property runner | `test_property_smoke` + execução de 100 mil |

## Critério RED

Antes da implementação, os testes devem falhar porque o package
`reservation_domain` ainda não existe. A falha esperada é de contrato ausente,
não de fixture, ambiente ou dependência externa.
