# Pedido de registro no INPI — roteiro

O registro do REP-P no INPI é obrigatório (Portaria 671, art. 91). O do
programa de tratamento é opcional (proteção). O INPI não recebe o código:
recebe o **resumo digital (hash SHA-512)** de um arquivo com o código e a
documentação técnica, e o certificado sai em até 10 dias depois do pedido.

## Antes do pedido (uma vez)

- [ ] **Nome definitivo do produto.** O nome vai no título do registro, no
      comprovante e no atestado. Hoje: «Pressente Ponto» (provisório).
- [ ] **Titular.** A empresa que será dona do programa (CNPJ). Autores: as
      pessoas que criaram o programa — conferir com o advogado como declarar a
      autoria de um programa escrito com assistência de IA.
- [ ] **Pontos a confirmar** em `REQUISITOS.md` (leiaute do AFD e do AEJ),
      validando um AFD gerado aqui num validador de terceiros. Cada correção
      depois do pacote exige versão nova.
- [ ] **e-CNPJ** do titular (assina a Declaração de Veracidade no INPI) e
      **e-CPF** do representante legal e do responsável técnico (assinam o
      atestado de cada empresa cliente).
- [ ] Revisão do advogado: este roteiro, o atestado (`GET /v1/atestado`) e as
      regras de cálculo listadas em `REQUISITOS.md`.

## O pacote (no dia do pedido)

1. Pedir ao Claude para gerar o pacote da versão (ou rodar
   `python -m scripts.pacote_inpi rep-p`). Ele cria `inpi/rep-p-<versão>/`:
   - `pressente-ponto-rep-p-<versão>.zip` — o arquivo que o INPI identifica;
   - `SHA512.txt` — o resumo digital que vai no formulário;
   - `MANIFESTO.txt` — o hash de cada arquivo dentro do pacote.
2. Commit e envio da pasta. A partir daí, o teste `test_inpi.py` trava: se o
   código do REP-P mudar sem subir a versão, os testes ficam vermelhos.
3. **Guardar o .zip sem mudar um byte por 50 anos**, em pelo menos dois
   lugares além do GitHub (ex.: um disco e uma nuvem). O pacote também pode
   ser refeito a partir do repositório, byte a byte, a qualquer tempo.

## O pedido no e-Software (INPI)

1. Pagar a GRU do **código 730** (Pedido de Registro de Programa de
   Computador) **antes** de preencher — valor na tabela vigente do INPI.
2. Baixar a **Declaração de Veracidade (DV)** e assiná-la com o e-CNPJ do
   titular.
3. Preencher o formulário:

| Campo | Valor |
| --- | --- |
| Título | Pressente Ponto - Registrador Eletrônico de Ponto via Programa (REP-P) |
| Data de criação | 07/11/2026 |
| Data de publicação | a do primeiro uso por um cliente (ou deixar sem, se ainda não publicado) |
| Linguagens | Python; SQL |
| Campo de aplicação | escolher na tabela do INPI: administração / recursos humanos (controle de jornada) |
| Tipo de programa | escolher na tabela do INPI: aplicativo / sistema de informação |
| Algoritmo do resumo | SHA-512 |
| Resumo digital | o de `SHA512.txt` |
| Derivação | não (primeira versão) |

4. Anexar a DV assinada e enviar. O número do pedido/registro vai para
   `implantacao/desenvolvedor.env` (`PONTO_NUMERO_INPI`), commit e
   «Publicar → 3»: o número passa a sair no AFD, no comprovante e no atestado.

## Depois do certificado

- Instalar o e-CNPJ do desenvolvedor no servidor (no lugar do certificado de
  teste) — os comprovantes e arquivos passam a ter valor legal.
- Para cada empresa cliente: baixar o atestado (`GET /v1/atestado`, com a
  chave dela), colher as duas assinaturas com e-CPF e entregar à empresa.
- Versão nova do REP-P (ex.: 1.0.1): novo pacote, novo pedido de registro
  (programa derivado, citando o anterior) e atestado com a versão nova.

Fontes: [INPI — perguntas frequentes sobre programa de computador](https://www.gov.br/inpi/es/servicios/preguntas-frecuentes/programas-de-computador),
[INPI — tabela de retribuições](https://www.gov.br/inpi/pt-br/servicos/custos-e-pagamento/NovaTabeladeRetribuiesINPI_SOFTWARE_Final_20_dez_25.pdf),
[Portaria 671 — perguntas e respostas](https://www.guiatrabalhista.com.br/tematicas/portaria-671-2021-perguntas-e-respostas.htm).
