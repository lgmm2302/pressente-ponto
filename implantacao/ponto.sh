#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Pressente Ponto — instalação, publicação e manutenção na VM (Ubuntu).
#
# Quem chama é o botão «Publicar o Pressente Ponto» (GitHub Actions), depois
# de copiar o código para ~/_ponto_novo. Ações:
#
#   verificar   só lê: serviços, saúde, versão no ar, Caddy, banco
#   instalar    1ª vez (e seguro repetir): usuário de sistema, banco e senhas,
#               certificado de TESTE, serviços, endereço no Caddy; depois publica
#   publicar    versão nova ao lado da atual, migra o banco, troca, confere a
#               saúde e VOLTA SOZINHO para a anterior se algo falhar
#   voltar      volta para a versão anterior
#   backup      pg_dump do banco do ponto (guarda os 14 mais novos) e expurgo
#               das fotos com prazo de guarda vencido
#   cadastrar   cadastra uma empresa no REP-P e mostra UMA vez a chave dela
#               (CAD_ID, CAD_RAZAO, CAD_CPF, CAD_FUSO, CAD_LOCAL)
#
# Nunca imprime senha, URL de banco nem chave. O que é segredo fica em
# /etc/pressente-ponto (root:ponto, 0640).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ACAO="${1:-verificar}"
NOVO="$HOME/_ponto_novo"
BASE=/opt/pressente-ponto
ETC=/etc/pressente-ponto
BANCO=pressente_ponto
UNIT_REP=pressente-ponto-rep
UNIT_TRAT=pressente-ponto-tratamento
PORTA_REP=8710
PORTA_TRAT=8711
HOST_PUBLICO="${PONTO_HOST_PUBLICO:-}"
MARCA_CADDY="# >>> pressente-ponto (gerado pelo instalador; não editar entre as marcas)"
FIM_CADDY="# <<< pressente-ponto"

titulo() { echo; echo "===== $* ====="; }
falha() { echo "ERRO: $*"; exit 1; }

precisa_sudo() {
  sudo -n true 2>/dev/null || falha "o usuário $(whoami) precisa de sudo sem senha nesta VM."
}

saudavel() {
  for _ in $(seq 1 30); do
    if curl -fsS -m 3 "http://127.0.0.1:$PORTA_REP/v1/saude" >/dev/null 2>&1 \
       && curl -fsS -m 3 "http://127.0.0.1:$PORTA_TRAT/v1/saude-tratamento" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# A release que está no ar de verdade (a pasta para onde o link «atual» aponta),
# ou nada. NÃO usar `readlink -f "$BASE/atual"` para isso: ele devolve o caminho
# do link mesmo quando o link ainda não existe — foi assim que a 1ª instalação
# (08/11/2026) gravou «anterior = /opt/pressente-ponto/atual», e um «voltar»
# nesse estado faria «atual» apontar para si mesmo.
versao_no_ar() {
  [ -L "$BASE/atual" ] || return 0
  readlink -e "$BASE/atual" 2>/dev/null || true
}

# A versão guardada em $BASE/anterior — só se for uma release de verdade:
# existe, está em releases/, não é o link «atual» nem a versão no ar.
anterior_valida() {
  local a
  a="$(cat "$BASE/anterior" 2>/dev/null || true)"
  [ -n "$a" ] || return 1
  [ -d "$a" ] || return 1
  [ "$a" != "$BASE/atual" ] || return 1
  [ "$a" != "$(versao_no_ar)" ] || return 1
  case "$a" in "$BASE/releases/"*) ;; *) return 1 ;; esac
  echo "$a"
}

