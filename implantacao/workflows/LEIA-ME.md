# Os botões do GitHub (workflows)

Estes dois arquivos são os botões do Pressente Ponto no GitHub. Por segurança,
o Claude não grava direto na pasta `.github`; copie-os uma vez:

1. No Explorador de Arquivos, abra `Documents\GitHub\pressente-ponto`.
2. Crie a pasta `.github` e, dentro dela, a pasta `workflows`.
3. Copie `testes.yml` e `publicar.yml` desta pasta para `.github\workflows`.
4. No GitHub Desktop: commit e **Push origin** (ou **Publish branch** na primeira vez).

Depois disso, em github.com/lgmm2302/pressente-ponto → **Actions** aparecem:
- **Testes**: roda sozinho a cada envio.
- **Publicar o Pressente Ponto**: o botão, com as opções 1 a 5.
