# Reservation domain — Phase 2

Pacote Python puro que implementa a máquina de estados comercial do Agente v2.

## Propriedades

- dataclasses imutáveis (`frozen`, `slots`);
- estados e eventos discriminados por tags estáveis;
- reducer total e sem efeitos externos;
- seleção por `offer_id` opaco;
- evidência positiva e temporal antes da seleção/draft;
- assinatura SHA-256 canônica do assunto executável;
- customer facts fechados e cobertos pela assinatura/payload;
- uma confirmação posterior ao resumo;
- no máximo um `ReservationCommand` por workflow;
- fingerprint por evento para separar duplicata exata de colisão de ID;
- outcome monotônico, com `called_unknown` preservado;
- serializer JSON estrito com `schema_version=1`, chaves únicas e escalares
  canônicos;
- property runner determinístico com oráculo positivo/negativo e classes de
  cobertura explícitas.

## Módulos

- `types.py` — value objects, estados, eventos, comando e outcome;
- `signature.py` — projeção canônica e álgebra de certainty;
- `reducer.py` — transições e matriz estado/evento;
- `serialization.py` — round-trip estrito e versionado;
- `properties.py` — sequências arbitrárias, duplicadas e fora de ordem.

## Ausências intencionais

O pacote não importa nem conhece FastAPI, Hermes, ManyChat, plugin, banco,
filesystem, subprocesso, rede, provider, ledger, worker ou outbox. Um comando é
somente dado imutável; a execução pertence à Fase 5.

## Validação

```bash
python3 -m unittest discover -s tests -v
python3 scripts/run_phase2_properties.py --sequences 100000 --max-events 20
# apenas para feedback rápido, sem valor de gate:
python3 scripts/run_phase2_properties.py --sequences 2000 --max-events 20 --smoke
python3 scripts/validate_phase2.py
```