# ─────────────────────────────────────────────────────────────────────────────
verificar() {
  titulo "Pressente Ponto — verificação (só leitura)"
  for u in "$UNIT_REP" "$UNIT_TRAT"; do
    if systemctl cat "$u" >/dev/null 2>&1; then
      echo "[serviço] $u: $(systemctl is-active "$u" || true)"
    else
      echo "[serviço] $u: NÃO INSTALADO (rode a opção «instalar»)"
    fi
  done
  echo "[saúde] REP-P: $(curl -fsS -m 3 "http://127.0.0.1:$PORTA_REP/v1/saude" 2>/dev/null || echo 'não responde')"
  echo "[saúde] tratamento: $(curl -fsS -m 3 "http://127.0.0.1:$PORTA_TRAT/v1/saude-tratamento" 2>/dev/null || echo 'não responde')"
  echo "[versão no ar] $(curl -fsS -m 3 "http://127.0.0.1:$PORTA_REP/v1/versao" 2>/dev/null || echo '?')"
  echo "[pasta atual] $(v="$(versao_no_ar)"; echo "${v:-nenhuma}")"
  echo "[anterior] $(anterior_valida || echo 'nenhuma')"
  if [ -n "$HOST_PUBLICO" ]; then
    echo "[porta da frente] https://$HOST_PUBLICO/v1/hora → $(curl -fsS -m 5 "https://$HOST_PUBLICO/v1/hora" 2>/dev/null || echo 'não responde (DNS apontado para esta VM?)')"
  fi
  if sudo -n grep -qsF "$MARCA_CADDY" /etc/caddy/Caddyfile /etc/caddy/conf.d/pressente-ponto.caddy 2>/dev/null; then
    echo "[Caddy] endereço do ponto configurado"
  else
    echo "[Caddy] endereço do ponto AINDA NÃO configurado"
  fi
  if sudo -n test -f "$ETC/rep.env"; then
    if sudo -n grep -q '^PONTO_CERTIFICADO_DE_TESTE=1' "$ETC/rep.env"; then
      echo "[certificado] de TESTE — comprovantes e AFD sem valor legal"
    else
      echo "[certificado] configurado (não é o de teste)"
    fi
    echo "[INPI] $(sudo -n grep -q '^PONTO_NUMERO_INPI=.\+' "$ETC/rep.env" && echo 'número configurado' || echo 'ainda sem número')"
  fi
  if command -v psql >/dev/null; then
    local n
    n="$(sudo -n -u postgres psql -tAc "SELECT count(*) FROM pg_database WHERE datname='$BANCO'" 2>/dev/null || echo '?')"
    echo "[banco] $BANCO: $([ "$n" = 1 ] && echo existe || echo 'não existe')"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
configurar_caddy() {
  [ -n "$HOST_PUBLICO" ] || falha "falta a variável PONTO_HOST_PUBLICO (ex.: ponto.pressente.com.br)."
  local bloco destino backup
  bloco="$(cat <<CADDY
$MARCA_CADDY
$HOST_PUBLICO {
	encode gzip
	@tratamento path /v1/saude-tratamento /v1/horarios* /v1/regras* /v1/escalas* /v1/feriados* /v1/matriculas* /v1/tratamentos* /v1/apuracao* /v1/espelhos* /v1/aej*
	reverse_proxy @tratamento 127.0.0.1:$PORTA_TRAT
	reverse_proxy 127.0.0.1:$PORTA_REP
}
$FIM_CADDY
CADDY
)"
  backup="/etc/caddy/Caddyfile.antes-do-ponto.$(date +%Y%m%d%H%M%S)"
  sudo -n cp -a /etc/caddy/Caddyfile "$backup"
  if sudo -n grep -qE '^[[:space:]]*import[[:space:]]+/etc/caddy/conf\.d' /etc/caddy/Caddyfile; then
    destino=/etc/caddy/conf.d/pressente-ponto.caddy
    echo "$bloco" | sudo -n tee "$destino" >/dev/null
  else
    destino=/etc/caddy/Caddyfile
    # tira o bloco antigo (se houver) e põe o novo no fim
    sudo -n awk -v ini="$MARCA_CADDY" -v fim="$FIM_CADDY" '
      $0 == ini {pular=1; next} $0 == fim {pular=0; next} !pular {print}' "$destino" | sudo -n tee "$destino.novo" >/dev/null
    printf '\n%s\n' "$bloco" | sudo -n tee -a "$destino.novo" >/dev/null
    sudo -n mv "$destino.novo" "$destino"
  fi
  if ! sudo -n caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    sudo -n cp -a "$backup" /etc/caddy/Caddyfile
    sudo -n rm -f /etc/caddy/conf.d/pressente-ponto.caddy
    falha "o Caddy recusou a configuração; voltei o Caddyfile como estava. Nada mudou para as escolas."
  fi
  sudo -n systemctl reload caddy
  echo "[Caddy] $HOST_PUBLICO → ponto (cópia do anterior em $backup)"
}

escrever_unit() {
  local nome="$1" descricao="$2" env="$3" app="$4" porta="$5" depois="$6"
  sudo -n tee "/etc/systemd/system/$nome.service" >/dev/null <<UNIT
[Unit]
Description=$descricao
After=network.target postgresql.service $depois
Wants=postgresql.service

[Service]
User=ponto
Group=ponto
EnvironmentFile=-$ETC/desenvolvedor.env
EnvironmentFile=$ETC/$env
WorkingDirectory=$BASE/atual
ExecStart=$BASE/atual/.venv/bin/uvicorn --factory $app --host 127.0.0.1 --port $porta --workers 2 --proxy-headers
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
}

