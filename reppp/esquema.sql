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
