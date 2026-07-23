# Fast Track — Agente completo até pagamento e finalização — Design

- Data: 2026-07-23
- Branch: `phase8-shadow-canary-rollout`
- Base funcional do sandbox: `b8fdd9280cc289d8bf53573d3a987bddb0478ae7`
- Status: desenho recomendado adotado após timeout de confirmação, aguardando revisão escrita de Carlos
- Rollout: `NO-GO`

## 1. Objetivo

Entregar um agente utilizável pelo cliente no WhatsApp que cuide do processo comercial completo, desde o primeiro atendimento até reserva, pagamento exigido pela política comercial, comunicação pós-pagamento e encerramento do atendimento.

O fluxo feliz não depende de humano. Handoff é uma saída excepcional para incerteza, ambiguidade ou decisão comercial que o sistema não pode resolver com segurança.

## 2. Resultado de produto

O agente deve, em uma conversa natural:

1. identificar idioma, serviço e intenção;
2. reutilizar os dados confiáveis do perfil ManyChat, sem pedir novamente telefone já disponível;
3. coletar somente os dados humanos faltantes;
4. consultar disponibilidade, preço e requisitos reais;
5. apresentar opções públicas sem IDs internos;
6. vincular a escolha a uma opção canônica retornada pelo provider;
7. apresentar um único resumo e aceitar uma única confirmação natural posterior;
8. criar a reserva ou as reservas necessárias por comando durável;
9. oferecer e conduzir o pagamento aplicável;
10. reconhecer a evidência financeira conforme o método escolhido;
11. registrar a confirmação de pagamento no provider quando a política exigir;
12. executar comunicações e formulários pós-pagamento por outbox;
13. informar o resultado real ao cliente e encerrar o atendimento.

“Atendimento concluído” significa que os efeitos exigidos para aquele caso possuem receipts canônicos. Uma resposta otimista da Maya não conclui o processo.

## 3. Escopo comercial

A primeira versão completa cobre:

- hospedagem via Cloudbeds;
- passeios e atividades via Bókun;
- pacote combinado hospedagem + passeio;
- FAQ e informações operacionais pelo Cérebro/knowledge autorizado;
- qualificação do lead;
- reserva;
- Stripe;
- Wise;
- Pix com comprovante visual validado;
- mensagens e formulários pós-pagamento;
- finalização do atendimento no ManyChat.

Para pacote combinado, a conversa e a confirmação comercial são unificadas, mas reservas e obrigações financeiras permanecem separadas por unidade de negócio. Hostel e agência não compartilham conta Stripe, conta Wise, recebedor Pix, target ou claim financeiro. O cliente recebe orientação pública clara para cada obrigação aplicável.

## 4. Abordagem escolhida

### 4.1 V2 como controle; runtime atual como adaptador operacional

O Agente v2 será owner de:

- conversa e fatos canônicos;
- progresso comercial derivado;
- seleção vinculada a lookup positivo;
- resumo e confirmação;
- comandos duráveis;
- ledgers, claims e outboxes;
- autorização e estado de reservas/pagamentos;
- decisão de conclusão ou handoff.

O runtime `/home/ubuntu/chapada-leads-hermes` será reaproveitado inicialmente apenas atrás de adapters estreitos para:

- consultas Cloudbeds/Bókun;
- criação Cloudbeds/Bókun;
- geração de links Stripe por conta correta;
- recepção e verificação de webhooks Stripe/Wise;
- validação mecânica de comprovante Pix;
- confirmação de pagamento no provider;
- ManyChat e efeitos pós-pagamento.

Não haverá dispatcher genérico controlado pelo modelo. Cada adapter consome um comando tipado autorizado pelo kernel e devolve um outcome tipado com certeza de execução.

### 4.2 Alternativas rejeitadas

- Migrar todos os SDKs/providers para o repositório V2 antes do primeiro uso: mais limpo, porém lento e duplicaria integrações existentes.
- Melhorar apenas o agente live e deixar o V2 em sandbox: mais rápido no curtíssimo prazo, mas preservaria os dois control planes e as causas da refatoração.

