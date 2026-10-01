# Base integrada do Agente V2

## Uso

A `main` é a base de desenvolvimento consolidada. Antes de iniciar a integração
secundária, faça fetch e crie uma branch isolada sobre `origin/main`. Não escolha
uma branch antiga por nome ou data. Para investigar a operação, execute sempre
READ → VERIFY de [runtime-authority.md](runtime-authority.md).

**Fonte integrada não é runtime publicado.** Esta consolidação não configura API,
não muda o canal ManyChat, não cria imagem e não reinicia GA, TEST ou Ops. O dashboard
separado e V3 não participam da integração.

## Proveniência da consolidação de 2026-10-01

| Fonte preservada | Revisão | Conteúdo |
|---|---|---|
| main anterior | `bd43318e8edefd7dde56d0b57cc3e700520341b2` | Autoridade canônica, runbook e testes operacionais |
| Agente qualificado | `346cf3331cce473c3c880add381f82ca1e48fa82` | Núcleo comercial, mídia, pagamentos, atendimento, Cloudbeds e formulários |
| Fechamento documental | `85fe1a4915c3f4c00740287a4de8ac92e7234c2d` | Qualificação da imagem e catálogo de formulários |
| Ops publicado | `039dd1a3c0a93e6b4e2e5192ba9da23c4f2977fb` | Painel/backend de leitura existente no repositório |
| Correção isolada | `ac0a44b055` | Rejeitar mounts graváveis pai/filho compartilhados entre componentes |
| Correção isolada | `1d35715a68` | Histórico do lead independente do volume de outros leads |

As três linhas principais foram integradas por merges reais, preservando a
ancestralidade, não por reset ou cópia superficial de diretórios. As duas correções
isoladas foram transplantadas e requalificadas: seis falhas causais antes, 248 testes
focais verdes depois.

### Melhorias comerciais preservadas

- Maya como autoridade semântica única; respostas na voz de atendente e recuperação sem reescritor concorrente.
- Contexto persistido, novo atendimento/renovação, resultados por componente e encerramento seguro de handoff.
- Pix/Wise com comprovante visual, PDF, áudio/transcrição xAI e mídia URL na mesma Maya.
- Stripe com modo explícito, receiver por conta, botões ManyChat e pagamentos independentes do status da reserva.
- Cloudbeds com cotação por grupo separada de inventário informativo e consultas sem escrita.
- Formulários de passeios após pagamento confirmado, catálogo incluído e validado na imagem.

Nesta consolidação, `v2_adapters`, `v2_application`, `v2_contracts`, `v2_host`,
`reservation_*`, `config`, `Dockerfile.v2` e `compose.v2.yaml` permanecem idênticos
à revisão comercial qualificada. As mudanças funcionais adicionais são do
verificador operacional e do Ops read-only, não da conversa ou dos pagamentos.

### Branches históricas e protótipos

Nem toda branch não ancestral representa uma melhoria aprovada faltante. Há
implementações alternativas anteriores de financeiro/mídia, recibos, OAuth e
rollback que divergem dos owners publicados. Elas não são importadas às cegas.
Branches, worktrees sujas, releases, fontes congeladas e evidências antigas ficam
preservadas; não há exclusão nem force-push nesta consolidação.

O inventário completo de refs, ancestralidade e equivalência de patches está na
evidência operacional host-local `workspace/v2-main-consolidation-727d3625/`.
A escolha da próxima base deve vir da main integrada, não desses protótipos.

## Documentação e CI

- `docs/refactor/ACTIVE.md` é um handoff curto para a base integrada.
- O conteúdo antigo está em `docs/refactor/ACTIVE.before-main-2026-10-01.md`, sem autorizações novas.
- `.github/workflows/v2-main.yml` roda a suíte integral por push/PR e reproduz propriedades, falhas/restarts/contention e mutações financeiras. Não recebe credenciais comerciais e não contém deploy.
- As fases 0–6 já falhavam na main anterior no GitHub. Seus workflows/jobs são preservados como validações históricas manuais; não são a validação automática desta base moderna.
- Contratos históricos continuam na suíte integral; não há deselection, ignore de testes ou `continue-on-error`. Manifests são regenerados pelos geradores existentes, não editados para mascarar falhas.

## Reproduzir a validação atual

Python 3.12, Docker acessível e checkout com **histórico Git completo** são necessários.
Alguns testes autenticam releases históricas; um archive sem `.git` não é substituto.
Crie um ambiente de testes sem configuração live, instale as dependências `runtime`
e `dev` declaradas no `pyproject.toml` e use pytest 9.1.1. O workflow contém a instalação executável.

```bash
docker pull mcr.microsoft.com/playwright:v1.55.0-noble
npm install --prefix artifacts/ops-dashboard/playwright --no-audit --no-fund @playwright/test@1.55.0
python scripts/check_fasttrack_boundaries.py
python scripts/generate_phase6_manifest.py --check
python scripts/generate_phase7_manifest.py --check
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  HERMES_LEADS_AGENT_CONFIG_PATH=/tmp/no-live-config \
  python -m pytest -q -p no:cacheprovider
git diff --check
```

Chromium executa testes reais de UI sobre dados sintéticos; não é conversa com cliente.
As suítes não autorizam mensagens, reservas, cobranças ou pagamentos reais.

## Limites para a API secundária

A API secundária permanece não implementada. A próxima tarefa precisa definir o
contrato do provedor e o escopo de aceitação. Maya, ferramentas e autoridade dos
efeitos existentes devem ser preservadas; um novo transporte não pode repetir uma
operação cujo resultado ficou desconhecido.

O último teste real de canal deixou a saída ManyChat sem confirmação; a consolidação
não resolve esse problema externo e não constitui aceitação E2E de WhatsApp ou
pagamento. Novas imagens e promoções exigem qualificação própria e autorização.
