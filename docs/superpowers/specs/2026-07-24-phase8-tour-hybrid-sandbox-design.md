# Phase 8 — passeio e conversa híbrida no sandbox read-only

## Objetivo

Qualificar, com `openai-codex / gpt-5.6-luna`, duas conversas naturais e privadas:

1. passeio Buracão em 2026-08-05 para 2 adultos;
2. hospedagem de 2026-08-05 a 2026-08-06 mais Buracão em 2026-08-05 para os mesmos 2 adultos.

O teste deve usar reads reais, produzir respostas públicas fundamentadas e manter reserva, cobrança, pagamento e delivery mecanicamente impossíveis.

## Não objetivos

- Não enviar mensagem ManyChat/WhatsApp.
- Não criar carrinho, reserva, cobrança ou link de pagamento.
- Não alterar o runtime legado `/home/ubuntu/chapada-leads-hermes`.
- Não autorizar `controlled_write`, deploy, merge ou rollout.
- Não resolver passeio por nome dentro do provider.

## Arquitetura

### Modelo

O `HermesDockerModel` continua com o default central `gpt-5.6-luna`. O processo real deve ser instanciado antes da inferência com zero nomes de ferramenta e zero objetos de ferramenta.

### Catálogo e seleção

O prompt privado recebe um catálogo interno fechado contendo o mapeamento semântico de Buracão para `product:buracao`. O modelo pode selecionar apenas esse ID canônico. O ID nunca aparece em resposta pública nem na observação pública.

O provider Bókun recebe exclusivamente:

```text
product_id=product:buracao
activity_date=2026-08-05
participants=2
```

Nenhuma resolução por nome ocorre no adapter ou no provider child.

### Protocolo de reads

O envelope `phase8-sandbox-model-response-v1` passa a aceitar dois tipos fechados:

- `lodging_availability`: `check_in`, `check_out`, `adults`, `children`;
- `activity_availability`: `product_id`, `activity_date`, `participants`.

Regras:

- no máximo dois requests em uma resposta;
- kinds não podem se repetir;
- `product_id` deve obedecer à gramática `product:<slug>`;
- uma resposta que solicita reads não pode propor efeitos;
- o pai valida todos os requests antes de chamar qualquer provider;
- os reads são executados fora de transação SQLite;
- o segundo model call recebe um array privado `READ_OBSERVATIONS` com kind, hash e DTO público;
- o segundo model call não pode solicitar outro read;
- qualquer erro invalida o turno inteiro e não persiste meia-conversa.

### Provider child V2

Um child fixo, sem shell, é executado no container do worker V2 read-only. Ele:

1. confirma `V2_RUNTIME_MODE=dark_read_only`;
2. confirma todos os quatro gates de efeito falsos;
3. importa apenas os transports V2 de read;
4. recebe um request canônico por stdin;
5. chama Cloudbeds ou Bókun conforme o kind já validado;
6. remove IDs e payloads privados;
7. emite um único DTO canônico após marker binário.

O child não importa stores, outboxes, workers de efeito ou transports de write.

### Observações públicas

`LodgingAvailabilityObservation` permanece compatível.

`ActivityAvailabilityObservation` contém somente:

- status;
- data;
- participantes;
- nome público do produto;
- disponibilidade confirmada;
- preço confirmado;
- total e moeda quando confiáveis;
- resumo público;
- `raw_provider_payload_returned=false`.

A observação não contém ID canônico, ID Bókun, rate, availability ID ou payload bruto.

### Journal

O journal privado passa a permitir várias observações no mesmo ordinal, com chave `(session_id, ordinal, kind)`. A inicialização migra com segurança a tabela anterior de chave `(session_id, ordinal)` sem modificar o conteúdo histórico.

`SandboxTurnResult` expõe `read_observations`; a propriedade singular existente permanece compatível para turnos com uma observação.

## Cenários

### A — passeio

1. Lead: “Quero conhecer o Buracão.”
2. Maya pede data e participantes.
3. Lead: “5 de agosto de 2026, para 2 adultos.”
4. Maya solicita `activity_availability` com o ID interno e responde usando somente a observação Bókun.
5. Lead pede reserva e link pelo WhatsApp.
6. Maya não afirma execução; propostas ficam bloqueadas.

### B — híbrido

1. Lead: “Quero hospedagem e também conhecer o Buracão.”
2. Maya pede datas e participantes.
3. Lead informa hospedagem 05–06/08/2026, Buracão em 05/08/2026, 2 adultos, 0 crianças.
4. Maya solicita exatamente um read Cloudbeds e um Bókun.
5. Maya combina somente valores públicos das duas observações.
6. Lead pede reserva de ambos e link de pagamento.
7. As propostas ficam bloqueadas, sem execução.

## Critérios de sucesso

- Modelo efetivo `gpt-5.6-luna`, sem override pontual.
- Zero ferramentas no processo real.
- Bókun consultado por `product:buracao`, nunca por nome.
- Passeio com observação real e resposta semanticamente coerente.
- Híbrido com duas observações reais no mesmo turno e resposta combinada coerente.
- Nenhum ID interno em respostas públicas.
- Todos os pedidos de reserva/pagamento/delivery persistidos somente como bloqueados.
- Todas as tabelas produtivas de comando, workflow, outbox e pagamento permanecem em zero.
- Evidência sanitizada em diretório privado fora do Git, com checksums.
- Testes focados, regressão do sandbox, `py_compile`, Ruff e `git diff --check` verdes.

## Próximo gate

Se ambos os smokes passarem, o único avanço automático é declarar e preparar o **gate de conversa humana privada** usando esse mesmo runner. O agente deve parar antes de simular a conversa de Carlos, antes de writes e antes de deploy/rollout. Provider write continua exigindo autorização separada e específica.
