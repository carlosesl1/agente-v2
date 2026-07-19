# Revisão adversarial — Fase 4

Data: 2026-07-19
Escopo: resumo único, classificação, trusted binding, ajuste e autorização.

## Questões testadas

1. **A classificação consegue escolher o target comercial?** Não. O DTO do
   classificador contém somente decisão, identidade/versão do classificador,
   confiança e evidence codes. Versão e assinatura são recompostas do estado.
2. **Texto contextual sem resumo vigente autoriza?** Não. Contexto ausente produz
   `AMBIGUOUS` sem `ConfirmationReceived`.
3. **Hash, locale ou outbox adulterado autoriza?** Não. O binding renderiza de
   novo e recompõe os IDs do artefato persistido.
4. **Confirmação no mesmo instante do resumo autoriza?** Não. A ordem é
   estritamente posterior.
5. **`ADJUST` deixa o resumo armado?** Não. Move para `awaiting_adjustment`, onde
   `confirmation_received` não possui handler.
6. **Ajuste sem mudança cria versão artificial?** Não. É rejeitado por assinatura
   semântica idêntica.
7. **Duplicata ou mesmo source ID com payload divergente duplica comando?** Não.
   Duplicata é ignorada; conflito falha fechado.
8. **Consulta Bókun sem horário atravessa o domínio?** Sim. `start_time=None` é
   filtro aberto; horário informado continua exigindo igualdade.
9. **Resumo pode vazar IDs internos?** O renderer omite e rejeita qualquer valor
   privado que coincida com conteúdo público.
10. **Falha/exceção de classificador autoriza?** Não. Produz ambiguidade sem evento.
11. **“Sim, talvez” ou uma confirmação em forma de pergunta autoriza?** Não.
    Marcadores positivos fora do conjunto fechado e qualquer `?` são ambíguos.

## Controles anti-falso-verde

- corpus sintético PT/EN cobre seis categorias e contexto ausente;
- dois replays começam em workflow vazio e atravessam adapters in-memory reais;
- 50 mil properties partem de `new_workflow` e atravessam os adapters reais
  in-memory, além de exercitar direções positivas e negativas;
- 19 mutantes críticos são executados somente em cópias temporárias;
- manifestos e checksums são regeneráveis no CI;
- scans AST proíbem I/O, rede e runtimes externos no package;
- regressões formais das Fases 0–3 permanecem gates de entrada.

## Remediação observada no CI de closeout

O primeiro CI do closeout (`f1e4bfb...`, run `29673240863`) revelou que o
mutante de sinais mistos dependia da ordem de iteração de `set`: podia morrer ou
sobreviver conforme o hash seed. O catálogo foi congelado novamente, o mutante
passou a forçar `ACCEPT` deterministicamente e um teste regressivo exige a mesma
morte sob `PYTHONHASHSEED` 0, 1 e 17. O gate completo voltou a 19/19.

## Limites e conclusão

Não houve LLM, Hermes, ManyChat, provider live, write, banco, fila, worker ou
deploy. A evidência prova contrato determinístico, não equivalência live.
Nenhum bloqueador conhecido permanece no escopo da Fase 4; rollout comercial
continua **NO-GO**.
