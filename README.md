# Pressente Ponto — REP-P

Registrador Eletrônico de Ponto via Programa (Portaria MTP 671/2021), feito para
ser **o programa registrado no INPI** e servir, por API, ao Pressente, ao Exxen,
ao Redalis e a quem licenciar só o ponto. *Nome provisório.*

Este repositório não importa nada dos outros sistemas, e nenhum deles escreve
no banco daqui: eles **batem o ponto e leem** pela API.

## O que já existe (fases 1 a 3 do plano)

| Peça | Onde | Regra |
| --- | --- | --- |
| Marcação | `reppp/registro.py` | Hora do servidor; sem horário proibido, sem autorização prévia, sem batida automática (art. 74) |
| NSR | `reppp/registro.py` | Por estabelecimento, de 1 em 1, sem buraco, mesmo com batidas simultâneas |
| Imutabilidade | `reppp/esquema.sql` | UPDATE, DELETE e TRUNCATE em `registros` e `comprovantes` param no próprio banco |
| Hash encadeado | `reppp/formato.py` | SHA-256 de cada marcação, ligado ao da anterior; `conferir()` acha qualquer adulteração ou registro apagado |
| Comprovante | `reppp/comprovante.py` | PDF com os campos do art. 79, assinado em PAdES, gravado uma vez; lista das últimas 48 h |
| AFD | `reppp/afd.py` | Anexo V: registros 1, 2, 5, 6, 7 e 9, ISO 8859-1, CR LF, CRC-16/KERMIT, linha de assinatura e `.p7s` (CAdES destacada) |
| API | `reppp/api.py` | Estabelecimentos, empregados, marcações, comprovantes, AFD, conferência e hora |

## O programa de tratamento (fase 5) — `ptrp/`

Processo **separado** do REP-P (`ptrp/api.py`): lê as marcações e nunca as
altera; grava só nas tabelas `ptrp_*`, que também não aceitam UPDATE nem DELETE.

| Peça | Onde | Regra |
| --- | --- | --- |
| Horários, escalas, regras, feriados | `ptrp/dados.py` | Horário não se edita (novo código); escala e regras têm vigência; a escala exige DSR |
| Regras de cálculo | `ptrp/regras.py` | Padrão da CLT; a convenção muda o que pode; recusa adicional abaixo do mínimo legal |
| Apuração | `ptrp/apuracao.py` | Tolerância de 5/10 min (art. 58 §1º, Súm. 366), noturno 22h–5h com hora reduzida e prorrogação (art. 73, Súm. 60), intervalo (art. 71, Tema 14), interjornada (art. 66), limite de 2 h (art. 59), DSR, feriado, folga, faltas, abonos, banco de horas, pré-assinalação, alerta de ponto britânico (Súm. 338) |
| Tratamentos | `ptrp/dados.py` | Incluir, desconsiderar, abonar e revogar: sempre registro novo, com motivo e autor; a original fica |
| Ciência | `ptrp/dados.py` | O empregado dá ciência ou recusa (com motivo) cada tratamento, uma vez só |
| Espelho | `ptrp/espelho.py` | PDF mensal assinado (PAdES), versões imutáveis, aceite ou contestação do empregado |
| AEJ | `ptrp/aej.py` | Anexo VI, versão 002, separador «\|», CR LF, ISO 8859-1, `.p7s` |

Em produção, rode os dois processos com usuários de banco diferentes
(`scripts/permissoes.sql`): o do tratamento só lê o REP-P.

O que ainda falta está em `REQUISITOS.md` (pontos a confirmar no texto oficial)
e no plano da especificação (programa de tratamento, AEJ, ajustes com ciência,
prova extra, integração).

## Rodar os testes

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[teste]"
export PONTO_BANCO_TESTE=postgresql://postgres@127.0.0.1:5432/postgres
python verificar.py          # testes + regras quebradas de propósito
python verificar.py --rapido # só os testes
```

## Configuração em produção

| Variável | O que é |
| --- | --- |
| `PONTO_BANCO` | PostgreSQL só do REP-P |
| `PONTO_NUMERO_INPI` | Número do registro no INPI (vai no comprovante e no AFD) |
| `PONTO_DESENVOLVEDOR_TIPO` / `PONTO_DESENVOLVEDOR_ID` | 1 = CNPJ, 2 = CPF, e o número do desenvolvedor |
| `PONTO_CERTIFICADO_PFX` / `PONTO_CERTIFICADO_SENHA` | e-CNPJ ICP-Brasil do desenvolvedor (instalado pelo Luiz; nunca no repositório) |
| `PONTO_CHAVE_ADMINISTRACAO` | Chave para cadastrar estabelecimentos |

O relógio do servidor precisa estar sincronizado com a Hora Legal Brasileira
(Observatório Nacional), com desvio máximo de 30 segundos (Anexo IX).
