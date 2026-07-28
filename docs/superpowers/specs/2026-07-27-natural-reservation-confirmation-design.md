# Design — trava natural de aprovação para ações críticas

Data: 2026-07-27
Atualizado: 2026-07-28
Status: desenho revisado para confirmação curta vinculada ao estado; aguardando aprovação da revisão

## Problema observado

O child da Maya é stateless por turno. Depois que o pai registra um `AwaitingConfirmationState`, a próxima `ModelRequest` contém apenas a mensagem atual e fatos comerciais; não contém a existência, a versão nem a projeção pública do resumo pendente. Uma confirmação natural como “sim, pode reservar exatamente esse passeio e gerar o link” pode então ser classificada novamente como seleção, repetindo o resumo em vez de confirmá-lo.

O resumo determinístico atual também expõe vocabulário técnico (`BRL 334.95`, `stripe`) e omite data, participantes, taxa e sinal.

## Objetivos

1. Permitir que a Maya interprete uma aceitação natural, inclusive curta, do resumo vigente sem obrigar o cliente a repetir produto, data, preço ou pagamento.
2. Manter a autorização inteiramente vinculada a estado estruturado, versão atual, releitura fresca e binding privado.
3. Produzir resumo e acknowledgement naturais para WhatsApp.
4. Não criar reconhecimento por regex, substring, lista de palavras ou frase mágica.
5. Validar com modelo real e providers controlados/reads reais, mas com workers de reserva, pagamento, handoff e entrega mecanicamente bloqueados.

## Não objetivos

- Não alterar a política comercial de sinal ou taxa.
- Não relaxar frescor, versão, idempotência, fencing ou read-back.
- Não alterar reserva Bókun, Stripe ou ManyChat já existentes.
- Não executar novo efeito externo durante a validação desta mudança.
- Não resolver neste trabalho o problema separado de replay de eventos com identidade divergente.

## Padrão comparado: Hermes, Codex e Claude Code

A comparação foi feita em 2026-07-28 nas documentações oficiais e, no caso do
Hermes instalado neste servidor, também no código da trava que antecede a
execução das ferramentas.

| Sistema | Padrão observado | Regra incorporada na Maya |
|---|---|---|
| Hermes | Detecta ação perigosa antes da ferramenta; mostra alvo e motivo; oferece aprovação única, por sessão, permanente ou negação; timeout e silêncio negam; regras hardline/deny têm precedência; o gateway bloqueia enquanto aguarda. | Interceptar antes do comando, mostrar efeito concreto, expirar sem resposta e impedir retry após negação. Para transações de clientes, aceitar **somente aprovação única**, nunca por sessão ou permanente. |
| Codex | Separa sandbox (o que pode tecnicamente fazer) de política de aprovação (quando precisa parar e perguntar); `on-request` pede elevação para sair do escopo; `untrusted` restringe comandos mutáveis; políticas granulares preservam categorias obrigatórias. | Separar o gate operacional do consentimento do cliente. Mesmo que um worker tenha credencial/capacidade, ele não recebe autorização sem grant vigente e específico. |
| Claude Code | Regras `deny`, `ask` e `allow` são aplicadas pelo runtime, não pelo modelo, com precedência `deny > ask > allow`; regras podem ser vinculadas ao tool call e aos seus parâmetros; sandbox e permissões se acumulam. | Classificar ações por contrato tipado no runtime, vincular a aprovação aos parâmetros exatos e manter bloqueios de política acima de qualquer consentimento. O modelo interpreta a fala, mas não abre o gate sozinho. |

Padrão comum aproveitado:

1. identificar uma ação concreta antes de executá-la;
2. explicar o efeito e o risco de forma compreensível;
3. congelar o alvo e os parâmetros daquela tentativa;
4. aguardar uma decisão ligada à proposta congelada;
5. negar por silêncio, expiração, recusa, conflito real de contexto ou mudança de escopo;
6. consumir a autorização uma única vez;
7. confirmar sucesso apenas depois de verificar o efeito.