instalar() {
  titulo "Pressente Ponto — instalação"
  precisa_sudo
  [ -d "$NOVO" ] || falha "o código não chegou em $NOVO."
  command -v psql >/dev/null || falha "PostgreSQL não está instalado nesta VM."
  command -v caddy >/dev/null || falha "Caddy não está instalado nesta VM."
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || falha "precisa de Python 3.10 ou mais novo."
  python3 -c 'import venv, ensurepip' 2>/dev/null || sudo -n apt-get install -y -q python3-venv >/dev/null
  id ponto >/dev/null 2>&1 || sudo -n useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin ponto
  sudo -n mkdir -p "$BASE/releases" "$ETC"
  sudo -n chown root:ponto "$ETC" && sudo -n chmod 0750 "$ETC"

  if sudo -n test -f "$ETC/dono.env"; then
    echo "[banco] já configurado (senhas mantidas)"
  else
    local SD SR SP SA
    SD="$(openssl rand -hex 24)"; SR="$(openssl rand -hex 24)"; SP="$(openssl rand -hex 24)"; SA="$(openssl rand -hex 24)"
    sudo -n -u postgres psql -v ON_ERROR_STOP=1 -q >/dev/null <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ponto_dono') THEN CREATE ROLE ponto_dono LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ponto_rep')  THEN CREATE ROLE ponto_rep LOGIN;  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ponto_ptrp') THEN CREATE ROLE ponto_ptrp LOGIN; END IF;