## 5. Fluxo ponta a ponta

```text
WhatsApp / ManyChat
→ ingress autenticado e idempotente
→ normalização do evento e identidade do lead
→ Maya interpreta conversa e propõe fatos/intenção
→ kernel valida e persiste estado canônico
→ consultas reais somente leitura
→ runtime vincula escolha a opção canônica positiva
→ resumo determinístico + confirmação natural posterior
→ ReservationCommand durável
→ worker executa provider uma vez
→ reservation effect_confirmed
→ PaymentWorkflow por obrigação financeira
→ evidência/settlement do método escolhido
→ confirmação no provider quando aplicável
→ outboxes pós-pagamento e mensagem final
→ workflow completed
```

Nenhuma transação de banco permanece aberta durante chamada LLM ou provider.

## 6. Atendimento e consultas

### 6.1 Maya

A Maya interpreta linguagem natural, mantém contexto multi-turno, responde no idioma do lead e coleta dados humanos. Ela não escolhe IDs técnicos por texto livre, não calcula autorização e não declara efeitos como executados sem receipt.

### 6.2 Cérebro e informações de pagamento

Fatos e instruções oficiais de Pix continuam vindo da autoridade comportamental/knowledge configurada para esse assunto. O modelo não inventa chave, recebedor, desconto, prazo, sinal ou regra de parcelamento. Stripe nunca é apresentado como Pix.

### 6.3 Hospedagem

A consulta usa `cloudbeds_consultar_hospedagem_v2`. A seleção técnica usa a opção canônica positiva retornada pela consulta, com datas, ocupação, valor e IDs privados vinculados.

### 6.4 Passeios

A consulta Bókun é exclusivamente por `tour_product_id` canônico escolhido de catálogo interno estável. Nome livre do passeio nunca resolve ou autoriza o produto dentro do provider path. A reserva consome a opção exata retornada pelo lookup por ID.

### 6.5 Pacote

`PackageProgress` é derivado do estado e das evidências. A Maya não gerencia flags de máquina. A confirmação combinada é preparada quando hospedagem e passeio estão selecionados e completos. Uma confirmação natural autoriza os componentes pendentes; cada provider mantém comando, ledger e outcome próprios.

## 7. Reserva

Uma reserva só pode ser criada quando:

- existe lookup positivo e fresco;
- a seleção canônica corresponde a ID, datas/horário, composição, valor e moeda;
- dados humanos obrigatórios estão presentes;
- o resumo canônico foi persistido;
- uma confirmação natural posterior corresponde à mesma assinatura;
- não existe comando equivalente já confirmado ou em estado incerto;
- o gate específico do provider está habilitado para aquele estágio de rollout.

Cloudbeds usa `cloudbeds_criar_reserva_v2`; Bókun usa `bokun_agendar_passeio_v2`. A LLM não compõe IDs ou payloads técnicos finais. O worker deriva o payload da seleção canônica, executa no máximo uma vez e registra `not_called`, `called_no_effect`, `called_unknown` ou `effect_confirmed`.

Somente `effect_confirmed` abre o pagamento.

## 8. Pagamentos

A obrigação financeira vem da reserva, política comercial e unidade de negócio. Ela pode ser sinal, valor integral ou pagamento no check-in; não é necessariamente 100% do valor da viagem. Valor, moeda, recebedor, desconto e prazo nunca vêm de texto livre da Maya.

Trocar método sem alterar a economia não reabre a reserva. Taxa, desconto ou total diferente cria nova versão financeira e exige novo resumo/aceite financeiro, mas nunca outra reserva.

### 8.1 Stripe

1. O sistema gera link na conta Stripe da unidade correta.
2. A URL é entregue ao cliente sem vazar metadata privada.
3. O pagamento só é reconhecido após webhook assinado e idempotente.
4. Metadata precisa vincular exatamente a obrigação e o target.
5. Webhook duplicado não repete confirmação no provider nem mensagens.
6. Falha definitivamente anterior ao provider pode ser repetida; resultado incerto vai para revisão humana.

