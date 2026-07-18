# Evidências da refatoração

Este diretório contém somente evidências técnicas sanitizadas.

## Regras

Permitido:

- commits, branches e URLs públicas de repositório;
- status Git e diff stats sem conteúdo;
- image digest e hashes SHA-256;
- configuração não secreta;
- contagens, métricas e exit codes.

Proibido:

- credenciais, tokens e arquivos `.env`;
- PII e identificadores de contatos;
- mensagens reais;
- payloads brutos de providers;
- bancos, logs e comprovantes;
- respostas completas de endpoints que possam conter dados operacionais.

## Fase 0

`phase-00/baseline-manifest.json` é o manifesto principal. `phase-00/validation-result.json` registra os checks executados. `SHA256SUMS` protege a integridade dos arquivos de evidência da fase.

Para verificar:

```bash
python3 scripts/validate_phase0.py
```
