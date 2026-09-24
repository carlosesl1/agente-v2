# Renovação por componente e qualificação do recibo Cloudbeds

## Autorização e escopo

Carlos aprovou neste chat a correção no fluxo existente, por componente, com consulta atual e nova confirmação. Base autenticada TEST: `ab58c2ff3823eb7963840294b4eeb77777f5289d`. Worktree existente: `/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`, branch `fix/v2-manychat-account-routing-727d3625`.

Somente fonte isolada, testes e qualificação sem efeitos externos nesta etapa. Não abrir janelas, criar reservas/cobranças, estornar, escrever nos bancos ativos ou promover GA/Ops. Não alterar V3/legado. Sem subagentes ou regras lexicais. Nova operação financeira pelo WhatsApp depende de autorização posterior.

## Desenho aprovado

Maya interpreta o pedido e o serviço desejado. O controlador não interpreta prosa: valida a proposta tipada e consulta a reserva anterior. Uma reserva anterior com efeito confirmado mas estado atual terminal (expirada/cancelada) pode dar lugar a uma nova proposta **do componente solicitado**, não a repetição do comando anterior. A proposta usa disponibilidade fresca e exige novo resumo entregue e nova confirmação do cliente antes de produzir novo comando.

A hospedagem ativa não deve ser recriada porque o passeio expirou. Resultado incerto, criação ainda em curso, consulta indisponível/obsoleta ou evidência financeira pendente de reconciliação não são permissão para duplicar. Reenviar mensagem, reiniciar o worker ou repetir confirmação não pode emitir outro comando. Uma leitura de saldo agregado não autentica a origem de um pagamento.

Preservar comandos, ledgers, ofertas financeiras, histórico e fences anteriores. O novo workflow usa as identidades já existentes por evento/draft; nenhuma exclusão ou reset financeiro. O resolver atual já projeta componentes históricos por lead. Não adicionar banco, fila, agente, revisor semântico ou framework de pedidos.

## Owners e fluxo

- `v2_application/active_execution.py`: conferir os componentes duráveis, seus estados atuais e o escopo da proposta antes de permitir progressão. Restrições de comandos ainda em curso continuam válidas.
- `v2_application/turn_executor.py`: usar o contexto factual existente nas decisões antes/depois das leituras e antes do commit. Rejeição volta à Maya pelo caminho existente, sem substituir sua resposta.
- `v2_application/conversation.py`: permitir o novo draft/resumo com disponibilidade fresca apenas quando a progressão foi autenticada; confirmação do workflow antigo nunca cria reserva.
- `v2_host/stripe_settlement.py`: preservar o sucesso documentado de `postPayment` (HTTP 200, `success=true`, IDs de pagamento/transação) e a evidência diagnóstica das respostas não reconhecidas. Não afrouxar por suposição sobre o incidente antigo.
- Stores/ledgers/outboxes existentes: novas identidades e histórico intacto; não reutilizar confirmação, pagamento ou baixa de obrigação antiga.

## Baixa Cloudbeds

A resposta original do incidente não foi retida; sua causa exata permanece desconhecida. A referência oficial atual continua documentando os campos exigidos. Exercitar o transporte real com endpoints controlados, persistência SQLite, projeção de settlement e contexto da Maya. Cobrir sucesso, recusa, corpo inválido/incompleto, timeout após aceitação, reinício e evento duplicado. Corrigir defeito demonstrado por teste causal; não apresentar fixture como resposta real da Cloudbeds.

## Critérios de aceite

1. Reprodução vermelha do bloqueio de renovação; verde só após correção mínima.
2. Passeio terminal pode chegar a **novo resumo**, sem recriar hospedagem ativa. Novo comando apenas após nova confirmação e leituras frescas.
3. Estado atual indisponível, ativo ou incerto, evidência financeira conflitante e confirmação antiga não duplicam efeitos.
4. Replay/reinício preserva histórico e não repete reservas, pagamentos ou baixas.
5. Receipt Cloudbeds completo percorre transporte→ledger→contexto; recibo incompleto permanece incerto e diagnosticável, com um único POST controlado.
6. Regressões focais, suíte completa hermética e fronteiras passam. Testes sintéticos e eventual modelo real sem efeitos são explicitamente distintos de E2E financeiro/WhatsApp.
7. Autoridade, GA/Ops, gates fechados e bancos ativos inalterados ao final. Entregar pendências reais para o teste financeiro autorizado.
