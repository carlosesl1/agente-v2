# Maya Operational Dashboard Mockup

Mockup visual navegável do painel operacional da Maya, criado para aprovação da direção visual antes de qualquer integração com dados reais.

## Preview local

```bash
cd /home/ubuntu/maya-dashboard-mockup
python -m http.server 8088 --bind 127.0.0.1
```

Abra `http://127.0.0.1:8088/` no navegador local.

## Verificação

```bash
python -m unittest -v tests/test_mockup_contract.py
```

A qualificação visual também deve conferir as resoluções desktop (`1440 × 1050`) e mobile (`390 × 844`), filtros, mudança de período, atualização simulada e abertura/fechamento do painel de detalhes.

## Escopo e segurança

Este artefato é isolado e somente visual. Ele:

- não acessa banco;
- não chama APIs de produção;
- não executa reservas, pagamentos, handoffs, mensagens, tentativas ou qualquer outro efeito;
- não altera o dashboard `/ops` existente;
- não contém autenticação ou credenciais;
- não utiliza bibliotecas, fontes ou recursos remotos.

## Dados demonstrativos

Todo conteúdo exibido é marcado como **Dados demonstrativos**. Os 12 atendimentos, indicadores, conversas, reservas e pagamentos são inteiramente fictícios e determinísticos. Nenhum dado de cliente real é usado.

## Próxima fase após aprovação

Após a aprovação visual, uma especificação separada deverá mapear cada componente para projeções read-only dos bancos reais. Essa etapa não faz parte deste mockup e não deve expor SQLite diretamente ao navegador.
