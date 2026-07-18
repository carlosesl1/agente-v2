# Baseline comportamental da Fase 1

## Contrato aceito para a refatoração

Para cada assunto comercial imutável:

```text
payload sintético/real normalizado
→ estado canônico construído pelo runtime
→ offer vinculada a evidência fresca
→ uma versão do draft
→ um resumo apresentado
→ uma confirmação natural posterior
→ no máximo um comando durável
→ no máximo um dispatch provider
→ outcome monotônico
→ outbox independente
```

## Comportamentos aceitos

1. O replay começa sem estado comercial.
2. Label pública serve somente para apresentação; identidade é técnica e opaca.
3. Zero ou múltiplos matches falham fechados.
4. Lookup vencido não autoriza seleção nem write.
5. Resumo não cria command, claim ou provider call.
6. Confirmação aceita exatamente a versão apresentada.
7. Alteração econômica cria nova versão e invalida a anterior.
8. Webhook/turno duplicado não cria segundo command ou dispatch.
9. A operação técnica é derivada do estado; a LLM não escolhe provider.
10. Outcome composto preserva `called_unknown` e nunca inventa execução.
11. Falha da mensagem não repete efeito comercial.
12. E-mail interno opcional não bloqueia a resposta pública de handoff.
13. Promessa de continuação exige workflow/outbox durável.
14. Runtime, commit, profile e imagem são atestados como um artefato.
15. Reads, writes e delivery possuem controles ortogonais.

## Baseline não aceito por incidente

| ID | Classificação principal | Comportamento não aceito caracterizado |
|---|---|---|
| F01 | reproduced | campo de autorização some na projeção runner/plugin |
| F02 | reproduced | mesmo draft exige duas confirmações |
| F03 | reproduced | alias legado mantém write sem o guard canônico |
| F04 | reproduced | assinatura omite pagamento ou item econômico |
| F05 | reproduced | lookup positivo vencido continua autorizando |
| F06 | reproduced | webhook duplicado e owners concorrentes geram dois dispatches |
| F07 | reproduced | metadata privada aninhada atravessa filtro raso |
| F08 | reproduced | fase é armada antes do resumo apresentado |
| F09 | contract_characterized | modelo escolhe provider que o estado já determina |
| F10 | contract_characterized | sessão é inferida por log/filesystem |
| F11 | contract_characterized | canary e runtime não atestam o mesmo artefato |
| F12 | reproduced | `dry_run` acopla read real e write |
| F13 | contract_characterized | fake aceita opção que snapshot do provider rejeita |
| F14 | reproduced | resposta promete continuação sem trabalho durável |
| F15 | reproduced | `n°`/`nº` altera identidade por label |
| F16 | reproduced | configuração torna write impossível por construção |
| F17 | reproduced + contract_characterized | outcome composto achata certeza e crashes ficam sem política segura |
| F18 | reproduced | e-mail opcional bloqueia handoff público |
| F19 | contract_characterized | capacidade local derruba readiness diretamente |
| F20 | reproduced | segunda tool no turno é consumida por budgets paralelos |
| F21 | contract_characterized | working tree, commit e imagem não têm identidade única |
| F22 | reproduced | teste injeta seleção/ID/lookup que dizia construir |

## Fault boundaries obrigatórias

O corpus contém witnesses explícitos para:

1. antes de persistir o evento;
2. depois do evento e antes do comando;
3. depois do comando e antes do claim;
4. depois do claim e antes do socket;
5. depois do socket com resposta perdida;
6. depois do provider e antes do outcome;
7. depois do outcome e antes da outbox;
8. durante a entrega pública.

A política desejada para as fases futuras é:

```text
provider_calls <= 1
falha de outbox não repete provider
incerteza nunca recebe retry automático
continuação pública nunca precede persistência
```

## Resultado do corpus

- 22 classes cobertas;
- 30 cenários;
- 16 witnesses `reproduced`;
- 14 witnesses `contract_characterized`;
- oito fault boundaries;
- zero cenário com estado inicial preseeded;
- zero capability externa habilitada.

## Decisão

Este baseline é suficiente para orientar a Fase 2, mas não autoriza iniciá-la
antes do closeout, commit remoto e CI da Fase 1. O sistema comercial continua
**NO-GO**.
