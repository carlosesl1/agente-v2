# Fase 1 — Caracterização e corpus de incidentes

## Status

`concluída`

Aberta em `2026-07-18T20:26:56Z`, a partir do commit-base
`664db7eb9717830eec2604e2c0811d26d08f048d`.

Commit de entrada: `cd49452a9e2fac544241f22851586828a4d21bd8`.
Commit técnico validado: `faadb8a0fcffc5f244bda744c1c6b03cd06c4768`.

## Objetivo

Transformar as classes históricas F01–F22 em cenários reproduzíveis e
sanitizados, executados desde a entrada ManyChat e estado vazio, antes de
implementar o domínio novo.

A fase caracteriza o comportamento observado ou a propriedade arquitetural
ausente. Ela não corrige o legado e não antecipa o `ReservationKernel` da
Fase 2.

## Escopo autorizado

- observar `/home/ubuntu/chapada-leads-hermes` em modo somente leitura;
- extrair shapes e contratos, nunca payloads reais;
- criar fixtures totalmente sintéticas e sanitizadas;
- criar harness determinístico, sem rede, provider, Hermes, Docker ou banco;
- criar testes de replay, concorrência, tempo e fault boundaries;
- registrar quais incidentes são reproduzidos, representados como contrato
  de falha ou não reproduzíveis;
- publicar evidências, hashes, comandos e resultados no `agente-v2`.

## Fora do escopo

- editar `/home/ubuntu/chapada-leads-hermes`;
- importar código executável do legado para o novo repositório;
- corrigir qualquer incidente;
- criar o reducer/kernel funcional da Fase 2;
- usar mensagens, telefones, e-mails, subscriber IDs ou payloads reais;
- abrir rede, executar provider read/write, enviar ManyChat/WhatsApp;
- alterar container, profile, env, Redis, Supabase ou deploy;
- iniciar a Fase 2.

## Contrato do harness

Cada replay começa com:

```text
payload ManyChat sintético e validado
+ estado vazio
+ provider snapshot sintético e sanitizado
+ relógio determinístico
+ plano explícito de falhas/duplicatas
```

É proibido iniciar o cenário com qualquer condição que deveria ser construída
pelo fluxo, incluindo seleção canônica, `offer_id`, fase de confirmação,
assinatura comercial, comando ou resultado de provider.

O resultado do replay deve registrar, sem texto/PII:

- eventos de entrada normalizados;
- estados e transições observados;
- resumo apresentado e sua versão;
- decisões de confirmação;
- comandos/dispatches/outcomes;
- handoff e outbox;
- violações de invariantes;
- fault point e contadores de efeitos.

## Entregáveis

- [x] schema versionado dos cenários;
- [x] fixtures sanitizadas de ManyChat e provider reads;
- [x] harness determinístico desde estado vazio;
- [x] cenário aplicável para F01–F22;
- [x] casos obrigatórios `n°/nº`, confirmação dupla, estado ausente,
      lookup vencido, configuração 120/300, handoff sem e-mail, outcome
      composto, webhook duplicado e crash em fronteiras;
- [x] testes temporais e concorrentes;
- [x] teste que proíbe preseed de estado canônico;
- [x] relatório de cobertura dos incidentes;
- [x] baseline de comportamento aceito/não aceito;
- [x] validador local e CI da Fase 1;
- [x] evidências e hashes SHA-256;
- [x] revisão de riscos;
- [x] commit de entrada e commit técnico enviados, CI e remoto verificados;
      o commit de closeout é verificado imediatamente após a publicação.

## Resultado

O corpus contém:

```text
fixtures ManyChat sintéticas: 4
fixtures provider sintéticas: 4
cenários: 30
classes F01–F22 cobertas: 22
witnesses reproduced: 16
witnesses contract_characterized: 14
not_reproducible: 0
violações derivadas: 37
fault boundaries: 8/8
```

O harness exige que o primeiro trace comece no primeiro payload da fixture,
que todos os demais `inbound` existam no payload e que `initial_state` seja
exatamente `{}`. Também rejeita preseed, fixture não sintética, trace kind
desconhecido, PII/segredo e capability externa.

## Evidências

Diretório:

```text
docs/refactor/evidence/phase-01/
```

Principais artefatos:

- `incident-coverage.json`;
- `source-map.json`;
- `corpus-manifest.json`, com hashes de 40 arquivos;
- `behavior-baseline.md`;
- `classification-method.md`;
- `source-readonly-verification.json`;
- `validation-result.json`;
- `SHA256SUMS`, com 14 artefatos externos ao manifesto.

## Validação executada

```text
replay do corpus: 30/30
unittest: 13/13
F01–F22: 22/22
fault boundaries: 8/8
fixtures: 8/8
source paths: 13
source symbols: 58
source symbol failures: 0
phase0 regression validator: ok
phase1 validator local: ok
phase1 validator CI-mode: ok
compileall: ok
git diff --check: ok
secret/PII scan: ok
```

GitHub Actions do commit técnico:

- [phase-0-validation — success](https://github.com/carlosesl1/agente-v2/actions/runs/29660723371);
- [phase-1-characterization — success](https://github.com/carlosesl1/agente-v2/actions/runs/29660723370).

## Fonte legada e segurança

O legado permaneceu em modo somente leitura:

```text
HEAD inicial/final: 57408d8b2040399bc25ee7957505208079458884
status entries inicial/final: 80
status canônico SHA-256 inicial/final:
15bfc6b0d3cb7248481027e5f736396b76206b0dd040358190f5545a7036a64a
```

Não houve teste no repositório legado, provider read/write, mensagem,
alteração de contato, container, profile, env ou deploy.

## Riscos revisados

- R17 registra o risco de confundir witness com E2E e é mitigado pela
  classificação explícita;
- R18 monitora drift do working tree legado e foi verificado por HEAD/status;
- R19 permanece aberto: traces sintéticos precisarão ser confirmados nos
  boundaries reais das fases posteriores.

## Gate de aceite

1. Cada F01–F22 está `reproduced`, `contract_characterized` ou possui
   justificativa mecânica para `not_reproducible`.
2. Cenários iniciam em payload sintético + estado vazio; o validador rejeita
   preseed de identidade, confirmação ou execução.
3. O harness não importa nem executa o legado e não possui capacidade de rede
   ou side effect externo.
4. Casos temporais, concorrentes, duplicados e fault boundaries têm assertions
   explícitas, não apenas snapshots aprovados.
5. Nenhum segredo, PII, mensagem real ou ID real existe no repositório.
6. Testes, compileall, diff check, scanner, hashes e CI passam após a última
   alteração.
7. `HEAD == origin/main` depois de push e fetch.

## Rollback

Esta fase adiciona somente testes, fixtures, documentação e evidências no novo
repositório. O rollback é a reversão dos commits da Fase 1; não existe ação no
runtime live.

## Decisão de avanço

A Fase 1 está concluída. A Fase 2 torna-se a próxima fase elegível, mas não foi
iniciada e exige um novo ciclo explícito, preservando a regra de uma fase por vez.