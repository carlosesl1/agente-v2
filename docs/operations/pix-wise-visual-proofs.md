# Pix/Wise — aceitação documental e revisão posterior

## Estado e autoridade

Este código é opt-in. A presença dele em uma branch/imagem **não o ativa**.
O manifesto `ACTIVE_RUNTIME.json`, seu verificador e `/readyz` continuam sendo
a autoridade do ambiente. Não houve migração de estado nem edição dos bancos ativos.
V3 e o checkout legado estão fora deste trabalho.

Política aprovada: comprovante consistente autoriza uma baixa automática; a
revisão humana posterior é não bloqueante. Isso **não equivale a confirmação
bancária independente**, nem prova autenticidade bancária irrefutável da imagem.

## Caminho implementado

1. O adaptador busca o anexo HTTPS em um dos hosts explicitamente configurados,
   valida/decode a imagem e retém os bytes pelo SHA-256. Não segue redirecionamentos.
2. Os pixels, o evento de origem e o hash chegam ao **mesmo child Maya**, sem tools.
   V9 acrescenta `payment_proof` ao contrato conversacional V8. A observação não
   contém autorização, resultado de aprovação nem valores esperados inventados.
3. O serviço resolve cobrança e reserva pelo owner do lead e pelo comando
   confirmado. Valor/moeda são os **persistidos na instrução emitida**, não o total
   da reserva nem um recálculo usando a porcentagem vigente.
4. Compara método, valor recebido, moeda, identificador do destinatário, nome
   configurado do beneficiário, estado concluído e janela temporal. Pagador pode
   ser terceiro. Ausência ou ambiguidade não vira inferência permissiva.
5. A transação é reclamada no owner financeiro existente. Pix usa o E2E;
   Wise usa o identificador numérico canônico da transferência. Reexportação da
   imagem não cria nova identidade. O hash do arquivo não é a chave financeira.
6. A baixa usa a fila/fence existentes, com leitura prévia de reserva e saldo:
   Cloudbeds `PIX`/`Wise`; Bókun `POINT_OF_SALE`, com método no comentário.
   Reserva cancelada não é reativada. Resposta perdida vira incerteza, não retry de POST.
7. A Maya recebe o resultado da conferência antes de responder. A prosa anterior
   à ferramenta é descartada. A conclusão posterior da baixa volta pelo caminho
   de completion existente, que distingue baixa de aceite ManyChat.

## Revisão humana futura

O tipo durável `VisualTransferEvidence` registra método, transação, evento, hash
original, dados observados, data de transferência, data de validação e hash da
comparação. Após reabrir o banco, a mesma evidência continua representando:

- `human_review_status = pending`;
- `bank_settlement_confirmed = false`.

O status financeiro permanece no owner de pagamento. A projeção para Maya inclui
`evidence_basis=visual_receipt` e a pendência humana, sem fingir crédito consultado
no banco. Não há novo ledger financeiro.

Ao lado do banco de followup, `payment-proof-evidence/` mantém os bytes originais,
as comparações (observado × esperado) e os resultados finais de admissão em
objetos identificados por conteúdo. O banco conserva a referência da comparação.
**O backup deve preservar esse diretório junto com o estado financeiro.**

Não foi implementado dashboard, endpoint de aprovação/reprovação humana nem
estorno automático. A revisão humana ordinária **não cria handoff**. Conflitos de
transação, pagamento expirado e erros impeditivos usam o handoff já existente.

## Configuração de ativação (não aplicada)

- `V2_ENABLE_VISUAL_PROOFS=true`;
- comando Hermes com `--contract maya-v9`;
- `V2_PROOF_MEDIA_HOSTS`: hosts reais de anexos, separados por vírgula;
- `V2_VISUAL_PROOF_RECEIVERS_JSON`: dados estruturados e conferidos da conta de
  cada unidade/método. Exemplo de formato, **não configuração de produção**:

```json
{
  "agency:pix": {
    "profile_id": "receiver:agency:example",
    "identifier_kind": "tax_id",
    "identifier": "12.345.678/0001-90",
    "recipient_names": ["Beneficiario de Exemplo"]
  }
}
```

Escopos permitidos: `hostel:pix`, `hostel:wise`, `agency:pix`, `agency:wise`.
Tipos de identificador: `tax_id`, `email`, `phone`, `iban`, `account`, `random_key`.
O `profile_id` precisa coincidir com o owner da cobrança; uma alteração de conta
exige perfil versionado, sem reaproveitar silenciosamente a identidade anterior.
Não se extraem contas bancárias da prosa das instruções.

A configuração exige modo produtivo/controlado, instruções de pagamento,
provedores, chave do owner, entrega e handoff configurados. Os controles existentes
de janela/kill switch continuam superiores à habilitação desta funcionalidade.
O gate de Stripe é independente: habilitar Pix/Wise não habilita Stripe.

## Limites explícitos desta versão

- PNG, JPEG, WebP e **PDF**, inclusive PDF escaneado e multipágina. A mesma Maya
  lê as páginas renderizadas; não há agente OCR nem autoridade financeira adicional.
- PDF original e páginas PNG são preservados por SHA-256 no arquivo de evidências.
  Cada página carrega `document_sha256`, `page_number`, `page_count` e o evento de
  origem. O assessment vincula a observação ao original e à lista inteira de páginas.
  O identificador da transferência continua sendo a chave de deduplicação: PDF,
  nova exportação ou imagem do mesmo pagamento não geram outra baixa.
- Até quatro imagens/páginas por lote, até 4 MiB por arquivo original e 6 MiB de
  dados codificados no conjunto, preservando espaço para envelope e contexto.
  PDFs são renderizados a 144 dpi com PDFium (`pypdfium2==5.13.0`) em subprocesso
  com limite de tempo/memória; máximo 16 megapixels por página.
- **Todas as páginas ou nenhuma**: documento com senha necessária, corrompido,
  vazio ou acima dos limites fica indisponível. Não se ignora silenciosamente a
  última página para aprovar um comprovante parcial. A Maya pede arquivo legível
  ou encaminha o caso conforme o contexto; mídia indisponível não autoriza baixa.
- `application/pdf` é reconhecido; `application/octet-stream` só é tratado como
  PDF se os bytes começarem pela assinatura PDF e o parser conseguir abri-los.
- Hora/fuso da transferência precisam ser inequívocos. Em Wise, usa-se o valor
  efetivamente destinado ao beneficiário, não o valor de origem antes de câmbio/taxas.
- A janela vem da reserva confirmada (prazo existente de 24h), com transferência
  não anterior à emissão da instrução. Não é uma API de recebimento bancário.
- Instruções antigas que não persistiram valor/moeda não são reconstruídas a
  partir da prosa. Exigem resolução operacional; **não pedir novo pagamento por
  isso**. Não existe backfill dos bancos ativos nesta entrega.
- Nenhuma rotina copia dados/regras de V3 ou torna Maya Ops gravável.

## Fontes de contrato consultadas

- Banco Central, `EndToEndId`, `https://raw.githubusercontent.com/bacen/pix-api/master/openapi.yaml`:
  32 caracteres alfanuméricos; o formato reduzido legado não é usado para novas
  evidências visuais. Leitura de estados históricos permanece compatível.
- Bókun, documentação oficial → `https://api-docs.bokun.dev/rest-v1.yaml`:
  `Payment.paymentType` aceita `POINT_OF_SALE`; `BANK_TRANSFER` não faz parte do enum
  consultado. O snapshot da fonte está no diretório privado de qualificação.

Os testes de provedores desta entrega usam HTTP simulado. Uma chamada real ao
modelo não constitui teste real de baixa em Cloudbeds/Bókun nem de WhatsApp.
