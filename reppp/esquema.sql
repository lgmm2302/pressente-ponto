-- Pressente Ponto — o armazenamento dos registros de ponto (ARP).
--
-- Regra que este esquema impõe sozinho, sem depender do código: o que foi
-- gravado em `registros` e `comprovantes` NUNCA muda nem some. UPDATE, DELETE
-- e TRUNCATE nessas tabelas param com erro (Portaria 671, art. 74 e Anexo IX:
-- os dados do ARP não podem ser apagados nem alterados).
--
-- O NSR é sequencial por estabelecimento, começando em 1, sem buracos: quem
-- grava trava a linha do estabelecimento (`SELECT … FOR UPDATE`) e a
-- unicidade (estabelecimento, nsr) recusa duplicata.

CREATE TABLE IF NOT EXISTS estabelecimentos (
    id                  BIGSERIAL PRIMARY KEY,
    tipo_identificador  SMALLINT NOT NULL CHECK (tipo_identificador IN (1, 2)),
    identificador       TEXT NOT NULL UNIQUE,
    cno_caepf           TEXT,
    razao_social        TEXT NOT NULL,
    local_prestacao     TEXT NOT NULL DEFAULT '',
    fuso                TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
    ultimo_nsr          BIGINT NOT NULL DEFAULT 0,
    ultimo_hash         TEXT,
    chave_api_hash      TEXT UNIQUE,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS empregados (
    id                  BIGSERIAL PRIMARY KEY,
    estabelecimento_id  BIGINT NOT NULL REFERENCES estabelecimentos(id),
    cpf                 TEXT NOT NULL,
    nome                TEXT NOT NULL,
    ativo               BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (estabelecimento_id, cpf)
);

CREATE TABLE IF NOT EXISTS registros (
    id                  BIGSERIAL PRIMARY KEY,
    estabelecimento_id  BIGINT NOT NULL REFERENCES estabelecimentos(id),
    nsr                 BIGINT NOT NULL CHECK (nsr >= 1),
    tipo                SMALLINT NOT NULL CHECK (tipo IN (2, 4, 5, 6, 7)),
    gravado_em          TIMESTAMPTZ NOT NULL,
    marcado_em          TIMESTAMPTZ,
    cpf                 TEXT,
    coletor             SMALLINT,
    offline             BOOLEAN,
    hash                TEXT,
    hash_anterior       TEXT,
    origem              JSONB NOT NULL DEFAULT '{}'::jsonb,
    linha               TEXT NOT NULL,
    UNIQUE (estabelecimento_id, nsr)
);
CREATE INDEX IF NOT EXISTS registros_por_dia ON registros (estabelecimento_id, gravado_em);
CREATE INDEX IF NOT EXISTS registros_por_cpf ON registros (estabelecimento_id, cpf, marcado_em);

CREATE TABLE IF NOT EXISTS comprovantes (
    registro_id         BIGINT PRIMARY KEY REFERENCES registros(id),
    pdf                 BYTEA NOT NULL,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION ponto_imutavel() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'registro de ponto é imutável: % em % recusado', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'P0001';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS registros_imutaveis ON registros;
CREATE TRIGGER registros_imutaveis BEFORE UPDATE OR DELETE ON registros
    FOR EACH ROW EXECUTE FUNCTION ponto_imutavel();
DROP TRIGGER IF EXISTS registros_sem_truncate ON registros;
CREATE TRIGGER registros_sem_truncate BEFORE TRUNCATE ON registros
    FOR EACH STATEMENT EXECUTE FUNCTION ponto_imutavel();

DROP TRIGGER IF EXISTS comprovantes_imutaveis ON comprovantes;
CREATE TRIGGER comprovantes_imutaveis BEFORE UPDATE OR DELETE ON comprovantes
    FOR EACH ROW EXECUTE FUNCTION ponto_imutavel();
DROP TRIGGER IF EXISTS comprovantes_sem_truncate ON comprovantes;
CREATE TRIGGER comprovantes_sem_truncate BEFORE TRUNCATE ON comprovantes
    FOR EACH STATEMENT EXECUTE FUNCTION ponto_imutavel();

-- ─────────────────────────────────────────────────────────────────────────────
-- A prova da batida (fase 7). O resumo da prova vai em `registros.origem`
-- (imutável com o registro) e o hash dele em `prova_sha256`, que a
-- conferência recalcula. A foto fica à parte, cifrada, e é a ÚNICA coisa do
-- ARP que pode sair — e só depois do prazo de guarda que a empresa definiu
-- (LGPD: dado biométrico não fica além do necessário).
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE registros ADD COLUMN IF NOT EXISTS prova_sha256 TEXT;

CREATE TABLE IF NOT EXISTS provas_config (
    id                  BIGSERIAL PRIMARY KEY,
    estabelecimento_id  BIGINT NOT NULL REFERENCES estabelecimentos(id),
    config              JSONB NOT NULL,
    autor_cpf           TEXT NOT NULL,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cercas (
    id                  BIGSERIAL PRIMARY KEY,
    estabelecimento_id  BIGINT NOT NULL REFERENCES estabelecimentos(id),
    nome                TEXT NOT NULL,
    latitude            DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude           DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    raio_m              INTEGER NOT NULL CHECK (raio_m BETWEEN 10 AND 5000),
    ativa               BOOLEAN NOT NULL DEFAULT true,
    autor_cpf           TEXT NOT NULL,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    desativada_em       TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS cercas_nome_ativa ON cercas (estabelecimento_id, nome) WHERE ativa;

CREATE TABLE IF NOT EXISTS fotos (
    registro_id         BIGINT PRIMARY KEY REFERENCES registros(id),
    nonce               BYTEA NOT NULL,
    cifrada             BYTEA NOT NULL,
    sha256              TEXT NOT NULL,
    tipo                TEXT NOT NULL,
    expira_em           TIMESTAMPTZ,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS acessos_a_fotos (
    id                  BIGSERIAL PRIMARY KEY,
    registro_id         BIGINT NOT NULL REFERENCES registros(id),
    quem_cpf            TEXT NOT NULL,
    motivo              TEXT NOT NULL CHECK (length(btrim(motivo)) >= 3),
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION ponto_foto_expurgavel() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND OLD.expira_em IS NOT NULL AND OLD.expira_em <= now() THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'foto da batida é imutável (só sai depois do prazo de guarda): % recusado', TG_OP
        USING ERRCODE = 'P0001';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fotos_imutaveis ON fotos;
CREATE TRIGGER fotos_imutaveis BEFORE UPDATE OR DELETE ON fotos
    FOR EACH ROW EXECUTE FUNCTION ponto_foto_expurgavel();
DROP TRIGGER IF EXISTS fotos_sem_truncate ON fotos;
CREATE TRIGGER fotos_sem_truncate BEFORE TRUNCATE ON fotos
    FOR EACH STATEMENT EXECUTE FUNCTION ponto_imutavel();

DROP TRIGGER IF EXISTS provas_config_imutavel ON provas_config;
CREATE TRIGGER provas_config_imutavel BEFORE UPDATE OR DELETE ON provas_config
    FOR EACH ROW EXECUTE FUNCTION ponto_imutavel();
DROP TRIGGER IF EXISTS acessos_a_fotos_imutaveis ON acessos_a_fotos;
CREATE TRIGGER acessos_a_fotos_imutaveis BEFORE UPDATE OR DELETE ON acessos_a_fotos
    FOR EACH ROW EXECUTE FUNCTION ponto_imutavel();
