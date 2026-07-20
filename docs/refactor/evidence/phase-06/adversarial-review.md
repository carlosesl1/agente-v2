# Revisão adversarial inline — Fase 6

Escopo: workflows de handoff/payment, claims, settlement, outboxes, properties,
faults/restarts/contention, mutations e closeout local capability-free.

Estado: re-gate anterior invalidado; segundo ciclo fecha execução de processos
no oracle de pureza e aguarda novo gate terminal. Properties permanecem em
857,092 s para budget de 900 s. Rollout `NO-GO`;
`phase7_started=false`; publicação e CI remoto ainda não alegados.

## Perguntas vinculantes

### 1. Handoff pode depender de e-mail interno?

Não. Fila, estado e acknowledgement público são required; e-mail interno é
optional/disabled e falha sem bloquear os efeitos requeridos.

### 2. Uma confirmação/resumo antigo reaparece após handoff terminal?

Não. A precedência terminal suprime confirmação e missing slots anteriores; o
mutante material de precedência é morto pelo teste governante.

### 3. Payment nasce de reserva não confirmada?

Não. `ConfirmedReservationAnchor` recompõe outcome canônico e exige
`EFFECT_CONFIRMED`; todos os outros outcomes são rejeitados.

### 4. Pix, Wise e Stripe compartilham um schema genérico permissivo?

Não. Evidências, trust profiles, fingerprints e validações são fechados por
método; amount/currency/receiver/target permanecem vinculados.

### 5. Um mesmo comprovante/evento pode pagar dois targets?

Não. A identidade global da evidência é claimada independentemente de caller,
unidade ou target; conflito divergente falha fechado.

### 6. Settlement pode consumir segundo slot após crash?

Não. Fence e slot são permanentes. Pré-fence pode requeue; pós-fence vai a
manual review e nunca recebe dispatch automático.

### 7. Outbox consegue repetir settlement ou escrever o ledger financeiro?

Não. Payment outbox só gerencia lease/receipt/delivery. Validator percorre o
call graph e rejeita writes outbox→ledger e ownership cross-workflow.

### 8. Contenção consegue falso-green por loser de lock?

Não. BUSY/LOCKED é retryable store unavailability; domínio conflitante não é
retry. O oracle compara winner/token/owner do child com SQLite reaberto.

### 9. Mutation runner aceita loader/import/timeout como kill?

Não. Baseline verde, target count 1 e protocolo único são obrigatórios;
loader/import/syntax/timeout não matam. Erros comportamentais, inclusive em
`subTest`, permanecem kills materiais.

### 10. A cópia mutante é restaurada entre casos?

Sim. Bytes originais são restaurados em `finally`; uma mutação reversa que
remove a restauração é morta causalmente após timeout, antes do mutante seguinte.

### 11. Envelopes vazios ou catálogos reduzidos passam?

Não. O validator fixa 16 modos, 27 faults, 12 restart points, quatro domínios e
12 mutantes sem importar runners; reconstrói rows, identidades, tipos exatos e
agregados.

### 12. PostgreSQL, rede ou capabilities live foram executados?

Não. PostgreSQL permanece contrato DDL estático. SQLite é local/temporário.
Scans e manifests rejeitam bancos/logs, imports/calls externos e execução de
processos dentro do package.

### 13. O CI reduz workloads para caber no timeout?

Não. Suíte, 20.000 properties, 2.000 restarts, 50 rounds e 12 mutantes rodam em
jobs independentes de 15 minutos; o aggregate gate depende dos cinco jobs sem
`if: always()`.

### 14. A Fase 7 ou rollout pode abrir automaticamente?

Não. A publicação da Fase 6 não inicia a Fase 7. O rollout continua `NO-GO` e
`phase7_started=false` até autorização e gates das fases posteriores.
