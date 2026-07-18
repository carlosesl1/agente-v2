# ADR 0001 — Migração incremental por strangler

- Status: **aceita**
- Data: 2026-07-18

## Contexto

O runtime atual contém integrações e proteções úteis, mas o núcleo comercial está distribuído e o working tree não é uma base reproduzível. Uma reescrita big bang aumentaria o risco e impediria comparação objetiva.

## Decisão

Construir o kernel novo ao lado do fluxo atual, inicialmente em shadow. Migrar uma fronteira por vez e remover o caminho legado apenas após equivalência/aceitação comprovada.

## Consequências

- exige período temporário de dual-read;
- aumenta observabilidade necessária;
- preserva rollback;
- evita reescrever adapters já validados;
- torna a remoção do legado uma fase obrigatória.

## Alternativas rejeitadas

- continuar adicionando patches no legado;
- reescrever tudo e trocar em um único deploy;
- mover o cérebro para ManyChat/n8n.