Diferença deliberada: ferramentas de desenvolvimento podem oferecer “permitir
durante a sessão” ou “não perguntar novamente”. A Maya **não oferecerá esses
atalhos** para reservas, cancelamentos, alterações, cobranças ou reembolsos.
Cada decisão comercial crítica exige uma autorização nova e one-shot para a
versão exata apresentada ao cliente.

## Arquitetura escolhida

### 0. Trava geral de ações críticas

A solução não será apenas um prompt. Será um gate de aplicação obrigatório,
executado depois da interpretação conversacional e antes de qualquer outbox de
efeito.

#### 0.1 Política tipada com precedência

Toda intenção material será reduzida a um `CriticalActionKind` e avaliada pelo
runtime em três classes:

- `allow`: consultas, explicações, simulações e coleta de dados que não criam,
  alteram, cancelam, cobram nem compartilham dados externamente;
- `ask`: reservar hospedagem, reservar passeio, reservar pacote, iniciar uma
  solicitação de pagamento, alterar uma reserva, cancelar, cobrar/capturar,
  reembolsar ou compartilhar dados em um handoff não solicitado;
- `deny`: ação fora da política/capacidade, concessão sem handoff, binding
  vencido ou divergente, versão antiga, valor alterado, identidade incompatível
  ou operação terminal em revisão manual.

A precedência será `deny > ask > allow`. O modelo não pode reclassificar um
`deny` como permitido nem tornar um `ask` executável apenas escrevendo uma
resposta persuasiva.

O conjunto inicial obrigatório é:

- `reserve_lodging`;
- `book_activity`;
- `book_package`;
- `initiate_payment`;
- `modify_reservation`;
- `cancel_reservation`;
- `charge_or_capture` e `refund` como capacidades futuras sempre separadas;
- `share_handoff_data`, quando o próprio cliente ainda não pediu contato
  humano nem autorizou o compartilhamento.

O registry pode conhecer uma ação sem habilitar sua capacidade. Cancelamento,
alteração, captura e reembolso permanecem `deny` enquanto não houver contratos,
providers e read-back implementados; quando forem habilitados, passam
obrigatoriamente por `ask`. Esta entrega implementa a fundação geral e integra
primeiro os fluxos de reserva + iniciação de pagamento já existentes.

#### 0.2 Proposta congelada antes da pergunta

Ao chegar a uma ação `ask`, o pai cria um `CriticalActionProposal` privado e
durável. O artefato contém, de forma canônica:

- `proposal_id` opaco e `proposal_digest`;
- lead/conversa e ação ou conjunto fechado de ações;
- `draft_id`, versão e `subject_signature` do resumo;
- produto/quarto, datas, participantes e forma de pagamento;
- moeda, total final, taxa e valor do sinal, quando aplicáveis;
- bindings privados autenticados do provider e do perfil;
- efeitos exatos que serão permitidos;
- `created_at`, `expires_at` e status de ciclo de vida.

A janela de aprovação é limitada por um TTL próprio — inicialmente 30 minutos,
adequado à conversa assíncrona do WhatsApp. A evidência/oferta do provider pode
vencer antes: isso não permite usar dados antigos, pois a releitura fresca segue
obrigatória antes do comando. Se qualquer termo material mudar, a proposta vira
`superseded` e um novo resumo é apresentado. A proposta começa como `pending` e
só pode ir para `approved`, `denied`, `expired`, `superseded`, `consumed` ou
`manual_review`.

O cliente vê somente a projeção pública natural. IDs, hashes, nomes de
providers, idempotency keys e bindings permanecem privados.

#### 0.3 Pergunta semelhante à de uma ferramenta de IA, mas natural

A pergunta deve dizer **o que a Maya fará em seguida**, não apenas “confirma?”.
Para o caso Bókun:

> Só para confirmar: vou reservar o Roteiro dos 4Ps em 18/11/2026 para 1 pessoa, pelo total final de R$ 334,95 já com a taxa, e depois gerar o link do sinal de R$ 66,99 no cartão. Posso fazer essa reserva?