END \$\$;
ALTER ROLE ponto_dono PASSWORD '$SD';
ALTER ROLE ponto_rep PASSWORD '$SR';
ALTER ROLE ponto_ptrp PASSWORD '$SP';
SQL
    if [ "$(sudo -n -u postgres psql -tAc "SELECT count(*) FROM pg_database WHERE datname='$BANCO'")" != 1 ]; then
      sudo -n -u postgres createdb -O ponto_dono "$BANCO"
    fi
    sudo -n -u postgres psql -q -d "$BANCO" -c "REVOKE ALL ON SCHEMA public FROM PUBLIC; GRANT USAGE ON SCHEMA public TO ponto_rep, ponto_ptrp; GRANT ALL ON SCHEMA public TO ponto_dono;" >/dev/null
    local URL="postgresql://%s:%s@127.0.0.1:5432/$BANCO"
    ( umask 077
      printf "PONTO_BANCO=$URL\n" ponto_dono "$SD" | sudo -n tee "$ETC/dono.env" >/dev/null
      { printf "PONTO_BANCO=$URL\n" ponto_rep "$SR"
        echo "PONTO_CRIAR_ESQUEMA=0"
        echo "PONTO_CHAVE_ADMINISTRACAO=$SA"
        echo "PONTO_CHAVE_PEM=$ETC/certificado-de-teste/teste-chave.pem"
        echo "PONTO_CERTIFICADO_PEM=$ETC/certificado-de-teste/teste-certificado.pem"
        echo "PONTO_CERTIFICADO_DE_TESTE=1"
      } | sudo -n tee "$ETC/rep.env" >/dev/null
      { printf "PONTO_BANCO=$URL\n" ponto_ptrp "$SP"
        echo "PONTO_CRIAR_ESQUEMA=0"
        echo "PONTO_CHAVE_PEM=$ETC/certificado-de-teste/teste-chave.pem"
        echo "PONTO_CERTIFICADO_PEM=$ETC/certificado-de-teste/teste-certificado.pem"
        echo "PONTO_CERTIFICADO_DE_TESTE=1"
      } | sudo -n tee "$ETC/ptrp.env" >/dev/null )
    sudo -n chown root:ponto "$ETC"/*.env && sudo -n chmod 0640 "$ETC"/*.env
    sudo -n chmod 0600 "$ETC/dono.env"
    echo "[banco] $BANCO criado; senhas novas gravadas em $ETC (não exibidas)"
    echo "[chave de administração] gravada em $ETC/rep.env (PONTO_CHAVE_ADMINISTRACAO) — não exibida"
  fi

  # Chave das fotos (AES-256): gerada uma vez; sem ela a foto não é guardada
  # (a batida entra do mesmo jeito, com aviso).
  if ! sudo -n grep -q '^PONTO_CHAVE_FOTOS=' "$ETC/rep.env"; then
    echo "PONTO_CHAVE_FOTOS=$(openssl rand -base64 32)" | sudo -n tee -a "$ETC/rep.env" >/dev/null
    echo "[fotos] chave de cifragem gerada em $ETC/rep.env (não exibida). Guarde uma cópia: sem ela, fotos antigas não abrem."
  fi

  escrever_unit "$UNIT_REP" "Pressente Ponto - REP-P" rep.env "reppp.api:criar_app" "$PORTA_REP" ""
  escrever_unit "$UNIT_TRAT" "Pressente Ponto - Programa de Tratamento" ptrp.env "ptrp.api:criar_app" "$PORTA_TRAT" "$UNIT_REP.service"
  sudo -n systemctl daemon-reload
  sudo -n systemctl enable "$UNIT_REP" "$UNIT_TRAT" >/dev/null 2>&1

  # O certificado de TESTE vem antes da 1ª publicação: sem certificado os
  # serviços não sobem (o comprovante e o AFD precisam ser assinados).
  if ! sudo -n test -f "$ETC/certificado-de-teste/teste-certificado.pem"; then
    sudo -n python3 -m venv "$BASE/ferramentas"
    sudo -n "$BASE/ferramentas/bin/pip" install -q --disable-pip-version-check \
      "$(grep -E '^cryptography==' "$NOVO/requirements.txt")"
    sudo -n mkdir -p "$ETC/certificado-de-teste"
    sudo -n "$BASE/ferramentas/bin/python" "$NOVO/scripts/certificado_de_teste.py" "$ETC/certificado-de-teste" >/dev/null
    sudo -n chown -R root:ponto "$ETC/certificado-de-teste" && sudo -n chmod 0750 "$ETC/certificado-de-teste" \
      && sudo -n chmod 0640 "$ETC/certificado-de-teste"/*
    echo "[certificado] de TESTE gerado (sem valor legal, até o e-CNPJ ser instalado)"
  fi
  publicar
  configurar_caddy
  verificar
}

# ─────────────────────────────────────────────────────────────────────────────
trocar_para() {
  local destino="$1"
  sudo -n ln -sfn "$destino" "$BASE/atual.tmp"
  sudo -n mv -T "$BASE/atual.tmp" "$BASE/atual"
  sudo -n systemctl restart "$UNIT_REP" "$UNIT_TRAT"
}

publicar() {
  titulo "Pressente Ponto — publicar"
  precisa_sudo
  [ -d "$NOVO" ] || falha "o código não chegou em $NOVO."
  sudo -n test -f "$ETC/dono.env" || falha "o ponto ainda não foi instalado nesta VM: rode a opção «instalar»."
  local curto rel anterior
  curto="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("commit_curto","sem-commit"))' "$NOVO/VERSAO_PUBLICADA.json" 2>/dev/null || echo sem-commit)"
  rel="$BASE/releases/$(date +%Y%m%d-%H%M%S)-$curto"
  sudo -n mkdir -p "$rel"
  sudo -n cp -a "$NOVO/." "$rel/"
  echo "[instalar dependências] $rel"
  sudo -n python3 -m venv "$rel/.venv"
  sudo -n "$rel/.venv/bin/pip" install -q --disable-pip-version-check -r "$rel/requirements.txt"
  sudo -n chown -R root:root "$rel" && sudo -n chmod -R a+rX "$rel"
  echo "[banco] migrando com o usuário dono"
  sudo -n bash -c "set -a; . '$ETC/dono.env'; set +a; cd '$rel' && .venv/bin/python -m scripts.migrar"
  # Os dados públicos do desenvolvedor (nome, CNPJ, nº do INPI, quem assina o
  # atestado) vêm do repositório: implantacao/desenvolvedor.env.
  if [ -f "$rel/implantacao/desenvolvedor.env" ]; then
    sudo -n install -o root -g ponto -m 0640 "$rel/implantacao/desenvolvedor.env" "$ETC/desenvolvedor.env"
  fi
  anterior="$(versao_no_ar)"
  trocar_para "$rel"
  if saudavel; then
    if [ -n "$anterior" ] && [ "$anterior" != "$rel" ]; then
      echo "$anterior" | sudo -n tee "$BASE/anterior" >/dev/null
    fi
    # guarda as 5 versões mais novas
    ls -1dt "$BASE"/releases/* 2>/dev/null | tail -n +6 | while read -r velha; do
      [ "$velha" = "$rel" ] || [ "$velha" = "$anterior" ] || sudo -n rm -rf "$velha"
    done
    echo "[ok] no ar: $curto"
  else
    echo "[FALHA] a versão nova não respondeu; voltando para a anterior"
    sudo -n journalctl -u "$UNIT_REP" -u "$UNIT_TRAT" -n 30 --no-pager 2>/dev/null | sed -E 's#postgresql://[^ ]+#postgresql://***#g' || true
    if [ -n "$anterior" ] && [ -d "$anterior" ]; then
      trocar_para "$anterior"
      saudavel && echo "[ok] voltou para $(basename "$anterior")" || echo "[FALHA] nem a anterior respondeu"
    fi
    exit 1
  fi
}

voltar() {
  titulo "Pressente Ponto — voltar para a versão anterior"
  precisa_sudo
  local anterior atual
  anterior="$(anterior_valida || true)"
  [ -n "$anterior" ] || falha "não há versão anterior válida guardada. Nada mudou."
  atual="$(versao_no_ar)"
  [ -n "$atual" ] || falha "não há versão no ar para guardar como anterior. Nada mudou."
  trocar_para "$anterior"
  saudavel || { trocar_para "$atual"; falha "a anterior não respondeu; mantive a atual."; }
  echo "$atual" | sudo -n tee "$BASE/anterior" >/dev/null
  echo "[ok] no ar: $(basename "$anterior")"
}

backup() {
  titulo "Pressente Ponto — backup do banco"
  precisa_sudo
  local destino="$HOME/backups-ponto" arq
  mkdir -p "$destino"
  arq="$destino/$BANCO-$(date +%Y%m%d-%H%M%S).dump"
  sudo -n -u postgres pg_dump -Fc "$BANCO" > "$arq"
  chmod 0600 "$arq"
  ls -1t "$destino"/*.dump | tail -n +15 | xargs -r rm -f
  echo "[ok] $(basename "$arq") ($(du -h "$arq" | cut -f1)); guardados: $(ls -1 "$destino"/*.dump | wc -l)"
  # Fotos com prazo de guarda vencido saem (LGPD); a batida e o hash da foto ficam.
  local cur
  cur="$(readlink -f "$BASE/atual")"
  sudo -n bash -c "set -a; . '$ETC/dono.env'; set +a; cd '$cur' && .venv/bin/python -m scripts.expurgar_fotos"
}

# ─────────────────────────────────────────────────────────────────────────────
cadastrar() {
  titulo "Pressente Ponto — cadastrar empresa (estabelecimento)"
  precisa_sudo
  sudo -n test -f "$ETC/rep.env" || falha "o ponto ainda não foi instalado nesta VM: rode a opção «instalar»."
  local adm
  adm="$(sudo -n grep '^PONTO_CHAVE_ADMINISTRACAO=' "$ETC/rep.env" | cut -d= -f2-)"
  [ -n "$adm" ] || falha "a chave de administração não está em $ETC/rep.env."
  ADM="$adm" python3 - <<'PY'
import json, os, urllib.error, urllib.request
corpo = {"tipo_identificador": 1 if len("".join(c for c in os.environ.get("CAD_ID", "") if c.isalnum())) == 14 else 2,
         "identificador": os.environ.get("CAD_ID", ""), "razao_social": os.environ.get("CAD_RAZAO", ""),
         "cpf_responsavel": os.environ.get("CAD_CPF", ""), "fuso": os.environ.get("CAD_FUSO") or "America/Cuiaba",
         "local_prestacao": os.environ.get("CAD_LOCAL", "")}
pedido = urllib.request.Request("http://127.0.0.1:8710/v1/estabelecimentos", data=json.dumps(corpo).encode(),
                                headers={"Content-Type": "application/json", "X-Chave-Administracao": os.environ["ADM"]},
                                method="POST")
try:
    with urllib.request.urlopen(pedido, timeout=30) as r:
        d = json.load(r)
except urllib.error.HTTPError as e:
    detalhe = json.loads(e.read() or b"{}").get("detail")
    raise SystemExit(f"ERRO: o REP-P recusou ({e.code}): {detalhe}")
print(f"[ok] empresa cadastrada (estabelecimento {d['id']}).")
print("")
print("CHAVE DO ESTABELECIMENTO (aparece só agora — copie para o ExxenOS, em Ponto › Pressente Ponto):")
print(d["chave"])
print("")
print("Depois de copiar, apague o registro desta execução no GitHub (… › Delete all logs).")
PY
}

case "$ACAO" in
  verificar) verificar ;;
  instalar) instalar ;;
  publicar) publicar; verificar ;;
  voltar) voltar; verificar ;;
  backup) backup ;;
  cadastrar) cadastrar ;;
  *) falha "ação desconhecida: $ACAO" ;;
esac