### 8.2 Wise

1. O sistema apresenta a conta e instruções corretas da unidade.
2. Registra expectativa vinculada a target, valor, moeda e prazo.
3. Somente crédito recebido por webhook com assinatura verificada pode confirmar settlement.
4. Crédito sem target fica `unmatched`; múltiplos candidatos ficam em revisão humana.
5. Claims globais impedem reutilização do mesmo crédito.
6. Confirmação no Cloudbeds/Bókun ocorre apenas após match exato e claim adquirido.

### 8.3 Pix

Carlos decidiu que a primeira versão pode aceitar comprovante Pix visual validado e continuar automaticamente, mesmo sem API bancária.

A validação exige, mecanicamente:

- unidade de negócio correta;
- valor exato;
- moeda correta;
- recebedor exatamente igual ao oficial;
- status de pagamento concluído;
- E2E/transaction ID válido e não placeholder;
- hash íntegro da evidência;
- claim global que impeça replay em outro target;
- ausência de ambiguidade.

O estado financeiro preserva a distinção:

- `visual_evidence_accepted=true`;
- `bank_settlement_confirmed=false`.

A mensagem pública pode dizer “comprovante validado e pagamento aceito para processamento”. Não pode dizer “pagamento confirmado pelo banco”. A regra comercial escolhida permite que essa evidência autorize a confirmação aplicável no provider e a finalização do atendimento.

Comprovante ilegível, divergente, ambíguo ou reutilizado não autoriza efeito e segue para correção ou handoff.

### 8.4 Hóspede estrangeiro

Quando a política vigente determinar que hóspede estrangeiro não paga antecipadamente, o agente conclui reserva e atendimento com obrigação `due_at_checkin`. Ele não gera Stripe nem exige Pix/Wise. Isso é conclusão válida do fluxo, não abandono de pagamento.

## 9. Pós-pagamento e finalização

Settlement e comunicação são separados. Após a evidência financeira aceita, o sistema:

1. registra o outcome financeiro e claim global;
2. confirma ou registra pagamento no provider quando exigido;
3. enfileira, de forma durável e idempotente, cada efeito pós-pagamento;
4. atualiza o estado pago/aceito do lead;
5. envia confirmação ao cliente pelo ManyChat;
6. envia formulário Bókun quando aplicável;
7. executa notificações internas opcionais sem bloquear o cliente;
8. entrega resumo final com referências públicas, serviço, datas e próximos passos;
9. marca o workflow como `completed`.

A falha de uma mensagem não repete settlement nem reserva. O worker da outbox faz retry apenas quando há certeza de que o envio não começou; entrega incerta exige revisão.

Uma mensagem nova após `completed` inicia follow-up ou nova intenção sem apagar o histórico concluído.

## 10. Handoff excepcional

Handoff só substitui automação quando existe:

- provider write com resultado incerto;
- pagamento/claim ambíguo;
- comprovante inválido que não pode ser corrigido na conversa;
- conflito de identidade, target, valor ou opção;
- indisponibilidade persistente sem retry seguro;
- pedido de desconto ou decisão comercial reservada a humano;
- requisito extraordinário sem contrato autorizado.

O handoff persiste fila e acknowledgement público. E-mail é opcional. Enquanto ativo, a IA não repete reserva, pagamento, confirmação ou mensagem.

## 11. Ingress e canal

O endpoint `/webhook/manychat` deve usar:

- autenticação configurada;
- idempotência por evento;
- debounce sem perder sequência;
- identidade canônica do lead;
- estado persistente isolado entre leads;
- allowlist durante canary;
- outbox para resposta pública;
- receipts de entrega.

O ManyChat continua sendo a integração WhatsApp. Não será criado um gateway direto Hermes→WhatsApp.

## 12. Segurança e invariantes

