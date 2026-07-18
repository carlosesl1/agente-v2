# ADR 0006 — Mesmo digest OCI da canary ao rollout

- Status: **aceita**
- Data: 2026-07-18

## Contexto

Canaries anteriores divergiram em mount, Python, profile e env. Build sobre working tree sujo torna o SHA do Git insuficiente para identificar os bytes.

## Decisão

Construir uma imagem a partir de commit limpo, registrar manifesto/hashes e promover o mesmo digest OCI, sem rebuild. Diferenças de ambiente permitidas são somente gates/allowlist/percentual declarados.

## Consequências

- rollback é por digest;
- profile e comportamento devem estar na imagem ou ser verificados por hash no startup;
- deploy com working tree sujo é proibido;
- canary se recusa a rodar diante de drift.
