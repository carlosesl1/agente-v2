# Agente V2 completo no WhatsApp — canary exclusivo do Carlos

## Estado do documento

- Data: 2026-07-24
- Status: aprovado em conversa pelo operador
- Branch de partida: `phase8-shadow-canary-rollout`
- Candidato de partida: `47965c5d7262665f5c1617b3b7066e5063c106c4`
- Contato autorizado: ManyChat subscriber `1873018537`

## Objetivo

Implantar um Agente V2 completo em um canary paralelo no WhatsApp, acessível somente pelo contato autorizado do Carlos, capaz de conversar naturalmente, consultar Cloudbeds e Bókun, criar reservas reais, gerar links Stripe em modo teste, executar handoff e enviar respostas/flows pelo ManyChat.

A preparação termina antes de qualquer conversa humana ou efeito comercial. Carlos inicia o teste pelo WhatsApp somente depois de receber o gate de prontidão.

## Escopo comercial do primeiro teste

### Habilitado

- conversa multi-turno com `openai-codex/gpt-5.6-luna`;
- consulta real Cloudbeds;
- consulta real Bókun;
- consulta do Cérebro/FAQ;
- apresentação de opções públicas sem IDs internos;
- coleta natural de dados ao longo de vários turnos;
- uma confirmação natural antes do write;
- criação real de reserva Cloudbeds;
- criação real de carrinho/reserva Bókun;
- geração de link Stripe em modo teste para agência e hostel;
- formulários, imagens, custom fields, flows e resposta de texto ManyChat;
- handoff com tag e registro durável;
- read-back da reserva criada;
- cancelamento/limpeza controlada depois do teste, somente após o operador inspecionar e autorizar a limpeza.

### Fechado

- cobrança Stripe real;
- confirmação automática de pagamento Stripe;
- confirmação ou baixa automática de Pix;
- confirmação ou baixa automática de Wise;
- postagem de pagamento no Bókun ou Cloudbeds;
- mensagens ou efeitos para qualquer subscriber diferente de `1873018537`;
- rollout público;
- migração de outros leads para o V2;
- retry automático após resultado de write incerto.

## Arquitetura

```text
WhatsApp
  -> ManyChat
    -> regra exclusiva subscriber 1873018537
      -> endpoint HTTPS canary V2
        -> API V2 autenticada
          -> inbox durável + dedupe + debounce
            -> worker conversacional
              -> GPT-5.6 Luna
              -> Cloudbeds/Bókun/Cérebro reads
              -> reducer + comandos duráveis
                -> worker Cloudbeds
                -> worker Bókun
                -> worker Stripe test
                -> worker handoff/follow-up
                -> public outbox
                  -> worker ManyChat
```

O canary usa:

- endpoint HTTPS próprio;
- API e worker separados;
- estado persistente isolado do runtime antigo;
- imagem OCI imutável;
- exatamente o digest aprovado no CI;
- credenciais montadas no runtime, nunca incorporadas à imagem ou ao repositório;
- allowlist finita com um único subscriber;
- autoridade assinada e com validade finita para o teste.

O agente antigo continua atendendo os demais contatos sem mudança.

## Componentes e responsabilidades

### API V2

- autenticar `X-V2-Webhook-Secret`;
- aplicar limite de corpo e JSON estrito;
- exigir identidade estável do evento e do subscriber;
- rejeitar subscriber fora da allowlist antes da persistência operacional;
- persistir a mensagem no inbox antes de retornar `202`;
- deduplicar replay idêntico e rejeitar conflito de payload;
- não executar modelo ou provider inline.

### Inbox e worker conversacional

- agrupar mensagens na janela de debounce;
- preservar ordem e isolamento por lead;
- carregar contexto e estado duráveis;
- consultar perfil ManyChat somente para o subscriber autenticado;
- chamar `openai-codex/gpt-5.6-luna` com a superfície comercial fechada do V2;
- não disponibilizar ferramentas administrativas, terminal, arquivos, deploy ou mensagens genéricas ao modelo;
- emitir reads, comandos e projeções públicas tipados.

### Catálogo e reads

- produtos Bókun são selecionados exclusivamente por ID canônico interno;
- o ID canônico vem de catálogo estável privado e nunca é pronunciado ao lead;
- o write Bókun consome a opção normalizada produzida pelo lookup do mesmo produto/data/participantes;
- o write Cloudbeds consome a opção normalizada produzida pelo lookup da mesma propriedade/datas/hóspedes;
- opções expiradas ou incompatíveis exigem novo lookup;
- nenhum preço ou disponibilidade pode nascer do modelo.

### Confirmação

