# ADR 0006 — Child manifest OCI imutável da canary ao rollback

- Status: **aceita e corrigida**
- Data original: 2026-07-18
- Correção: 2026-07-21
- Design autoritativo: `2889e9ec08f466bbb16a30e4bb5c9a098daf54d3`

## Contexto

Canaries anteriores podiam divergir em source, wheel, runtime graph, mounts, profile e
env. Git SHA, tag, image ID ou hash de archive isolados não provam os bytes executados
nem ligam a avaliação source/runtime ao container efetivo.

A Fase 8 também precisa construir uma única vez depois de fechar a composição
operacional. Build anterior aos contratos upstream, wiring e reviews congela uma
imagem incapaz de atravessar os gates posteriores sem rebuild.

## Decisão

A identidade executável é o **child manifest digest `linux/arm64`**, resolvido e
autenticado a partir de um OCI index com exatamente um child executável e nenhum
descriptor extra de plataforma/attestation.

A cadeia autorizativa é acíclica:

```text
source functional F + evidence-only E
→ wheel 0.8.0
→ runtime functional F + evidence-only E
→ um approval manifest combinado externo
→ payload-context manifest
→ source attestation
→ build-input identity sobre tar canônico
→ OCI index
→ child manifest linux/arm64
→ config + layers
```

Somente os candidatos funcionais F entram nos bytes executáveis. Os filhos E e o
approval manifest autenticam evidência e reviews, sem entrar no payload e sem criar
auto-hash.

Canary, promoção e rollback materializam a mesma referência imutável:

```text
<registry>/<repository>@sha256:<linux-arm64-child-manifest>
```

O release manifest comum termina em index/child/config/layers. Container ID, mounts,
instance ID, roots, effective config e stage binding pertencem a uma
`ContainerExecutionAttestation` separada, produzida por instância e revalidada antes
de readiness.

## Gates

1. Design aprovado não autoriza implementação.
2. Plano substituto e quarentena precisam de aprovação própria antes do Slice 0.
3. Source F/E, wheel, runtime F/E e release contract precisam fechar seus AND gates.
4. Só depois existe uma decisão humana distinta **GO/NO-GO de build**.
5. Build, dark canary, ingresso, conversa humana, E2E, rollout e closeout continuam
   gates independentes.

## Consequências

- tag mutável, image ID e archive hash são apenas evidência suplementar;
- nenhum rebuild é permitido entre canary, promoção e rollback;
- profile, graph, policy, catálogo e configuração não secreta entram na cadeia de
  hashes e no startup fail-closed;
- o builder recebe tar canônico fechado, nunca um diretório operacional mutável;
- o runtime operacional não pode ser build context;
- rollback retém a mesma identidade child-manifest, sem apagar efeitos comerciais;
- qualquer mudança em F, E, wheel, approval manifest, payload, attestation ou runtime
  invalida reviews e exige nova identidade.

## Estado operacional

Esta ADR corrige apenas a autoridade documental. Implementação, wiring, build,
canary e rollout permanecem `NO-GO` até seus gates explícitos.
