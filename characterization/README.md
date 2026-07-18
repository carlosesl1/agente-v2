# Corpus de caracterização da Fase 1

Este diretório contém witnesses causais sintéticos para as classes F01–F22.
Ele não contém uma cópia do legado e não executa a Maya, Hermes, providers,
Docker, banco ou rede.

## O que “reproduced” significa

`reproduced` significa que o mecanismo causal pode ser demonstrado
mecanicamente pelo harness a partir de sequência, configuração, tempo,
provenance ou outcome sanitizado. Não significa que o binário live foi
executado nesta fase.

`contract_characterized` significa que a falha depende de uma fronteira ou
ambiente que a Fase 1 deliberadamente não executa. O cenário registra o
contrato inseguro, suas fontes no legado e a violação que as próximas fases
devem impedir.

## Entrada obrigatória

Todo cenário começa com:

```text
initial_state = {}
+ fixture ManyChat sintética
+ snapshot de provider sintético opcional
+ relógio UTC determinístico
+ trace causal sanitizado
```

O harness rejeita estado inicial contendo seleção, IDs técnicos, fases,
assinaturas, confirmação, comando ou outcome.

## Estrutura

```text
characterization/
├── harness.py
├── schema/scenario.schema.json
├── fixtures/manychat/
├── fixtures/provider/
└── incidents/
```

Os eventos do trace não são respostas reais. São tipos sem PII usados para
provar propriedades como:

- fase perdida em projeção;
- confirmação repetida;
- alias sem guard;
- lookup vencido;
- concorrência e webhook duplicado;
- label Unicode usada como identidade;
- orçamento temporal impossível;
- outcome composto não monotônico;
- handoff público acoplado a e-mail opcional;
- drift de artefato;
- false green por preseed;
- oito janelas explícitas de crash.

## Execução

```bash
python3 -m characterization.harness
python3 -m unittest discover -s tests -v
```

## Segurança

Todos os cenários declaram estas capacidades como `false`:

```text
network
provider_reads
provider_writes
message_delivery
database
```

O validador da fase também examina imports, paths, secrets, PII, cobertura e
hashes antes do closeout.