- LLM interpreta; kernel autoriza.
- Produto/provider somente por ID canônico interno; IDs não são pedidos nem exibidos ao lead.
- Uma opção só autoriza write quando vinculada a lookup positivo.
- Um resumo e uma confirmação natural; nunca duas confirmações idênticas.
- Reserva e settlement possuem ledgers separados.
- Cada método financeiro possui evidência e claim próprios.
- Pix, Wise e Stripe nunca entram pelo contrato uns dos outros.
- Nenhum retry automático após write potencialmente iniciado.
- Nenhum segredo, payload bruto, JSON interno, tool name ou enum interno chega ao cliente.
- Nenhum texto público otimista sobrevive a outcome bloqueado/incerto.
- Alteração econômica invalida somente a versão financeira, não recria reserva.
- Outbox de comunicação nunca executa settlement.

## 13. Falhas e recuperação

- Falha de modelo/protocolo: não persiste turno parcial; resposta segura e retry controlado.
- Falha de read: resposta conservadora sem inventar disponibilidade/preço.
- Falha antes de provider write: pode ser retryable quando comprovadamente sem efeito.
- Timeout/erro após início de write: `called_unknown`, sem retry, handoff/reconciliação.
- Falha pós-pagamento: obrigação permanece aceita; somente job de comunicação é repetido.
- Evento duplicado: replay idêntico é no-op; payload divergente é conflito.
- Restart: leases/fencing e journals recuperam trabalho sem repetir efeito.

## 14. Estratégia de entrega

A implantação será incremental, mas cada incremento pertence ao mesmo produto final:

1. composição local ManyChat-shaped com providers fake;
2. conversa com providers reais somente leitura;
3. contratos de reserva/pagamento com writes fechados;
4. canário de um provider write por workflow, com read-back e cleanup;
5. ManyChat allowlist com Carlos;
6. conversa humana completa por serviço;
7. ativação gradual de hospedagem, passeio e pacote;
8. rollout geral somente após gates e autorização.

Provider write, envio ManyChat e promoção de rollout são autorizações separadas. Aprovar este design não habilita nenhuma delas.

## 15. Critérios de aceite

### 15.1 Hospedagem + Stripe

Uma conversa ManyChat natural consulta Cloudbeds, seleciona quarto, confirma uma vez, cria uma reserva, entrega link Stripe, recebe webhook assinado, registra pagamento, envia confirmação e termina `completed` sem handoff.

### 15.2 Passeio + Pix

Uma conversa consulta Bókun por ID canônico, agenda a opção escolhida, apresenta Pix correto, recebe comprovante, valida valor/recebedor/status/E2E, impede replay, registra `visual_evidence_accepted` sem afirmar settlement bancário, executa a confirmação aplicável, envia formulário/confirmação e termina `completed`.

### 15.3 Pacote + Wise

Uma conversa seleciona hospedagem e passeio, apresenta um resumo combinado, aceita uma confirmação, cria os dois targets uma vez, conduz as obrigações por unidade de negócio, reconhece apenas créditos Wise assinados e vinculados, conclui os dois componentes e encerra o atendimento.

### 15.4 Exceções

Testes provam que lookup vencido, confirmação de outro resumo, cross-tool, comprovante Pix divergente/reutilizado, webhook duplicado, crédito Wise ambíguo, timeout de provider e falha de outbox não causam efeito duplicado nem falsa mensagem de sucesso.

### 15.5 Gate humano

Antes do rollout, Carlos executa conversas reais de hospedagem, passeio e pacote via ManyChat allowlisted. Aprovação do comportamento conversacional não substitui autorização de provider write nem rollout.

## 16. Fora do escopo da primeira entrega

- API bancária Pix;
- gateway WhatsApp direto fora do ManyChat;
- motor genérico/DSL de side effects;
- reescrita completa dos SDKs de provider dentro do V2;
- roteamento ou autorização por palavras isoladas;
- automação de decisão comercial excepcional, como desconto não previamente autorizado.
