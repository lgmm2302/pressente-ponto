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
