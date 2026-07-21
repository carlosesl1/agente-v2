# Fase 8 — Shadow, canary e rollout por digest

## Estado

- Status: **ativa — design/plano publicados; entrada autenticada**.
- Base publicada da Fase 7: `93682024b4867d3e313324339a7060d5351dcd3d`, tree `b779e35c671f3050d056c6ef3c8c0700f5b13f35`.
- Spec aprovada: `0dbc9cb9722762dfc4f24a3ea73bfce974835a84`.
- Plano corrigido publicado: `49b4930d5c5df48eb85cb58c73d5ceded876259a`.
- Branch: `phase8-shadow-canary-rollout`.
- Rollout: **NO-GO**.
- `phase8_started=true`.
- `phase9_started=false`.

A autorização de 2026-07-21 abre a execução controlada da Fase 8 até o gate
conversacional. Ela não autoriza provider write, pagamento, entrega pública fora
do contato de teste, rollout comercial nem início da Fase 9. A Task 1 somente
autentica a entrada; não executa Docker, provider, ManyChat, rede ou capability
live.

## Objetivo

Construir uma única imagem OCI a partir da réplica sanitizada aprovada na Fase
7, autenticar sua identidade e promover os mesmos bytes por dark canary, ingress,
teste conversacional humano, uma canary E2E separadamente autorizada e rollout
gradual.

## Entrada autenticada

`../evidence/phase-08/entry-baseline.json` fixa mecanicamente:

- closeout publicado da Fase 7 em `93682024...` / `b779e35c...`;
- candidato funcional `2c99be11...` / `3a05029f...`;
- snapshot terminal `73904070...` / `7017fcf9...`;
- revisão terminal 3/3 e CI remoto `29804123764`, seis jobs verdes;
- integração pós-merge 762/762, output SHA-256
  `f96f1e28c580db6b2166163576ea91dbc12461093220fe65992c4b94d29da3f1`;
- réplica limpa `183fb41d...` / `e546e9d8...`;
- runtime operacional observado no HEAD `57408d8b...`, tree `67b5fe18...`,
  86 entradas e fingerprints exatos, sem alteração;
- imagem live de rollback
  `sha256:2dc5f71557b82d4d0646ab1dba0b61edfa7d916320047dd03ce8554dbfa50d53`,
  release `57408d8b...` / `v2026.07.13.0545`;
- Python `3.12.13`, SQLite `3.46.1`, zero capability live, rollout `NO-GO` e
  Fase 9 não iniciada.

## Escada de gates

1. **Entry — autenticado nesta Task 1.** Base/spec/plano e fingerprints estão
   fixados; os validators das Fases 0–7 permanecem verdes.
2. **Build — não iniciado.** Exige preflight fail-closed e uma imagem construída
   uma única vez a partir da réplica limpa.
3. **Dark canary — bloqueado pelo build.** Reads reais podem ocorrer somente com
   zero write e zero delivery.
4. **Ingress — bloqueado pelo dark canary.** A mesma imagem deve continuar com
   outbound e writes fechados.
5. **Conversa humana — bloqueada pelo ingress.** Carlos executa o teste; nenhuma
   simulação substitui sua aprovação.
6. **Canary E2E — bloqueada.** Requer autorização posterior que fixe contato,
   workflow, provider, janela, write único e cancelamento.
7. **Rollout — bloqueado.** Reutiliza o mesmo manifest digest OCI; image ID e
   archive hash são evidências suplementares. Avança por estágios com GO explícito.
8. **Closeout — bloqueado.** A Fase 9 permanece fechada até nova decisão.

Artefato futuro ausente significa gate ainda não aberto, não sucesso implícito.
Qualquer identidade divergente, efeito prematuro, PII/segredo ou drift do runtime
é `NO-GO`.

## Limites atuais

Até um gate posterior abrir explicitamente, não executar:

- Docker build/create/start/tag/promote;
- rede, ManyChat, LLM ou entrega pública;
- Cloudbeds/Bókun/Stripe/Wise/provider write ou pagamento;
- Supabase, Redis, PostgreSQL ou estado/sessão live;
- alteração em `/home/ubuntu/chapada-leads-hermes`;
- remoção de legado ou qualquer trabalho da Fase 9.

## Riscos de entrada

Riscos transversais `R06`, `R07`, `R13`, `R63` e `R71`, além dos riscos
específicos `R72`–`R75`, permanecem governados por
`../06-risk-register.md`. Nenhum risco aberto é convertido em autorização pelo
simples início da fase.

## Gate de fechamento

A fase só poderá fechar após a cadeia completa da spec: identidade de release,
dark canary, ingress, teste de Carlos, canary E2E explicitamente autorizada,
rollout/rollback, revisão terminal, CI remoto e atualização final dos riscos. Na
entrada atual, somente o primeiro gate está autenticado e `phase9_started=false`.
