# Estratégia de validação e rollout

## Regra principal

Testes com LLM são evidência estatística de interpretação. Não substituem invariantes determinísticos de autorização e exactly-once.

## Pirâmide de validação

### 1. Reducer e tipos

- tabela completa estado/evento;
- unitários;
- property-based com no mínimo 100 mil sequências;
- eventos duplicados, concorrentes, fora de ordem e atrasados;
- metamorphic tests por campo econômico.

**Gate:** nenhum write prematuro; no máximo um comando por versão.

### 2. Contratos de boundary

Exercitar:

```text
schema → plugin → contexto → dispatch → executor → adapter
```

Substituir apenas o HTTP final quando necessário. Validar:

- payload exato;
- sanitização;
- ausência de aliases;
- estado ausente fail-closed;
- outcome tipado.

### 3. Persistência, ledger e outbox

Usar o mesmo schema de produção, com:

- processo reiniciado;
- corrida multiprocesso;
- unique constraints;
- lease expirado;
- claim abandonado;
- backlog e replay.

### 4. Fault injection

Falhar em cada fronteira:

1. antes de persistir evento;
2. depois do evento e antes do comando;
3. depois do comando e antes do claim;
4. depois do claim e antes do socket;
5. depois do socket com resposta perdida;
6. depois do provider e antes do outcome;
7. depois do outcome e antes do outbox;
8. durante `setCustomField`;
9. durante `sendFlow`.

Invariantes:

```text
provider_calls <= 1
falha da outbox não repete provider
called_unknown nunca tem retry automático
```

### 5. Replay de conversas

Criar corpus anonimizado de conversas reais e manter um holdout. Preservar:

- mensagens curtas;
- dados enviados em turnos separados;
- correções;
- idiomas;
- pacote;
- dúvidas;
- duplicatas;
- mudança de ideia.

Provider results são gravados/sanitizados para todos os modelos receberem a mesma realidade.

### 6. Matriz de modelos

- fallbacks desabilitados;
- modelo/provider/reasoning atestados;
- repeats balanceados;
- todos os resultados no denominador;
- revisão cega;
- nunca rerodar até ficar verde.

### 7. Dark canary

Usar a própria imagem candidata:

```text
mode=shadow
provider_reads_real=true
provider_writes_enabled=false
delivery_enabled=false
```

Executar pelo menos três fluxos completos. Esperado:

- lookup real positivo;
- resumo sem command/provider claim;
- confirmação produz um comando bloqueado pelo gate;
- `provider_call_executed=false`;
- zero mensagem pública;
- zero efeito comercial.

### 8. Ingress ManyChat real

Contato autorizado, endpoint e debounce reais, com outbound/write fechados. Provar:

- webhook novo;
- sessão nova;
- profile/modelo esperado;
- nenhuma rota legada;
- estado isolado.

### 9. Canary E2E real

Somente com autorização explícita para:

- um subscriber;
- um workflow;
- um provider;
- uma reserva;
- um período.

Passos:

1. conversa natural;
2. resumo;
3. uma confirmação;
4. um comando;
5. um dispatch;
6. read-back de uma reserva;
7. uma mensagem outbox;
8. redelivery do webhook com zero novos efeitos;
9. auditoria/cancelamento planejado.

Falha significa NO-GO. Não repetir comercialmente até nova RCA.

## Identidade de artefato

O manifesto da release deve conter:

- Git commit limpo;
- image digest OCI;
- `uv.lock` hash;
- hashes de HERMES, SOUL, config, skills e plugin;
- versão Hermes;
- model/provider/reasoning;
- versão do schema;
- fingerprint de env não secreto.

Canary e rollout usam o mesmo digest, sem rebuild.

## Rollout

```text
1% / até 100 conversas / 24h
→ 5% / até 300 / 24h
→ 25% / até 1.000 / 48h
→ 100%
```

Os números podem ser reduzidos se o volume real for menor; o tempo mínimo de observação permanece.

## Stop conditions

Rollback imediato diante de:

- write antes de confirmação;
- mais de um comando/tentativa;
- provider incerto com retry automático;
- drift de digest/hash;
- ledger indisponível;
- estado contraditório;
- promessa pública sem evidência;
- outbox sem recuperação;
- aumento anormal de handoff/timeout.

## Evidência por execução

Sem PII/texto bruto:

- release/image digest;
- hash do lead;
- message/session IDs internos;
- estado anterior/posterior;
- draft version;
- signature hash;
- command/attempt/idempotency;
- ledger status;
- outcome;
- outbox status e idade;
- resultado ManyChat.
