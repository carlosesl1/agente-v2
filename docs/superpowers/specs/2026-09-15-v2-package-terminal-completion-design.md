# Correção causal da jornada pacote V2

**Data:** 2026-09-15
**Base imutável:** `bf8efedaec10c269e5df1246924384e5dfcd2d8d`
**Escopo:** Agente V2 somente; V3, legado e Maya Ops ficam fora.

## Problemas autenticados

1. Um pacote assinado contém o perfil completo necessário ao passeio. `split_package_command()` reutiliza esse perfil nos dois filhos, e o preflight Cloudbeds rejeita corretamente `passengers`.
2. No caso real, o titular não era passageiro. O checkout Bókun exigia nascimento e gênero do titular; o perfil tinha gênero, mas não nascimento. O transporte encerrou antes do submit, depois de criar/ler o carrinho, com `called_no_effect`.
3. Os outcomes `not_called`/`called_no_effect` foram persistidos, mas `CompletionProjector` só projeta grupos totalmente confirmados. A boundary permaneceu `execution_queued`, então o turno seguinte recebeu status obsoleto e afirmou processamento.

## Design aprovado

### 1. Subject por componente antes da assinatura filha

O comando pai continua assinando o pacote completo. Ao derivar os filhos:

- hospedagem recebe somente `customer_ref`, nome, e-mail, telefone e país;
- atividade recebe o perfil integral e o manifesto de passageiros;
- cada filho recalcula assinatura, `command_id` e `idempotency_key` sobre seu próprio subject;
- o `customer_ref` permanece idêntico para agrupamento/reconciliação.

Nenhum adapter remove campos depois de assinado.

### 2. Preflight de titular e passageiros para atividade

Para qualquer reserva de atividade:

- o manifesto continua obrigatório para todos os passageiros quando a party tem mais de uma pessoa;
- nascimento e gênero do contato principal também são obrigatórios;
- quando o contato principal coincide canonicamente com um passageiro, esses dois valores podem ser derivados desse mesmo passageiro;
- quando o contato não é passageiro, Maya precisa coletar os dados próprios do titular antes de `select`/resumo;
- jamais copiar nascimento/gênero de outra pessoa.

No transporte Bókun:

- selecionar explicitamente a opção que aceita `RESERVE_FOR_EXTERNAL_PAYMENT`;
- emitir `BookingAnswersDto` somente com os campos documentados;
- mapear o titular por igualdade canônica, não pela posição 1;
- ausência/rejeição antes da reserva continua `called_no_effect`, sem retry.

### 3. Outcome terminal como autoridade operacional

Criar um resolvedor somente leitura sobre o ledger:

- correlaciona os filhos por `draft_id`, versão, customer_ref e serviços esperados;
- retorna `queued`, `executing`, `confirmed`, `failed_before_provider`, `failed_no_effect`, `partial_failure`, `uncertain` ou `manual_review`;
- um status terminal desativa o guard de “em processamento”, sem autorizar nova ação;
- o status entra no request estruturado da Maya e no prompt como fato autenticado.

O `CompletionProjector` também publica uma única mensagem de desfecho para todo grupo terminal:

- sem efeito: não confirmado, nenhum pagamento criado e sem retry;
- parcial: enumera por serviço o que foi confirmado e o que falhou;
- incerto/manual: não afirma presença nem ausência, orienta verificação e proíbe repetição;
- replay usa `release_id` determinístico.

## Invariantes

- uma tentativa por `operation-id`, sem retry cego;
- `called_unknown` nunca vira “não reservou”;
- falha de um componente não vira sucesso do pacote;
- pagamento só nasce para grupo integralmente confirmado;
- nenhuma PII entra no estado público de status;
- nenhuma reserva real será repetida para qualificação sem autorização explícita nova;
- GA só pode mudar após autoridade, suítes, imagem imutável e E2E autorizado passarem.