- o lead confirma uma única vez um resumo natural e completo;
- a assinatura de confirmação inclui somente campos comerciais e de execução normalizados;
- prosa do provider, previews e campos voláteis ficam fora da assinatura;
- mudança de opção, datas, pessoas, moeda ou total invalida a confirmação;
- confirmação não depende de uma frase mágica.

### Workers de reserva

Cloudbeds e Bókun têm workers distintos. Cada worker:

- reivindica um comando durável com lease e fencing token;
- reconstrói o payload a partir de estado canônico e opção consultada;
- revalida subscriber, workflow, confirmação, opção e deadline;
- registra o fence antes do primeiro possível write;
- executa no máximo uma chamada provider-capable por tentativa;
- persiste outcome e recibo;
- classifica falha comprovadamente pré-dispatch como retryable;
- classifica timeout ou erro depois de possível dispatch como `manual_review`;
- nunca faz retry cego após resultado incerto.

### Stripe test

- o worker só aceita reserva Cloudbeds/Bókun confirmada e persistida;
- usa contas e contexto de negócio separados para hostel e agência;
- exige ambiente Stripe `test` em ambos;
- o link é idempotente por reserva, conta e intenção;
- não aceita link real/live no canary;
- não confirma pagamento nem baixa o provider;
- persiste recibo sanitizado antes de publicar o flow.

### ManyChat delivery

- o worker consome somente public outbox durável;
- revalida allowlist imediatamente antes de cada request;
- usa idempotency key estável;
- suporta texto, custom fields, flows, imagens e tag de handoff por DTOs fechados;
- nunca publica JSON, payload, hash, ID canônico, stack trace ou instrução interna;
- falha comprovadamente antes da conexão libera a claim;
- outcome incerto vira `manual_review` e bloqueia repetição automática;
- um lead não autorizado não consegue enfileirar nem consumir delivery.

### Handoff

- handoff é um workflow irmão, não um fallback textual;
- a mensagem pública e a tag são jobs duráveis e independentes;
- e-mail interno permanece opcional e não bloqueia o handoff;
- `route=handoff` se e somente se `reply_type=handoff`;
- handoff terminal suprime pedidos antigos de confirmação.

## Autoridade e isolamento

A autoridade do teste deve vincular:

- subscriber `1873018537`;
- canal ManyChat canary;
- digest da política de capabilities;
- geração imutável;
- deadline UTC;
- orçamento finito por classe de efeito;
- digest do candidato e da imagem;
- allocations distintas para mensagens e efeitos.

A allowlist deve existir em três boundaries:

1. ingresso;
2. emissão/commit de comando;
3. execução de provider ou delivery.

A remoção do subscriber da allowlist deve bloquear novos efeitos sem depender de prompt, modelo ou reinício de sessão.

## Estado e durabilidade

O canary usa arquivos/volumes próprios para:

- inbox;
- conversation boundary;
- execution ledger;
- follow-up/payment state;
- payment initiation;
- public outbox;
- heartbeat;
- authority manifest;
- transcript criptografado.

A API e o worker compartilham somente os owners necessários. O runtime antigo não acessa esses stores. Reinício deve preservar dedupe, leases, fences, commands, receipts e manual review.

## Configuração de efeitos

O modo final do canary é `controlled_write`. Os gates ativos são:

- Cloudbeds reservation write: `true`;
- Bókun cart/reservation write: `true`;
- Stripe link: `true`, com ambiente forçado a `test`;
- ManyChat delivery: `true`;
- payment confirmation/settlement: `false`;
- Pix/Wise settlement: `false`.

O runtime exige o acknowledgment operacional exato e falha ao iniciar se:

- um gate ativo não tiver transport concreto;
- Stripe estiver em live;
- allowlist estiver vazia ou contiver outro subscriber;
- authority manifest estiver ausente, expirado ou inválido;
- heartbeat do worker estiver ausente;
- qualquer worker obrigatório estiver fechado;
- credenciais e IDs ManyChat necessários estiverem incompletos.

## ManyChat e coexistência

- criar endpoint canary V2 separado;
- configurar regra ManyChat exclusivamente para o subscriber autorizado;
- não reutilizar a rota do agente antigo para o primeiro teste;
- impedir que BOT IA/n8n/fluxo legado respondam ao mesmo evento;
- provar provenance por novo ingress V2, nova sessão Luna e outbox/receipt V2;
- manter o restante dos contatos no fluxo anterior;
- rollback de canal: remover/desativar a regra canary;
- fast stop do V2: fechar `V2_ENABLE_MANYCHAT_DELIVERY` e reiniciar somente o canary.

## Qualificação antes do teste humano

### RED/GREEN focado