Para alteração, a Maya mostra antes/depois e eventual diferença de valor. Para
cancelamento, informa qual reserva será cancelada e a consequência financeira
conhecida. Para cobrança/captura ou reembolso futuro, mostra valor, moeda e
destino e pede uma aprovação própria — essas ações nunca ficam implícitas numa
aprovação anterior de reserva.

Gerar um link de pagamento não debita o cliente. Esse passo pode fazer parte do
mesmo conjunto fechado da reserva se estiver explicitamente descrito. O clique
e o pagamento continuam sob controle do cliente. Uma cobrança automática,
captura ou reembolso é outra ação crítica e exige nova proposta.

#### 0.4 Aprovação natural, mas ligada à proposta atual

O modelo recebe a projeção pública autenticada da proposta pendente e interpreta
a mensagem inteira. Quando existe exatamente uma proposta autenticada, pendente,
vigente e inalterada, a própria pergunta apresentada pela Maya fornece o objeto
da confirmação. O cliente não precisa repetir os dados que acabou de receber.

São confirmações válidas nesse estado, entre outras formulações semanticamente
equivalentes:

- “Sim”;
- “Pode reservar”;
- “Pode sim”;
- “Confirmado”;
- “Isso mesmo”;
- “Correto”;
- “Pode seguir”;
- “Sim, por favor”;
- “Pode reservar esse passeio e gerar o link”.

Esses exemplos não formam uma allowlist e não serão reconhecidos por regex,
substring ou correspondência lexical. O modelo classifica a fala no contexto da
pergunta pendente; o runtime concede autoridade somente pela proposta
autenticada, versão, action scope, prazo, releitura e demais gates mecânicos.

Uma conversa informativa intermediária não cancela a proposta. Enquanto ela não
expirar, não for substituída e não receber recusa ou mudança material, uma
confirmação curta posterior ainda pode consumi-la. Isso evita que a Maya repita
o mesmo resumo e peça consentimento novamente sem necessidade.

Continuam sem autorizar:

- “Sim, mas agora são duas pessoas” → `adjust`, nunca aprovação;
- “Pode, só que para outra data” → `adjust`;
- “Talvez”, “acho que sim” ou outra resposta realmente incerta → `inform`;
- pergunta sobre taxa, sinal ou política → `inform`, nunca aprovação;
- aceite sem proposta pendente, de proposta expirada ou de resumo superado →
  rejeitado pelo runtime;
- qualquer retorno que misture `confirm` com fatos materiais novos → rejeitado
  pelo contrato e tratado como mudança, nunca como grant.

Botões como “Confirmar reserva”, “Quero alterar” e “Não reservar” poderão
transportar futuramente um callback assinado do ManyChat. Esta entrega não aceita
esse caminho porque o inbound atual ainda não carrega prova autenticada do
clique. Texto natural contextual é o único caminho habilitado agora.

Quando houver `approve_action`, o pai cria um `CriticalActionGrant` one-shot,
autenticado e vinculado ao `proposal_digest`, à versão, ao evento do cliente e
ao prazo. O texto do modelo não é o grant.

#### 0.5 Consumo atômico e sem autorização ampla

Imediatamente antes do write:

1. verificar que a proposta é a única pendente naquele escopo e ainda está
   vigente;
2. verificar conversa/lead, versão, assinatura, perfil e ação exatos;
3. refazer o read do provider;
4. comparar todos os termos materiais e bindings;
5. consumir o grant na mesma transação que grava o comando e a relay outbox;
6. transportar apenas o comando canônico pelo strict dispatch fence;
7. executar uma vez com idempotência/fencing;
8. fazer read-back do provider.

O grant não pode autorizar uma ação adicional, outro valor, outro provider,
outro cliente nem uma segunda tentativa. Repetir a mesma mensagem depois do
consumo retorna o receipt/idempotency outcome já existente e não cria outro
efeito.

