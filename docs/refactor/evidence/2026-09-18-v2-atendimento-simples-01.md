# V2 atendimento simples — evidência do incremento 1

## Entrega e limites

Correção implementada localmente em `refactor/v2-atendimento-simples-727d3625`, sobre o candidato funcional `8980646d5615db9ecb32c97f4579ba09080dc02a`.

1. O seletor lê `options[].paymentMethods.allowedMethods` e não aceita o formato antigo na raiz como fallback.
2. Valor e respostas usam a opção habilitada; uma opção incompatível não empresta seu preço para autorizar o submit.
3. A ausência de pagamento externo retorna `no_effect` com causa `checkout_external_payment_unavailable` antes do submit. O carrinho auxiliar pode existir; `no_effect` refere-se ao booking final.
4. O port preserva `no_effect`, `rejected` e a causa local reconhecida em `normalized_status`, sem novo schema/banco e sem copiar texto arbitrário para esse campo.
5. A causa sobrevive a fechar/reabrir o SQLite temporário e o worker permanece ocioso na segunda execução, com um único dispatch consumido.

**GO apenas para conservar a correção local testada. NO-GO para afirmar simplificação completa, booking real ou promover produção.** Não houve deploy, reserva, pagamento, envio WhatsApp, alteração de estado live, Maya Ops, V3 ou legado. Revisão feita inline, sem subagentes. Não houve push nem CI remoto novo.

## Testes realmente executados

Pasta dos logs: `/home/ubuntu/workspace/v2-simplificacao-atendimento-727d3625/`.

| Artefato | Resultado | Significado |
|---|---|---|
| `01-parser-red.txt` | 9 falhas esperadas | O parser antigo não encontra o contrato nested e aceita o decoy de raiz. |
| `02-parser-green.txt` | 65 aprovados | Corrigido o seletor, suíte de transporte passa. |
| `03-cause-red.txt` | 5 falhas esperadas, 4 aprovados | Ausência do motivo local e colapso indevido em `rejected`. |
| `04-cause-green.txt` | 119 aprovados | Transporte e port corrigidos. |
| `05-affected-green.txt` | 152 aprovados | Suítes afetadas antes da correção do runner. |
| `06-full-suite.txt` | 2.205 aprovados, 7 falhas, 2.958 subtests aprovados | Diagnóstico inicial da suíte inteira, Python 3.11 selecionado pelo `uv --no-project`. Não é a evidência final de runtime suportado. |
| `07-full-suite-py312.txt` | 2.205 aprovados, 7 falhas, 2.958 subtests aprovados | Reexecução integral em Python 3.12.14; mesma lista de falhas. Exit 1, não “suíte toda verde”. |
| `08-base-history-py312.txt` | 8 falhas, 33 aprovados | Primeira comparação da fonte anterior por `git archive`; uma falha adicional por ausência do histórico Git no harness. |
| `09-base-history-git-py312.txt` | 7 falhas, 34 aprovados, 17 subtests aprovados | Baseline anterior com acesso ao histórico Git: exatamente as sete falhas atuais, sem diferenças de node IDs. |
| `10-affected-py312.txt` | 152 aprovados | Evidência final focal na versão suportada; inclui transporte, port, worker/SQLite reaberto, conclusão, execução ativa e guard. |
| `11-static.txt` | exit 0 | Guard de fronteiras, compileall, Ruff 0.15.10 e diff check. |

Não foram alterados os testes históricos nem seus manifests para fazê-los passar. A lista inteira de falhas e os SHA-256 dos logs/fontes estão em `INCREMENT-1-EVIDENCE.json`, gerado por `verify_increment_evidence.py`. Esse script exige igualdade exata entre os node IDs de falha do baseline e do candidato.

Os fixtures têm dados sintéticos e o shape nested comprovado na auditoria read-only anterior. As chamadas HTTP desta rodada usam `httpx.MockTransport`; não houve chamada ao Bókun real, modelo real nem conversa E2E nova.

## Revisão de código

- Produção alterada só em `v2_adapters/provider_http.py` e `v2_adapters/_provider_common.py`.
- Não houve nova política de retry, mudança de certeza transacional, estado paralelo, migração SQLite, fiscal de prosa ou regra de privacidade.
- Regressões cobrem método nested válido, estruturas ausentes/inválidas, decoy antigo, opção incompatível anterior, vínculo de valor da opção selecionada, dois passageiros preservados, submit único e ausência de submit.
- A classificação compartilhada deixa de chamar todo `no_effect` de `rejected`; a causa específica só é aceita no port Bókun e com `status=no_effect`.
- Testes de resultado ambíguo e bloqueios financeiros existentes continuam no conjunto aprovado. Isso não certifica o comportamento futuro da Maya, ainda não modificado.

## Graphify

O mapa estrutural foi gerado por AST sobre 69 arquivos de código V2 da base documental `ee4dfaac875069d23cf298c9cb1267da838c315b`: 1.837 nós, 5.294 arestas e 81 comunidades rotuladas. Sem modelo/subagentes na extração.

Artefatos: `graphify-out/graph.html`, `graph.json`, `GRAPH_REPORT.md`, `source-binding.json` e `HEALTH.json` na mesma pasta de evidência. **É um mapa pré-correção**, com vínculo de SHA-256 para cada arquivo indexado, não um snapshot atualizado do runtime. A fonte da worktree e os testes prevalecem sobre o grafo.

Limites declarados pelo extrator: 367 arestas sem endpoint extraído, 415 relações de mesmos endpoints colapsadas e 7 auto-relações. Os pacotes internos do kernel e testes não integram esse subset. O mapa facilitou encontrar o seletor compartilhado e os consumidores de `ModelRequest`; não comprova cobertura completa de consumidores.

## Pendências da simplificação aprovada

Continuam sem implementação, não escondidas pelo resultado dos testes:

- valores completos de titular/passageiros no contexto da Maya e vínculo explícito pessoa/papel;
- resultados terminais e financeiros por componente, com referências utilizáveis;
- histórico coerente, conclusões assíncronas escritas pela mesma Maya e deduplicação da comunicação;
- remoção dos protocolos redundantes de confirmação/seleção/progresso/correção de texto e das instruções contraditórias;
- testes de conversação com modelo real em harness de efeitos fechados.

O próximo incremento é o contexto e a ligação pessoa/papel, conforme roadmap do plano. A autorização funcional já consta na especificação; publicação/produção e operações reais continuam fora dela.
