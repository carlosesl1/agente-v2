# Task 3 — Proveniência dos adapters de leitura

Data da extração: 2026-07-23

Esta evidência registra somente a origem técnica consultada. Nenhum módulo listado abaixo é importado, executado ou usado como fallback pelo V2.

## Fontes reais encontradas

| Domínio | Arquivo legado somente leitura | SHA-256 |
|---|---|---|
| Cérebro/FAQ | `/home/ubuntu/chapada-leads-hermes/services/cerebro.py` | `a4ff4bc7fd6f104293e66ac7291b1f59b2246e2e8ba7b2cc6aac664a5b5796b9` |
| Cloudbeds service | `/home/ubuntu/chapada-leads-hermes/services/cloudbeds.py` | `15cc293d985142a0f59a6faed07629efbf1376396567824384659cd13f013082` |
| Cloudbeds tool V2 | `/home/ubuntu/chapada-leads-hermes/tools/cloudbeds_v2_tools.py` | `43f0a0a8669f3030ee90a5fa1a1932769e1aa35dc46a0460cf0d5648f55f52ad` |
| Bókun service | `/home/ubuntu/chapada-leads-hermes/services/bokun.py` | `5df4133cc2980d43ec8a8cab3bf8aa0a2d808c7dcad0138cd47a8b1d522be0ed` |
| Bókun tool V2 | `/home/ubuntu/chapada-leads-hermes/tools/bokun_v2_tools.py` | `239accab85abd62b5abb240381307c9b25d939e60b6ebe9630b3d5b2caaf4310` |

Os caminhos `integrations/cloudbeds/client.py` e `integrations/bokun/client.py` citados no plano inicial não existem no checkout legado consultado. Eles não foram substituídos por conteúdo presumido; a tabela usa os arquivos reais encontrados.

## Código extraído atrás de contratos V2

- `v2_adapters/knowledge.py`
- `v2_adapters/cloudbeds.py`
- `v2_adapters/bokun.py`
- `v2_contracts/providers.py`
- `v2_application/reads.py`

Os adapters recebem transports injetados, emitem somente `ReadObservation` tipada, vinculam cada resposta ao hash do request e mantêm IDs técnicos em commitment privado. Eles não importam módulos do legado.

## Testes de equivalência e segurança ativos

`tests/test_v2_reads.py` prova:

1. vínculo de datas, ocupação, preço e moeda da hospedagem;
2. remoção dos IDs privados Cloudbeds do payload público;
3. rejeição de nome arbitrário no lugar de ID canônico Bókun;
4. remoção de campos de credencial na resposta do Cérebro;
5. rejeição de observação expirada.

O scanner `scripts/check_fasttrack_boundaries.py` bloqueia imports e referências de caminho para o runtime legado.
