# Fase 3 — Consultas e seleção por OfferSnapshot

## Status

`remediação de revisão tardia`

Aberta em `2026-07-18T23:24:58Z`, a partir do commit-base
`e318a2f1cad6fbeda3db11a0368f7b762ae84cdf`.

Implementação publicada em `2026-07-19T00:40:28Z` no commit
`33b1975dd660963b242f961721d8117404654893`.

O closeout `e19d0e571ec4f19f6f3979a88b9ddb559a4994f5` foi reaberto quando um
parecer independente tardio reproduziu colisões de target/provenance. Ele não é
mais tratado como encerramento definitivo.

## Objetivo

Remover labels e payloads produzidos pela LLM da identidade técnica de
consultas e seleção. Adapters read-only Cloudbeds/Bókun devem produzir
`LookupEvidence` e `OfferSnapshot` canônicos, selecionáveis somente por
`offer_id` opaco e evidência fresca.

## Design aprovado

- [spec da Fase 3](../../superpowers/specs/2026-07-18-phase-3-lookup-adapters-design.md)

Boundary:

```text
request tipado
→ adapter read-only
→ transporte obrigatório injetado
→ response sanitizada
→ normalizador estrito
→ LookupResult
```

Não existe transporte de rede padrão nesta fase.

## Owners

- request DTO: forma e IDs técnicos internos necessários ao lookup;
- adapter: request provider + normalização de schema;
- identity: `offer_id`, `lookup_id` e hashes canônicos;
- provenance: TTL, request fingerprints e response hashes;
- selection: frescor, status positivo e match exato único;
- fixture transport: prova boundary sem rede.

## Escopo autorizado

- package `reservation_lookup` sem I/O real;
- Cloudbeds GET availability + rate plans;
- Bókun GET metadata + availability;
- transport `Protocol` injetado;
- fixtures HTTP sintéticas e sanitizadas;
- IDs opacos e deterministicamente derivados;
- revalidação por mudança executável;
- testes RED, contract, metamórficos, property e mutation;
- validador/CI/evidências da Fase 3.

## Fora do escopo

- editar ou importar o legado;
- provider/network live, auth ou credenciais;
- writes Cloudbeds/Bókun;
- resolver nome público para product ID;
- categorias Bókun além de adults/children;
- renderer, confirmação, persistência, worker, ledger, outbox;
- runner/plugin/executor, deploy ou rollout;
- iniciar a Fase 4.

## Invariantes

1. somente GET pode atravessar o transport;
2. nenhum adapter tem transporte real/default;
3. product/property IDs vêm de contexto interno, nunca de label do lead;
4. `offer_id` exclui label e provenance;
5. todo campo técnico/econômico executável participa da identidade;
6. status positivo exige offers completos e bookable;
7. zero/múltiplos matches, TTL vencido e status não positivo falham fechados;
8. label-only preserva seleção; mudança executável invalida;
9. falha HTTP/schema/transport produz `UNCERTAIN` e zero offers;
10. raw payload, auth, headers e PII não saem no resultado;
11. fixtures são exclusivamente sintéticas;
12. legado permanece somente leitura.

## Entregáveis

- [x] spec e plano detalhado;
- [x] tipos e transport protocol;
- [x] identidade opaca canônica;
- [x] adapter Cloudbeds;
- [x] adapter Bókun;
- [x] seleção e revalidação fail-closed;
- [x] fixtures sanitizadas e manifest;
- [x] tests RED/contract/metamórficos/property;
- [x] mutation testing reproduzível;
- [x] source map, riscos, evidências e hashes;
- [x] validador local e CI;
- [x] revisão adversarial;
- [x] commits de entrada e implementação remotos verificados;
- [x] closeout preparado para publicação e verificação remota.

## Gate

1. paths/query/método e ordem dos requests são exatos;
2. ID/label divergente não autoriza;
3. label tipograficamente equivalente não quebra seleção;
4. provider ref/data/hora/party/preço/moeda/availability alterados invalidam;
5. zero/múltiplos matches falham fechados;
6. TTL/provenance/status são validados;
7. malformed/partial/provider error não gera offer autorizável;
8. property workload e mutantes obrigatórios passam;
9. package não importa rede, filesystem write, env, provider SDK ou legado;
10. validadores 0–3, scans, compileall, diff, hashes e CI passam;
11. `HEAD == origin/main == remote` no SHA final.

## Baseline legado somente leitura

- HEAD: `57408d8b2040399bc25ee7957505208079458884`;
- status entries preexistentes: `80`;
- status SHA-256: `77c02eb09d415e01f45515ccacf9bc7b93f34d1d8a66aafc0af905d8734c940b`.

## Rollback

Reverter somente os commits da Fase 3 no repositório novo. Não há ação live.

## Evidência de fechamento

- suíte integral após remediação: `103` testes, `OK`;
- property gate: `50.000` casos, seed `20260718`;
- obrigações positivas: `50.000` autorizações e `50.000` equivalências de
  label;
- baselines produzidos por adapters in-memory: `18.750` Cloudbeds e `31.250`
  Bókun;
- rejeições exercidas: `50.000` mutações executáveis, `50.000` TTLs no limite
  exclusivo, `50.000` zero-match, `50.000` multiple-match, `50.000`
  cross-target, `50.000` lookup rebindings, `50.000` response swaps e `50.000`
  totals zero;
- falsos positivos, invalidações perdidas e exceções inesperadas: `0`;
- mutation gate: `19/19` mutantes mortos por runner reproduzível;
- fixtures: `8`, quatro Cloudbeds e quatro Bókun, todas sintéticas;
- package: `8` módulos Python, nenhum import de rede/env/filesystem/provider;
- hashes: pendentes de regeneração final após remediação;
- validadores das Fases 0–3: `ok`, zero failures;
- legado somente leitura: HEAD/status/status-hash inalterados;
- nenhuma rede, auth, provider, write, banco, fila, Docker ou deploy executado.

GitHub Actions do commit de implementação:

- `phase-0-validation`: run `29667240880`, `success`;
- `phase-1-characterization`: run `29667240879`, `success`;
- `phase-2-domain`: run `29667240862`, `success`;
- `phase-3-lookups`: run `29667240868`, `success`.

Uma das três frentes externas entregou parecer tardio após o closeout; duas
expiraram sem summary. Os achados materiais foram reproduzidos em RED e estão
documentados em A10–A15. O parecer útil integra a evidência; os timeouts não.

## Decisão de avanço

A Fase 4 voltou a ficar bloqueada até novo commit de remediação, CI e closeout
remoto da Fase 3.
