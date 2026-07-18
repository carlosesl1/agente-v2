# Baseline técnico — Fase 0

## Fonte

Captura sanitizada em:

```text
docs/refactor/evidence/phase-00/baseline-manifest.json
```

Data da captura: `2026-07-18T19:40:38Z`.

## Código de origem atual

- Repositório observado: `carlosesl1/manychat-hermes-chapada`.
- Branch: `main`.
- HEAD: `57408d8b2040399bc25ee7957505208079458884`.
- Working tree limpo: **não**.
- Entradas no status: `80`.
  - modificadas: `61`;
  - removidas: `13`;
  - não rastreadas: `6`.

Consequência: o commit HEAD não identifica os bytes do sistema atualmente construído/implantado.

## Runtime observado

- Container: `chapada-leads-hermes`.
- Estado: `running`.
- Health: `healthy`.
- Reinícios: `0`.
- Imagem: `sha256:2dc5f71557b82d4d0646ab1dba0b61edfa7d916320047dd03ce8554dbfa50d53`.

Dos 16 artefatos críticos comparados:

- 15 existem no container e têm hash igual ao working tree;
- `deploy/production/docker-compose.yml` não faz parte de `/app` na imagem.

Isso comprova correspondência parcial entre checkout e container, mas não vincula a imagem a um commit limpo.

## Configuração temporal

```text
native_agent_timeout_seconds = 120
http_timeout_seconds         = 300
min_write_budget_seconds     = 302
configuration_can_start_write = false
```

Essa configuração impede mecanicamente o início de writes protegidos pelo deadline. O runtime pode estar saudável e, ainda assim, o fluxo comercial ser incapaz de reservar.

## Complexidade estática do núcleo

Arquivos medidos:

- `app.py`;
- `domain/hermes_native_runner.py`;
- `domain/chapada_native_tools.py`;
- `domain/tool_executor.py`;
- `tools/side_effect_guard.py`.

Resultados:

```text
linhas:             13.969
nós de decisão:      2.238
arquivos de teste:      73
funções de teste:       906
```

Quantidade de testes não equivale a prova E2E. Parte relevante dos testes começa com estado canônico pré-carregado, planner roteirizado, stores em memória ou provider fake.

## Limitações do baseline

- Não contém mensagens reais, PII ou payloads brutos.
- Não prova que reservas ou pagamentos funcionam.
- Não executa writes.
- Não altera o sistema live.
- Não substitui snapshots/replays sanitizados que serão produzidos na Fase 1.

## Decisão da Fase 0

O sistema atual é fonte de comportamento e incidentes, não base segura para novos patches incrementais. A refatoração deve começar em trilha limpa, com migração progressiva e rollback explícito.
