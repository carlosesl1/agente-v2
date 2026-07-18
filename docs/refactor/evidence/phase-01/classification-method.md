# Método de classificação da Fase 1

## Objetivo

Separar prova mecânica de mecanismo causal de uma alegação indevida de que o
runtime live foi reexecutado. A Fase 1 não importa nem executa o legado.

## Unidades de evidência

Cada caso contém:

1. `source_refs` relativos ao repositório legado;
2. payload ManyChat totalmente sintético;
3. `initial_state = {}`;
4. snapshot de provider sintético quando necessário;
5. relógio UTC fixo;
6. trace causal sanitizado;
7. violações derivadas pelo harness;
8. capabilities externas explicitamente fechadas.

As referências de código explicam de onde veio o contrato. O trace não é um log
real nem uma transcrição de cliente.

## Classificações

### `reproduced`

O mecanismo causal é demonstrado deterministicamente pelo harness sem depender
de rede, modelo ou provider. Exemplos:

- campo obrigatório removido em projeção;
- resumo aceito seguido de outro pedido de confirmação;
- lookup usado após TTL;
- assinatura que omite campo econômico;
- duas entregas do mesmo webhook produzindo dois dispatches;
- `n°` e `nº` quebrando seleção por label;
- desigualdade temporal `120 < 300 + 1 + 1`;
- parent outcome incompatível com os leaves;
- e-mail opcional bloqueando handoff público;
- teste histórico preseeding a condição que dizia construir.

A classificação prova o mecanismo, não afirma que o binário live foi executado.

### `contract_characterized`

A falha depende de ambiente, fronteira externa, imagem, processo ou política de
crash que esta fase não pode executar com segurança. O caso registra:

- source owner;
- contrato inseguro;
- witness mínimo;
- violação que as próximas fases precisam impedir.

Inclui seleção livre de provider pela LLM, bootstrap implícito, drift de runtime,
fake otimista, capacity/readiness, identidade de release e as oito janelas de
crash.

### `not_reproducible`

Seria usado apenas se a fonte e a evidência histórica não permitissem construir
nem witness nem contrato verificável. Nenhuma classe F01–F22 ficou nesse estado.

## Prevenção de false green

O validador rejeita:

- qualquer estado inicial diferente de `{}`;
- preseed de seleção, ID, fase, assinatura, confirmação, comando ou outcome;
- fixture sem `synthetic=true`;
- PII, e-mail, telefone brasileiro, token ou private key;
- imports de rede, subprocesso, banco ou HTTP no harness;
- cenário sem fonte relativa e símbolos;
- cobertura diferente de F01–F22;
- ausência de qualquer uma das oito fault boundaries;
- relatório gerado divergente do corpus;
- arquivo obrigatório não staged/tracked.

## Limites

O corpus não valida ainda:

- qualidade de interpretação da LLM;
- compatibilidade do reducer novo;
- providers reais;
- persistência/ledger/outbox reais;
- ManyChat ingress live;
- artefato OCI ou rollout.

Esses gates pertencem às fases posteriores e permanecem bloqueados.
