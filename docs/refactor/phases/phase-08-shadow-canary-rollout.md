# Fase 8 — Correção da fronteira operacional, shadow e rollout

## Estado

- Design upstream: **aprovado 3/3 e aprovado por Carlos**.
- Design autoritativo: commit `2889e9ec08f466bbb16a30e4bb5c9a098daf54d3`,
  tree `ed57032319d2319389412f4407b268e3d7b7a78c`, blob
  `0e599670b4bc585b1665d932a84afcf3c4b57456`, SHA-256
  `0f7486191e9963b3786a83cc7096c2af12a89905c5d92fcc27edf431367dcf60`.
- Plano substituto: `docs/superpowers/plans/2026-07-21-phase-8-operational-boundary-correction.md`.
- Quarentena: `../evidence/phase-08/quarantine-manifest.json`.
- Gate atual: **plano/quarentena preparados para revisão; Slice 0 bloqueado**.
- Implementação, wiring e build: **NO-GO**.
- Dark canary, ingress, conversa, E2E e rollout: **não iniciados**.
- `phase8_started=true`; `phase9_started=false`.

A autorização de 2026-07-21 cobre somente a preparação do plano TDD substituto e da
quarentena documental. Não autoriza código de runtime, wheel, Docker, provider,
ManyChat, rede, deploy ou efeito live.

## Por que o plano anterior foi substituído

A auditoria do composition root provou que o entrypoint canônico não construía um
adapter Phase 7 concreto. Além disso, a arquitetura anterior tratava image ID/archive
como autoridade primária e adiava contratos indispensáveis de reply/replay, relay,
execution locks, migration ownership, qualification e release identity.

Executar aquele plano congelaria uma imagem que não continha o caminho necessário ao
gate E2E. Os blobs antigos permanecem preservados como história, mas são
`HISTORICAL-NON-EXECUTABLE` e não possuem command ownership.

## Escada de gates corrigida

1. **Design — fechado:** spec imutável aprovada 3/3 e aprovada por Carlos.
2. **Plano/quarentena — em preparação/revisão:** somente após aprovação pode abrir o
   Slice 0.
3. **Contract lock:** interfaces antigas inalcançáveis; contratos e RED provenance
   fechados; zero runtime change.
4. **Upstream:** kernel 0.8.0, schemas/roots, UDS, coordinator, replay, relays,
   deliveries, qualification e cancellation por TDD faseado.
5. **Upstream terminal:** candidatos source F/E imutáveis, heavy gate econômico e
   review AND no mesmo par.
6. **Wheel:** wheel 0.8.0 autenticada e revisada contra o mesmo source F/E.
7. **Runtime wiring:** candidata limpa, factory canônica sem `None`, startup/lifespan
   e runtime F/E aprovados.
8. **Release contract:** tar canônico, source attestation, approval manifest combinado,
   registry policy e child manifest `linux/arm64` testados sem build live.
9. **GO/NO-GO de build:** decisão separada; nada anterior implica GO.
10. **Build:** publicação OCI única e autenticação de index/child/config/layers.
11. **Dark canary:** reads reais, zero provider write e zero delivery.
12. **Ingress fechado:** rota isolada, somente o contato autorizado, outbound e writes
    ainda fechados.
13. **Conversa humana:** Carlos é avisado e executa conversas naturais; simulação não
    substitui sua aprovação.
14. **Canary E2E:** exige autorização posterior que fixe contato, workflow, provider,
    período, único write e cancelamento.
15. **Rollout/closeout:** mesmo child digest, estágios com GO explícito, rollback e
    Fase 9 ainda separada.

## Invariantes atuais

- `/home/ubuntu/chapada-leads-hermes` permanece somente leitura e nunca é build
  context.
- Roots Boundary v7, Phase5-v5 ou Phase6-v1 encontrados em destino de migração são
  stop conditions; a implementação requer roots novos e exatos.
- Mixed mode é proibido sem `migration-ownership-v1` compartilhado por todos os
  mutators legacy; caso contrário, somente cutover global quiescente é elegível.
- Kernel e `ToolDispatch` permanecem owners únicos de decisão/autorização.
- Provider writes e delivery ocorrem somente em workers pós-commit.
- Qualquer timeout, summary ausente ou `Needs fixes` zera o gate AND da identidade.
- Correção material invalida todos os pareceres anteriores.
- Raw output, PII, segredos e payload provider não entram no Git.

## Próxima decisão

O commit documental de plano/quarentena precisa passar validators e revisão AND.
Depois, Carlos revisa o plano escrito. Somente uma aprovação explícita posterior abre
o Slice 0; nenhum trabalho de implementação começa automaticamente.
