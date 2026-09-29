# Maya — voz de atendimento, não relato de sistema

## Pedido e escopo autorizado

Carlos pediu uma mudança conceitual geral: Maya fala como atendente da Chapada, não como observadora de consultas. O exemplo apontado foi “Não apareceu cotação de um único quarto privativo para três pessoas”. Não é pedido de substituição literal nem autorização de deploy.

Owner: orientação principal de Maya em `config/v2_terra_system_prompt.txt`. Continuação do candidato Cloudbeds `589ced171b32ff59f6494c18c3aca4a3ec3e7b4d`, na mesma worktree declarada. Autoridade GA/TEST/Ops verificada, sem alterações. V3 fora do escopo.

## Decisão

Comunicar o significado prático dos fatos para o cliente, em nome da Chapada: resposta direta, alternativa pertinente, próximo passo necessário. A certeza deve ser proporcional aos fatos. Uma ausência confirmada permite negativa direta; falha ou informação ausente não permite declarar lotação. Uma alternativa não cotada não permite prometer valor, reserva ou capacidade não demonstrada.

Esse princípio é comum a hospedagem, passeios, políticas, reservas, pagamento e handoff. Identificadores e distinções de execução continuam nos contratos internos. Não criar reescritor de resposta, lista de expressões proibidas, avaliador LLM, regex, regras por mensagem ou nova camada de aprovação. Troca pontual de frase não atende ao pedido; um pós-processador tiraria autoria da Maya.

## Resultado

- Implementado no prompt principal, sem mudanças em código de aplicação.
- Baseline e duas tentativas intermediárias preservados; versão `qualified` com oito cenários repetidos duas vezes, 16 frames válidos e revisão semântica integral.
- 231 testes focais/integração passaram; 2 contratos novos tiveram red/green. Não é nova suíte integral nem teste de canal.
- Relatório, respostas brutas, hashes e limites: `/home/ubuntu/workspace/v2-attendant-voice-727d3625/RESULTADO.md`.
- Autoridade verificada; GA/TEST/Ops preservados; sem deploy.

## Execução e aceite

1. Preservar respostas anteriores; preparar cenários controlados e comparar o prompt anterior com o candidato usando a mesma Maya real e entradas equivalentes.
2. Escrever teste de presença/integração da orientação global antes de editar. Este teste não certifica estilo de respostas.
3. Consolidar a voz em uma seção principal e corrigir apenas instruções próximas que induzem relato técnico, sem mudar consultas, fatos, efeitos, preços ou capacidades.
4. Sandbox sem tools, sender, worker de efeitos, credenciais comerciais ou banco operacional. Usar fixtures declaradas, não fingir consultas atuais a provedores.
5. Revisão humana direta das respostas: fato certo, escopo certo, tom de atendente, próximo passo viável, nenhuma falsa certeza. Repetir o candidato em contextos independentes; nenhum resultado não aprovado é apagado.
6. Rodar testes focais de prompt/contratos, guard de arquitetura e diff; versionar evidência e informar separadamente que não houve publicação nem teste WhatsApp.