Pacotes e os passos inseparáveis já descritos podem usar uma única proposta
composta. O resumo deve enumerar todos os efeitos. A autorização é all-or-none
quanto ao **escopo consentido**; isso não promete atomicidade entre providers.
Se um provider concluir e outro ficar incerto, o conjunto vai para reconciliação
e revisão manual, sem retry cego. Qualquer componente novo exige nova versão e
nova pergunta.

#### 0.6 Expiração, negação, mudança e incerteza

- silêncio e timeout significam `expired`, nunca consentimento;
- negação explícita vira `denied`; a Maya não repete nem reformula a mesma
  tentativa para contornar a decisão;
- qualquer mudança de produto, data, quarto, pessoas, preço, taxa, sinal,
  moeda, pagamento ou conjunto de efeitos vira `superseded`;
- mensagens concorrentes ou fora de ordem só podem atingir a proposta vigente;
- se o provider mudar um termo no reread, nenhum write ocorre; a Maya apresenta
  uma proposta nova;
- resultado incerto após o write vira `manual_review`, sem retry e sem pedir ao
  cliente que “confirme de novo” uma ação que talvez já tenha acontecido;
- na ausência de um canal humano apto a receber a confirmação, o gate falha
  fechado.

#### 0.7 Comunicação depois da aprovação

Ao consumir a aprovação, antes do outcome:

> Perfeito — vou processar sua reserva agora.

Isso é acknowledgement, não afirmação de sucesso. Somente após read-back:

> Pronto, seu passeio foi reservado. Este é o link para pagar o sinal de R$ 66,99: …

Falha confirmada, incerteza e revisão manual têm textos próprios e nunca são
apresentadas como reserva concluída.

#### 0.8 Mapeamento sobre as primitivas V2 existentes

Não será criada uma segunda máquina de estados para reservas:

- `AwaitingConfirmationState(draft, summary)` materializa a proposta crítica
  congelada;
- `CommercialDraft.subject_signature` já autentica componentes, cliente e
  termos econômicos;
- `SummaryPresented` já liga `draft_id`, versão, assinatura, mensagem e
  instante de apresentação;
- `ConfirmationRecord` materializa o grant one-shot;
- `ExecutionQueuedState.__post_init__` já valida draft, resumo, confirmação e
  comando como um único conjunto;
- `commit_turn_v8` já grava state, comando, relay e receipt atomicamente;
- `ReservationCommand` já deriva identidade/idempotency key do subject
  canônico;
- `_confirmation_read_requests` já força releitura derivada do draft antes do
  comando;
- o strict dispatch fence continua enviando somente `dumps_command(command)`.

O protocolo novo endurece essas primitivas em vez de duplicá-las:

1. cria uma projeção pública fechada `PendingCriticalActionContext` para o
   child interpretar a resposta;
2. deriva `proposal_digest` por domain separation do action scope, draft,
   versão, assinatura, resumo público e prazo;
3. exige no retorno tipado `confirmed_summary_version`, action scope e uma
   `approval_basis` fechada (`contextual_reference` nesta entrega);
4. trata `SummaryPresented.presented_at + approval_ttl` como prazo derivado da
   proposta, sem nova tabela; o TTL inicial será configurável e testado, e a
   releitura do provider continua obrigatória independentemente dele;
5. grava `ConfirmationRecord` e muda diretamente para `ExecutionQueuedState`
   na mesma transação do comando, de modo que não exista uma janela durável de
   “aprovado mas ainda não enfileirado”;
6. usa o receipt existente para replay, consumo único e prova de auditoria.

Por compatibilidade e YAGNI, `CriticalActionProposal` e
`CriticalActionGrant` são nomes do protocolo geral. No fluxo de reserva, eles
são materializados pelas classes acima. Novas tabelas só serão justificadas
quando uma futura ação crítica não couber nessa máquina de estados.

### 1. Contexto público autenticado do resumo pendente

Adicionar ao contrato `ModelRequest` uma projeção fechada opcional do resumo vigente. Ela será derivada exclusivamente pelo pai quando o `BoundaryState.workflow` for exatamente `AwaitingConfirmationState`.

