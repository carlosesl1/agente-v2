# Nova contratação independente após baixa não enviada

Autorização: operador aprovou prosseguir neste contato, preservando compra/pagamento anteriores; não autoriza reembolso, transferência ou replay.

## Contrato mínimo
A gramática já distingue seleção (consulta fresh + resumo novo) de confirmação do resumo. Reutilizar esse contrato, não inventar flag, identidade, frase-gatilho ou reset. Reserva anterior deve estar terminal e sem pagamento no provedor em GET autenticado recente. Em seu histórico financeiro, permitir além dos estados sem evidência apenas o resultado terminal `not_dispatched` com status canônico `retryable`. Isso não significa que a captura foi devolvida: continua visível e separada. Nenhuma captura sem resultado terminal, baixa incerta, parcial ou confirmada autoriza progressão.

Na confirmação, revalidar o histórico corrente e o novo resumo/consentimento. Novo workflow/draft/comando/idempotency key e obrigação não reaproveitam os antigos. Não modificar contratos de baixa, ledger, idempotência, webhook ou handoff.

## Encaminhamento e operação
A fila humana permanece bloqueante. No ensaio autorizado, encerrar somente o incidente atual identificado via evento nativo, qualificando em cópia e replay NOOP; o projetor não pode recriar a mesma fila. Não cancelar incidentes sucessivos para obter liberação. Finanças e evidências anteriores permanecem intactas.

## Aceitação
RED causal com captura + not_dispatched retida; GREEN com resumo sem efeitos e confirmação distinta/replay estável. Negativos para queued/capture-only/unknown/partial/settled, GET stale/inválido/reativação e serviço irmão. Prova de contexto a partir de stores financeiros reais, não somente contexto sintetizado. Prova de package→nova atividade após restart com captura antiga retida. Suíte completa e imagem antes de TEST. GA/Ops/V3 fora de escopo. No ensaio real, manter worker financeiro ativo durante checkout humano e observar baixa Bókun + canal; não chamar E2E concluído antes disso.
