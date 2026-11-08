# Requisitos legais do REP-P e o que falta confirmar

Base: Portaria MTP nº 671/2021, arts. 73 a 92 e Anexos V (AFD), VI (AEJ),
VII (atestado técnico) e IX (requisitos do REP-P). A Portaria Consolidada MTE
nº 1/2025 (vigente desde 02/01/2026) revogou outras partes da 671 (art. 1º,
I e XII; arts. 2º a 23; arts. 140 a 184-F); as regras de ponto seguem nela.
**Conferir a redação vigente com o advogado antes do registro no INPI.**

## Atendido no código

- Programa exclusivo de registro de jornada, em servidor dedicado ou nuvem (art. 78).
- Sem restrição de horário, sem marcação automática, sem autorização prévia para
  hora extra, sem meio de alterar ou apagar o que foi registrado (art. 74).
- NSR por estabelecimento, iniciando em 1 (Anexo IX).
- Dados do ARP não apagáveis nem alteráveis (Anexo IX): travas no banco + corrente de hash.
- Marcação com NSR, CPF, data e hora da marcação, data e hora da gravação,
  identificador do coletor, on-line/off-line e hash SHA-256 (Anexo IX, Anexo V tipo 7).
- Off-line só com a hora do coletor e marcado como off-line; enviado na reconexão.
- Comprovante com os campos do art. 79, em PDF assinado (PAdES), disponível logo
  após a batida, com acesso às últimas 48 horas (art. 80).
- AFD do Anexo V gerado na hora para qualquer intervalo (art. 81 e Anexo IX),
  ISO 8859-1, CR LF, CRC-16/KERMIT nos tipos 1 a 5, linha
  `ASSINATURA_DIGITAL_EM_ARQUIVO_P7S` e `.p7s` CAdES destacado com certificado
  ICP-Brasil (arts. 87–88).
- Nome do arquivo: `AFD` + nº INPI + CNPJ/CPF do empregador + `REP_P`.
- Eventos sensíveis do REP-P (tipo 6: 02, 07, 08).

## A confirmar no texto oficial (marcados `CONFIRMAR` em `reppp/formato.py`)

1. Campo numérico (N): zeros à esquerda (adotado) ou outro preenchimento.
2. Composição exata do hash do tipo 7: adotado SHA-256 das posições 001–073 da
   linha + hash do tipo 7 anterior do mesmo estabelecimento, hexadecimal minúsculo.
3. CRC em hexadecimal maiúsculo (adotado) ou minúsculo.
4. Extensão `.txt` no nome do AFD.
5. Se a linha de assinatura também termina em CR LF (adotado: sim).
Validar um AFD gerado aqui num validador de terceiros (ex.: o de um fabricante
conhecido) antes do registro.

## Ainda não feito (fases seguintes)

- Programa de tratamento: jornadas, escalas, extras, noturno, banco de horas,
  espelho mensal com aceite, **AEJ (Anexo VI)**.
- Ajustes com ciência do empregado (registro novo, original preservado).
- Sincronização e monitoramento do relógio com a HLB (desvio ≤ 30 s).
- Eventos 07/08 automáticos de disponibilidade e indisponibilidade.
- Alta disponibilidade e armazenamento redundante do ARP (infraestrutura).
- Prova extra: local, cerca geográfica, foto/facial (LGPD), alertas.
- Atestado técnico (Anexo VII) — rascunho para o Luiz e o advogado.

## Fontes

- Portaria MTP 671/2021 — perguntas e respostas: https://www.guiatrabalhista.com.br/tematicas/portaria-671-2021-perguntas-e-respostas.htm
- Leiaute do AFD (MTE): https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/fiscalizacao-do-trabalho/leiaute-do-arquivo-fonte-de-dados-afd.pdf
- Requisitos do REP-P (CNI, RT Informa 33): https://conexaotrabalho.portaldaindustria.com.br/media/publication/files/RT%20Informa%20N.%2033%20-%20NOVEMBRO%20-%20REP%20-%20Ponto%20Eletronico_GRV74vP.pdf
- Portaria Consolidada MTE nº 1/2025: https://blog.iusnatura.com.br/portaria-mte-1-25-entra-em-vigor-em-2026/