Campos permitidos:

- versão do resumo;
- tipo de serviço;
- componentes públicos com nome, datas, quantidade de pessoas, total e moeda;
- forma de pagamento em enum canônico;
- texto público já apresentado.

A projeção não conterá `offer_id`, `product_id`, IDs de provider, hashes, binding privado, perfil ou dados pessoais.

O adapter serializará esse contexto no payload atual do child. O prompt instruirá a Maya a interpretar a mensagem inteira contra esse resumo: aceitação inequívoca, curta ou detalhada → `intent=confirm` com a versão fornecida; dúvida real, recusa ou mudança → `inform`/`adjust`.

### 2. Autorização permanece determinística

A nova projeção ajuda somente na interpretação conversacional. Ela não autoriza a reserva.

O caminho produtivo continuará exigindo:

- `AwaitingConfirmationState` atual;
- `intent=confirm` tipado pelo modelo;
- versão idêntica à versão pendente;
- perfil compatível;
- releitura fresca derivada do draft;
- oferta, preço, pessoas e binding idênticos;
- criação de exatamente um comando durável e workers fenced.

Nenhum texto abre o gate sem uma proposta autenticada pendente. Com exatamente
uma proposta vigente e inalterada, uma confirmação curta pode ser suficiente,
pois a autoridade vem do estado e não das palavras isoladas.

### 3. Resumo natural determinístico

O reducer renderizará o resumo a partir do draft autenticado, sem confiar no texto do modelo.

Para o cenário Bókun em português, formato esperado:

> Só para confirmar: Roteiro dos 4Ps em 18/11/2026 para 1 pessoa, total de R$ 334,95 já com a taxa. O sinal no cartão será de R$ 66,99. Posso reservar?

Regras:

- moeda e data no locale do lead;
- `cartão`, nunca `stripe`;
- valor final Bókun, nunca subtotal;
- para agência + Stripe, sinal de 20% calculado com `Decimal` e `ROUND_HALF_UP` sobre o total confirmado;
- singular/plural natural;
- pacote e hospedagem conservam seus próprios componentes e políticas.

### 4. Acknowledgement seguro

Após o commit do comando, mas antes do resultado do provider:

> Perfeito — vou processar sua reserva agora.

O texto não afirma que a reserva já existe. A confirmação definitiva e o link continuam sendo projetados apenas depois dos outcomes `effect_confirmed` e do Payment Link confirmado.

## Tratamento de mudanças e ambiguidades

- Se o lead mudar data, passeio, pessoas, pagamento ou qualquer termo material, o modelo deve retornar `adjust`; o resumo anterior não é confirmado.
- Se a resposta expressar incerteza real, condição, pergunta ou conflito com a proposta, não há comando e a Maya pede esclarecimento curto somente quando necessário.
- Uma afirmação curta não é ambígua por ser curta quando há exatamente uma proposta pendente e vigente; “Sim”, “pode reservar”, “pode sim”, “confirmado” e “isso mesmo” devem confirmar sem nova pergunta.
- Uma conversa informativa intermediária não revoga a proposta nem torna uma afirmação curta inválida; expiração, recusa, substituição ou mudança material revogam.
- Se o total mudar na releitura, o binding falha fechado e um novo resumo deve ser apresentado.
- Handoff continua tendo precedência terminal.

## Testes

### RED/GREEN unitário

1. `ModelRequest` transporta somente a projeção pública fechada do resumo.
2. A projeção omite IDs, hashes, binding e PII.
3. O wire do Hermes child contém o resumo pendente e sua versão.
4. O resumo Bókun PT-BR usa `R$ 334,95`, taxa incluída, data, 1 pessoa, `cartão` e sinal `R$ 66,99`; não contém `BRL`, `stripe`, IDs ou hashes.
5. Confirmação válida produz acknowledgement natural e um comando; `adjust`/ambiguidade não produz comando.
6. Resumo alterado/versionado mantém o fail-closed existente.
7. A política tipada aplica `deny > ask > allow` fora do modelo.
8. Toda ação crítica é bloqueada sem `CriticalActionGrant` correspondente.
9. Um grant autoriza somente o digest/versão/efeitos exatos e é consumido uma
   vez atomicamente.
