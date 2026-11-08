# Pressente Ponto — REP-P · Documentação técnica

Programa de computador: **Pressente Ponto — Registrador Eletrônico de Ponto via
Programa (REP-P)**. Versão registrada: a de `reppp/__init__.py` (`VERSAO`).
Linguagens: Python 3.10+ e SQL (PostgreSQL 14+).

## 1. Finalidade

Registrar a jornada de trabalho de empregados por meio de programa de
computador, nos termos da Portaria MTP nº 671/2021 (REP-P), de modo exclusivo:
o programa não tem outra função além do registro de ponto e da guarda e
entrega dos registros. Atende a várias empresas usuárias (estabelecimentos),
cada uma isolada das demais.

## 2. Arquitetura

| Módulo | Responsabilidade |
| --- | --- |
| `reppp/formato.py` | Leiaute do Arquivo Fonte de Dados (AFD, Anexo V): campos de posição fixa, ISO 8859-1, CR LF, CRC-16/KERMIT, data e hora com fuso, registros tipo 1, 2, 5, 6, 7 e 9; validação de CPF e CNPJ (numérico e alfanumérico) |
| `reppp/esquema.sql` | Armazenamento dos Registros de Ponto (ARP) em PostgreSQL, com gatilhos que recusam UPDATE, DELETE e TRUNCATE nos registros, comprovantes e provas |
| `reppp/registro.py` | Núcleo: cadastro do empregador (tipo 2) e dos empregados (tipo 5), eventos sensíveis (tipo 6) e a marcação (tipo 7); NSR sequencial por estabelecimento sob trava de linha; hash encadeado; conferência de integridade |
| `reppp/comprovante.py` | Comprovante de Registro de Ponto do Trabalhador em PDF (art. 79), assinado em PAdES, gravado uma única vez; lista das últimas 48 horas (art. 80) |
| `reppp/afd.py` | Geração do AFD de qualquer período, com a linha de assinatura e o arquivo `.p7s` (CAdES destacada) |
| `reppp/assinatura.py` | Assinaturas com certificado ICP-Brasil (art. 88): CAdES-BES destacada e PAdES |
| `reppp/prova.py` | Prova adicional da marcação: localização no instante da batida, cercas geográficas, dispositivo, IP e foto cifrada (AES-256-GCM), sem nunca impedir a marcação |
| `reppp/atestado.py` | Atestado Técnico e Termo de Responsabilidade por empresa usuária (art. 89) |
| `reppp/api.py` | Interface HTTP (FastAPI) com autenticação por chave do estabelecimento |

## 3. A marcação

1. A hora da marcação é a do servidor (sincronizado com a Hora Legal
   Brasileira). Na marcação off-line, a hora vem do coletor, é marcada como
   off-line no registro e não pode estar no futuro.
2. Não há restrição de horário, marcação automática nem exigência de
   autorização prévia (art. 74).
3. O NSR é atribuído sob trava da linha do estabelecimento: sequencial, a
   partir de 1, sem lacunas, inclusive com marcações simultâneas.
4. O registro tipo 7 leva o código hash SHA-256 das posições 001 a 073 da
   linha, concatenadas com o hash da marcação anterior do mesmo
   estabelecimento (corrente de hashes).
5. O comprovante é gerado e assinado logo após a marcação.

## 4. Integridade

- O banco recusa alteração e exclusão dos registros (gatilhos).
- A conferência (`registro.conferir`) recalcula toda a corrente: NSR sem
  lacuna, CRC dos registros 2 e 5, hash de cada registro 7, encadeamento e o
  hash da prova de cada marcação. Qualquer alteração por fora é apontada.
- A foto é a única informação que pode sair do ARP, e apenas depois do prazo
  de guarda definido pela empresa (LGPD); o hash dela permanece no registro.

## 5. Arquivos e assinaturas

| Arquivo | Formato | Assinatura |
| --- | --- | --- |
| AFD | Anexo V; nome `AFD` + nº INPI + CNPJ/CPF + `REP_P` | CAdES destacada (`.p7s`), SHA-256, signing-certificate-v2 |
| Comprovante | PDF por marcação | PAdES |
| Atestado técnico | PDF por empresa usuária | Assinatura qualificada das pessoas físicas (fora do servidor) |

## 6. Segurança

- Chave de acesso por estabelecimento (só o hash é guardado); chave de
  administração separada; um estabelecimento nunca lê dados de outro.
- Certificado do desenvolvedor fora do código, carregado por variável de
  ambiente; usuários de banco distintos para o REP-P e o programa de
  tratamento (`scripts/permissoes.sql`).
- Cada abertura de foto registra quem, quando e por quê.

## 7. Verificação

`verificar.py` executa a bateria de testes contra PostgreSQL real e quebra
de propósito cada regra legal (mutações), exigindo que todas sejam detectadas.
