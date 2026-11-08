# Pressente Ponto — Programa de Tratamento · Documentação técnica

Programa de computador: **Pressente Ponto — Programa de Tratamento de Registro
de Ponto (PTRP)**. Versão registrada: a de `ptrp/__init__.py` (`VERSAO`).
Linguagens: Python 3.10+ e SQL (PostgreSQL 14+).

## 1. Finalidade

Tratar as marcações registradas pelo REP-P — sem alterá-las — para apurar a
jornada, gerar o Espelho de Ponto Eletrônico (art. 84) e o Arquivo Eletrônico
de Jornada (AEJ, Anexo VI), nos termos da Portaria MTP nº 671/2021.

## 2. Arquitetura

| Módulo | Responsabilidade |
| --- | --- |
| `ptrp/regras.py` | Regras de cálculo com vigência: tolerâncias, adicionais, noturno, intervalos, interjornada, banco de horas; recusa parâmetros abaixo do mínimo legal |
| `ptrp/apuracao.py` | Apuração pura (sem banco): jornadas, horas trabalhadas, hora noturna reduzida e prorrogação, extras, débitos, faltas, abonos, DSR, feriados, intervalo intrajornada, interjornada, banco de horas, intervalo pré-assinalado e alertas |
| `ptrp/esquema.sql` | Tabelas do tratamento, todas sem UPDATE e sem DELETE |
| `ptrp/dados.py` | Cadastros (horários, escalas, regras, feriados) e tratamentos (incluir, desconsiderar, abonar, revogar) com motivo, autor e ciência do empregado |
| `ptrp/espelho.py` | Espelho mensal em PDF assinado (PAdES), versões imutáveis, aceite ou contestação do empregado |
| `ptrp/aej.py` | AEJ versão 002 (registros 01 a 08 e 99), assinado em CAdES destacada |
| `ptrp/api.py` | Interface HTTP em processo separado do REP-P |

## 3. Princípios

- As marcações originais nunca são alteradas: o tratamento acrescenta
  registros (inclusão «I», desconsideração «D», pré-assinalação «P»), sempre
  com motivo, e a original continua visível no espelho e no AEJ.
- O empregado dá ciência ou recusa cada tratamento, uma única vez; a recusa
  aparece no espelho.
- As regras de cálculo têm vigência; um período é sempre apurado com as
  regras que valiam nele.
- O programa só lê o REP-P (usuário de banco sem permissão de escrita nele).

## 4. Verificação

Os casos de cálculo (tolerância, noturno, intervalo, interjornada, limite de
horas extras, banco de horas, ponto britânico) são testados contra a CLT e a
jurisprudência citada em cada regra, com mutações que precisam ser detectadas.
