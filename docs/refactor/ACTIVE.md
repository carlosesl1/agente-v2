# Agente V2 — base integrada de desenvolvimento

## Fonte de verdade

- **Desenvolvimento e próxima integração de canal:** `main` deste repositório, após fetch. Abra uma branch isolada sobre essa base.
- **Runtime efetivamente publicado:** exclusivamente `ACTIVE_RUNTIME.json` após READ → VERIFY conforme `docs/operations/runtime-authority.md`.
- `main` não substitui `production/ga`, `production/ops` nem o manifesto. Componentes distintos não têm necessariamente o mesmo SHA ou imagem.

## Consolidação de 2026-10-01

O pedido autorizado é organizar na main as melhorias qualificadas existentes, sem configurar a API secundária e sem publicar outro runtime.

Fontes e limites estão em [Base de integração](../operations/integration-baseline.md). O núcleo do agente, seus prompts/configurações comerciais e `Dockerfile.v2` são preservados da revisão qualificada `346cf3331cce473c3c880add381f82ca1e48fa82`, cujo fechamento documental é `85fe1a4915c3f4c00740287a4de8ac92e7234c2d`.

A main também reúne a autoridade operacional que já estava na main, o Ops read-only existente e duas correções isoladas requalificadas: sobreposição de mounts e histórico de execuções por lead. A consolidação de fonte não muda o dashboard separado, a imagem ou os bancos da operação.

## Próximo trabalho

A API secundária permanece **não configurada**. Seu contrato, autenticação, canal e escopo de teste devem ser fornecidos/aprovados no novo trabalho. Esta consolidação não escolhe fornecedor nem autoriza envio, reserva, pagamento, reprocessamento ou deploy.

Não há NEXT automático herdado de branches antigas. O pedido atual no chat delimita o trabalho; a autoridade de runtime continua obrigatória.

## Validação

- Workflow automático: `.github/workflows/v2-main.yml`, suíte integral sem exclusões, Chromium real e reprodução das evidências de propriedades/falhas/mutações financeiras.
- Workflows das fases 0–6: preservados integralmente como execuções manuais históricas, sem disputar a validação automática da base moderna.
- Testes locais: ambiente sem configuração live, Python 3.12, dependências declaradas no `pyproject.toml` e pytest 9.1.1. Consulte as instruções na base de integração.
- Uma suíte verde não comprova aceitação de WhatsApp/API nem E2E financeiro real. O bloqueio conhecido de entrega do ManyChat não é resolvido por um merge.

## Histórico preservado

O handoff anterior, incluindo entradas já substituídas e a história Ops, foi preservado em [ACTIVE anterior à consolidação](ACTIVE.before-main-2026-10-01.md). Seus NEXT, branches e autorizações são históricos; não devem direcionar automaticamente novo trabalho.
