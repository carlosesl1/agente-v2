# Pix/Wise por comprovante — execução local

**Base:** cd0ec4e (somente desenho sobre TEST 15798cd). Worktree existente atendimento-simples-727d3625; ACTIVE.md preexistente preservado. Autoridade live separada, sem deploy, banco real, cobrança ou envio externo.

1. RED causal no domínio: evidência visual tipada Pix/Wise, origem documental explícita; igualdade de recebedor/valor/moeda/janela e identidade global. Acrescentar tipo sem mudar a semântica de VerifiedWiseCredit e Pix histórico.
2. GREEN domínio + serialização: comprovante aceito não confirma banco; revisão pendente derivada da evidência persistida no owner. Testar reopen/replay e conflito entre obrigações.
3. Mídia e Maya: download limitado de imagem, hash dos bytes/evento, pixels no mesmo child. Extensão opt-in V9 com observação nullable, sem segundo modelo. Comparação e resolução da cobrança autenticadas; resposta da Maya após retorno da ferramenta.
4. Integração: cobrança emitida e reserva confirmada → análise armazenada no owner → claim financeiro → settlement existente com métodos Pix/Wise → completion existente. Reutilizar guard, fence e outbox. Revisão futura não é handoff; erro impeditivo usa coordinator existente.
5. Testes conectados com SQLite e HTTP controlado: Pix/Wise × hostel/agência, campos ausentes/divergentes, outro lead, duplicação por reenvio/reexportação, concorrência/reinício, cancelamento/timeout, baixa e retorno factual. Exercitar fábrica/child e contexto de continuação. Não confundir fixtures com pagamento real.
6. Qualificação: testes afetados e guard/manifest do pacote; revisão inline; commit explícito, relatório de evidência e limites, autoridade live novamente.

Configuração fica fechada por padrão. Cadastro estruturado de recebedores deve ser configuração explícita: nunca extrair conta da prosa nem preencher campo lido a partir do esperado. Aceitar moeda/valor destinados ao beneficiário, sem converter origem Wise. Dashboard/UI e ações humanas futuras fora do escopo.
