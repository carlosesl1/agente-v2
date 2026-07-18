# Fase 0 — Baseline e governança

## Status

`concluída`

## Objetivo

Criar a base limpa e verificável da refatoração sem alterar o sistema atual.

## Escopo autorizado

- criar e versionar documentação no novo repositório;
- observar Git/runtime atual em modo read-only;
- registrar métricas, hashes e configuração não secreta;
- criar validação local/CI da documentação.

## Fora do escopo

- editar `/home/ubuntu/chapada-leads-hermes`;
- alterar container, env, profile ou config live;
- limpar contato;
- enviar ManyChat/WhatsApp;
- chamar provider write;
- implantar imagem.

## Checklist de entregáveis

- [x] repositório novo clonado;
- [x] charter;
- [x] baseline sanitizado;
- [x] taxonomia de falhas;
- [x] arquitetura-alvo;
- [x] plano faseado;
- [x] estratégia de validação/rollout;
- [x] registro de riscos;
- [x] ADRs iniciais;
- [x] validador local;
- [x] CI;
- [x] hashes da evidência;
- [x] secret/PII scan local;
- [x] commit criado;
- [x] push verificado — local e `origin/main` conferidos após fetch.

## Evidências capturadas

Diretório:

```text
docs/refactor/evidence/phase-00/
```

Conteúdo:

- `baseline-manifest.json`;
- `source-working-tree-status.txt`;
- `source-diff-stat.txt`;
- `source-diff-numstat.txt`;
- `validation-result.json`;
- `critical-artifact-hashes.json`;
- `SHA256SUMS` após fechamento.

## Critérios de aceite

1. Validador retorna exit `0`.
2. JSONs são parseáveis e schema básico confere.
3. Nenhum padrão de segredo/PII conhecido é encontrado.
4. Todos os documentos canônicos existem.
5. `SHA256SUMS` confere.
6. Working tree do `agente-v2` contém somente arquivos intencionais.
7. Primeiro commit usa Conventional Commit.
8. Remote HEAD corresponde ao commit local após push.
9. Nenhuma alteração aparece no sistema live causada pela fase.

## Decisão de avanço

A Fase 0 está concluída. A Fase 1 é a próxima fase elegível, mas deve começar em um novo ciclo explícito, preservando a regra de uma fase por vez.