10. Grant vencido, negado, superado, de outra conversa ou já consumido não
    cria comando.
11. “Sim”, “pode reservar”, “pode sim”, “confirmado” e “isso mesmo” abrem o gate
    somente quando existe exatamente uma proposta autenticada, vigente e
    inalterada; os mesmos textos sem proposta pendente não autorizam nada.
12. “Sim, mas…” com mudança material invalida a aprovação.
13. Alterar, cancelar, cobrar/capturar e reembolsar exigem suas próprias
    propostas e resumos de consequência.
14. Uma aprovação de reserva + link não autoriza cobrança automática.
15. Timeout, ausência de canal humano, negação e resultado incerto permanecem
    fail-closed e não produzem retry cego.
16. Corridas entre duas versões só permitem consumir a proposta vigente.
17. Reentrega/replay do mesmo evento retorna o receipt existente sem novo
    comando.

### Sandbox conversacional

Executar conversa ManyChat-shaped com modelo real, estado novo e os efeitos mecanicamente bloqueados:

1. disponibilidade e total final;
2. dados e cartão/sinal;
3. resumo natural;
4. em estados novos e isolados, o lead responde alternadamente “Sim”, “Pode
   reservar”, “Pode sim”, “Confirmado”, “Isso mesmo” e uma formulação longa;
5. Maya retorna confirmação tipada no primeiro turno de cada aceitação, sem
   repetir resumo nem exigir que o cliente recite os termos;
6. runtime prepara no máximo um comando, mas nenhum worker/provider write/Stripe/ManyChat/handoff é executado.
7. registrar a proposta, a interpretação, o grant e a tentativa de consumo em
   evidência sanitizada, provando que o modelo sozinho não abriu o gate.

Executar também casos negativos em estados isolados: afirmação curta sem
proposta pendente, proposta expirada, proposta substituída, “talvez”, pergunta,
recusa, mudança material e `confirm` acompanhado de fatos novos. Todos devem
produzir zero comandos.

Preservar transcript sanitizado e contadores de efeitos externos iguais a zero.

## Critérios de aceite

- Uma única confirmação natural do resumo inalterado é suficiente, inclusive
  quando o cliente responde apenas “Sim”, “pode reservar”, “pode sim”,
  “confirmado” ou “isso mesmo”.
- Não há frase mágica nem autorização lexical.
- A proposta continua confirmável após um desvio informativo enquanto estiver
  pendente, vigente e inalterada; a Maya não repete confirmação sem necessidade.
- Toda ação crítica é interceptada por política tipada antes da outbox de
  efeito; o prompt nunca é a barreira única.
- A aprovação é one-shot e não existe opção “durante a sessão” ou “sempre” para
  transações do cliente.
- O cliente aprova o efeito concreto e a versão exata; mudança material exige
  uma nova pergunta.
- O resumo não expõe termos internos.
- Nenhum efeito é afirmado antes do read-back.
- Testes focados, sandbox real effect-denied e regressão completa passam no mesmo HEAD.
- Git permanece limpo exceto pelo `uv.lock` preexistente e intocado.

## Fontes consultadas

- Hermes Agent — configuração de Smart Approvals:
  https://hermes-agent.nousresearch.com/docs/user-guide/configuration
- Hermes Agent — `/approve` e `/deny`:
  https://hermes-agent.nousresearch.com/docs/reference/slash-commands
- OpenAI Codex — Agent approvals & security:
  https://learn.chatgpt.com/docs/agent-approvals-security
- Anthropic Claude Code — Configure permissions:
  https://code.claude.com/docs/en/permissions
- Anthropic Claude Code — Security and trust / sandboxing:
  https://code.claude.com/docs/en/security
