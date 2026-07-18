# Fase 1 — Caracterização e corpus de incidentes

## Status

`em execução`

Aberta em `2026-07-18T20:26:56Z`, a partir do commit-base
`664db7eb9717830eec2604e2c0811d26d08f048d`.

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

- [ ] schema versionado dos cenários;
- [ ] fixtures sanitizadas de ManyChat e provider reads;
- [ ] harness determinístico desde estado vazio;
- [ ] cenário aplicável para F01–F22;
- [ ] casos obrigatórios `n°/nº`, confirmação dupla, estado ausente,
      lookup vencido, configuração 120/300, handoff sem e-mail, outcome
      composto, webhook duplicado e crash em fronteiras;
- [ ] testes temporais e concorrentes;
- [ ] teste que proíbe preseed de estado canônico;
- [ ] relatório de cobertura dos incidentes;
- [ ] baseline de comportamento aceito/não aceito;
- [ ] validador local e CI da Fase 1;
- [ ] evidências e hashes SHA-256;
- [ ] revisão de riscos;
- [ ] commit de entrada e commit de saída enviados e verificados no remoto.

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

Pendente. A Fase 2 permanece bloqueada até o closeout formal desta fase.