- allowlist em ingress, commit e execução;
- composição `controlled_write` completa;
- Cloudbeds/Bókun write workers;
- Stripe test worker;
- ManyChat text/field/flow/image/tag worker;
- idempotência, fencing, restart e manual review;
- confirmação única;
- package hospedagem+passeio;
- ausência de efeitos quando um request do lote é inválido;
- impossibilidade de settle/confirm payment.

### Conversa E2E sem efeitos externos

Executar uma conversa ManyChat-shaped completa com transports registradores:

1. saudação;
2. hospedagem;
3. passeio;
4. pacote híbrido;
5. coleta natural de campos;
6. confirmação;
7. comandos Cloudbeds/Bókun;
8. links Stripe test;
9. flows e respostas;
10. handoff.

O teste precisa demonstrar os payloads tipados e a ordem causal, mas não pode tocar os providers.

### Probes reais read-only

- ManyChat `getInfo` para o subscriber autorizado;
- Cloudbeds availability;
- Bókun availability e metadata;
- configuração das duas contas Stripe test sem criar link;
- existência dos flows, fields, imagens e tag necessários;
- health/readiness e heartbeat.

### Gates do candidato

- testes focados;
- regressão proporcional;
- full suite uma vez no candidato congelado;
- Ruff, compile e `git diff --check`;
- scan de segredos e PII;
- revisão integrada única dos boundaries de efeito;
- build OCI único;
- CI no SHA exato;
- pull da imagem publicada por digest;
- verificação de labels, usuário não-root, rootfs read-only e sem mounts indevidos.

## Implantação

A implantação termina com:

- API e worker canary ativos;
- endpoint HTTPS canary saudável;
- regra ManyChat exclusiva configurada;
- gates e credentials efetivos verificados sem imprimir valores;
- stores íntegros;
- filas, comandos, reservas, links e outboxes vazios;
- nenhum processo de teste pendente;
- nenhum evento enviado pelo preparador;
- rollback exercitado como configuração, sem executar uma conversa.

Não fazer merge em `main` nem rollout para todos como efeito implícito da preparação. A promoção da branch/imagem para o canary é permitida; rollout geral exige nova decisão.

## Teste do Carlos

Depois do gate, Carlos envia pelo WhatsApp, uma mensagem por vez, seguindo a janela de debounce. A sequência recomendada é:

1. saudação;
2. consulta Cloudbeds;
3. reserva Cloudbeds após resumo e confirmação;
4. link Stripe test do hostel;
5. consulta Bókun;
6. reserva Bókun após resumo e confirmação;
7. link Stripe test da agência;
8. pacote híbrido;
9. handoff.

Após cada write:

- parar a conversa;
- executar read-back do provider;
- auditar ledger, command, outbox e delivery;
- confirmar que não houve efeito duplicado;
- continuar somente se o estágio anterior estiver consistente.

## Stop conditions

Parar imediatamente e fechar delivery/writes se houver:

- resposta duplicada;
- resposta originada pelo runtime legado;
- subscriber diferente no V2;
- timeout após dispatch;
- `manual_review`;
- preço, disponibilidade ou link sem recibo;
- mismatch entre opção consultada e payload de write;
- reserva duplicada;
- Stripe live;
- qualquer baixa financeira;
- falha de heartbeat/readiness;
- outbox presa ou envio de resultado incerto.

## Pós-teste

- read-back de cada reserva;
- inventário de links Stripe test;
- inventário de mensagens/flows/tags;
- contagem de effects/commands/receipts/manual review;
- relatório sanitizado;
- Carlos decide se as reservas serão mantidas ou canceladas;
- cancelamento é um write separado e verificado;
- somente depois é discutido rollout gradual para outros leads ou pagamentos reais.

## Critérios de aceite

O sistema está pronto para Carlos iniciar o teste quando todos forem verdadeiros:

1. candidato, CI e imagem estão vinculados ao mesmo SHA;
2. `controlled_write` compõe todos os workers exigidos sem fallback/no-op;
3. allowlist tem exatamente `1873018537`;
4. Cloudbeds/Bókun writes estão habilitados apenas no canary;
5. Stripe está habilitado e mecanicamente fixado em `test`;
6. ManyChat delivery está habilitado apenas para Carlos;
7. payment confirmation/Pix/Wise settlement continuam fechados;
8. probes reais read-only estão saudáveis;
9. endpoint HTTPS, API, worker e heartbeat estão saudáveis;
10. stores e filas começam vazios;
11. nenhum efeito real ocorreu durante a preparação;
12. rollback e stop conditions estão documentados e executáveis;
13. nenhum evento humano foi simulado como aprovação de conversa;
14. Carlos recebeu a sequência de teste e o aviso para parar após cada write.
