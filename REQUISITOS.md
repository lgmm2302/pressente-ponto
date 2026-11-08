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

## Programa de tratamento (feito na fase 5)

- PTRP separado do REP-P; preserva as marcações originais e só acrescenta
  (inclusão «I», desconsideração «D», pré-assinalação «P», ausências e banco) —
  Portaria 671, art. 82 e perguntas 53 a 56 do MTE.
- AEJ do Anexo VI (versão 002) e espelho de ponto (art. 84), ambos assinados.

### A confirmar no AEJ
1. `seqEntSaida` com 3 dígitos (adotado: «001»).
2. `seqEntSaida` de uma marcação desconsiderada (adotado: o do par em que caiu).
3. Nome do arquivo (a Portaria não exige; adotado `AEJ_<CNPJ>_<início>_<fim>.txt`).
4. CNO × CAEPF: hoje decididos pelo tamanho do número cadastrado (12 ou 14).

### A conferir com o advogado (regras de cálculo)
- Prorrogação do noturno (Súmula 60, II): aplicada quando pelo menos metade da
  janela 22h–5h foi trabalhada e o turno segue depois das 5h.
- Banco de horas: extras viram crédito e atrasos viram débito; faltas não entram.
- Dia sem escala com trabalho: tratado como folga (extra comum).
- Tolerância do intervalo (TST, IRR Tema 14): até 5 min no total.

## Ainda não feito (fases seguintes)

- App web de batida para quem licenciar só o ponto (fase 4).
- Vencimento do banco de horas (pagar o saldo vencido) e DSR perdido por falta.
- Folga compensatória de feriado (AEJ 07 tipo 4).
- Sincronização e monitoramento do relógio com a HLB (desvio ≤ 30 s).
- Eventos 07/08 automáticos de disponibilidade e indisponibilidade.
- Alta disponibilidade e armazenamento redundante do ARP (infraestrutura).
- Reconhecimento facial (comparar a foto com a do cadastro): hoje a foto é
  guardada como prova; a comparação automática fica para um fornecedor
  especializado, com relatório de impacto (LGPD art. 38) antes de ligar.
- Prova da batida, a conferir com o advogado: o texto da base legal da foto e
  o prazo de guarda que cada empresa vai usar.
- Atestado técnico (Anexo VII) — rascunho para o Luiz e o advogado.

## Fontes

- Portaria MTP 671/2021 — perguntas e respostas: https://www.guiatrabalhista.com.br/tematicas/portaria-671-2021-perguntas-e-respostas.htm
- Leiaute do AFD (MTE): https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/fiscalizacao-do-trabalho/leiaute-do-arquivo-fonte-de-dados-afd.pdf
- Requisitos do REP-P (CNI, RT Informa 33): https://conexaotrabalho.portaldaindustria.com.br/media/publication/files/RT%20Informa%20N.%2033%20-%20NOVEMBRO%20-%20REP%20-%20Ponto%20Eletronico_GRV74vP.pdf
- Portaria Consolidada MTE nº 1/2025: https://blog.iusnatura.com.br/portaria-mte-1-25-entra-em-vigor-em-2026/
