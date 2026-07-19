# `reservation_confirmation`

Boundary puro da Fase 4 para apresentação e classificação de confirmação.

## Regras

- não executa I/O, rede, provider, LLM ou entrega;
- não persiste mensagem bruta;
- `DecisionCandidate` não contém versão, assinatura, oferta, provider ou operação;
- `RenderedSummary` declara `claim_status="none"` e `private_fields=()`;
- IDs e hashes são identidades determinísticas, não autenticação;
- somente o reducer do domínio pode construir `ReservationCommand`.

Renderer, preparation, classifier e binding serão adicionados em ciclos TDD
separados. A API pública permanece fechada e tipada.
