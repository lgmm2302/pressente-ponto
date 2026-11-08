-- Pressente Ponto — Programa de Tratamento de Registro de Ponto (PTRP).
--
-- O PTRP NUNCA mexe nas tabelas do REP-P (`registros`, `comprovantes`): ele
-- só as lê. Tudo o que o tratamento faz — incluir uma marcação esquecida,
-- desconsiderar uma batida em duplicidade, abonar uma falta — é um registro
-- NOVO aqui, com motivo, autor e data, e a ciência do empregado.
--
-- Nada destas tabelas se altera nem se apaga: mudar um horário é criar outro
-- código; mudar regra ou escala é gravar uma nova com vigência; desfazer um
-- tratamento é gravar um tratamento «revogar». A história fica inteira.

CREATE TABLE IF NOT EXISTS ptrp_horarios (
    id                       BIGSERIAL PRIMARY KEY,
    estabelecimento_id       BIGINT NOT NULL REFERENCES estabelecimentos(id),
    codigo                   TEXT NOT NULL,
    periodos                 JSONB NOT NULL,
    intervalo_pre_assinalado BOOLEAN NOT NULL DEFAULT false,
    autor_cpf                TEXT NOT NULL,
    criado_em                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (estabelecimento_id, codigo)
);

CREATE TABLE IF NOT EXISTS ptrp_regras (
    id                  BIGSERIAL PRIMARY KEY,
    estabelecimento_id  BIGINT NOT NULL REFERENCES estabelecimentos(id),
    vigencia            DATE NOT NULL,
    regras              JSONB NOT NULL,
    autor_cpf           TEXT NOT NULL,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (estabelecimento_id, vigencia)
);

CREATE TABLE IF NOT EXISTS ptrp_escalas (
    id                  BIGSERIAL PRIMARY KEY,
    estabelecimento_id  BIGINT NOT NULL REFERENCES estabelecimentos(id),
    cpf                 TEXT NOT NULL,
    vigencia            DATE NOT NULL,
    semana              JSONB NOT NULL,  -- {"0": "COM", ..., "5": "FOLGA", "6": "DSR"}; 0 = segunda
    autor_cpf           TEXT NOT NULL,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (estabelecimento_id, cpf, vigencia)
);

CREATE TABLE IF NOT EXISTS ptrp_feriados (
    id                  BIGSERIAL PRIMARY KEY,
    estabelecimento_id  BIGINT NOT NULL REFERENCES estabelecimentos(id),
    data                DATE NOT NULL,
    descricao           TEXT NOT NULL,
    ativo               BOOLEAN NOT NULL DEFAULT true,
    autor_cpf           TEXT NOT NULL,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ptrp_vinculos (
    estabelecimento_id  BIGINT NOT NULL REFERENCES estabelecimentos(id),
    cpf                 TEXT NOT NULL,
    matricula_esocial   TEXT NOT NULL,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (estabelecimento_id, cpf)
);

CREATE TABLE IF NOT EXISTS ptrp_tratamentos (
    id                  BIGSERIAL PRIMARY KEY,
    estabelecimento_id  BIGINT NOT NULL REFERENCES estabelecimentos(id),
    cpf                 TEXT NOT NULL,
    tipo                TEXT NOT NULL CHECK (tipo IN ('incluir', 'desconsiderar', 'abonar', 'revogar')),
    momento             TIMESTAMPTZ,                               -- incluir
    registro_id         BIGINT REFERENCES registros(id),           -- desconsiderar
    data                DATE,                                      -- abonar
    alvo_id             BIGINT REFERENCES ptrp_tratamentos(id),    -- revogar
    motivo              TEXT NOT NULL CHECK (length(btrim(motivo)) >= 3),
    autor_cpf           TEXT NOT NULL,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((tipo = 'incluir' AND momento IS NOT NULL) OR (tipo = 'desconsiderar' AND registro_id IS NOT NULL)
        OR (tipo = 'abonar' AND data IS NOT NULL) OR (tipo = 'revogar' AND alvo_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ptrp_tratamentos_cpf ON ptrp_tratamentos (estabelecimento_id, cpf);

CREATE TABLE IF NOT EXISTS ptrp_ciencias (
    id                  BIGSERIAL PRIMARY KEY,
    tratamento_id       BIGINT NOT NULL UNIQUE REFERENCES ptrp_tratamentos(id),
    decisao             TEXT NOT NULL CHECK (decisao IN ('ciente', 'recusado')),
    comentario          TEXT NOT NULL DEFAULT '',
    origem              JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ptrp_espelhos (
    id                  BIGSERIAL PRIMARY KEY,
    estabelecimento_id  BIGINT NOT NULL REFERENCES estabelecimentos(id),
    cpf                 TEXT NOT NULL,
    competencia         TEXT NOT NULL CHECK (competencia ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    versao              INTEGER NOT NULL,
    pdf                 BYTEA NOT NULL,
    resumo              JSONB NOT NULL,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (estabelecimento_id, cpf, competencia, versao)
);

CREATE TABLE IF NOT EXISTS ptrp_aceites (
    id                  BIGSERIAL PRIMARY KEY,
    espelho_id          BIGINT NOT NULL UNIQUE REFERENCES ptrp_espelhos(id),
    decisao             TEXT NOT NULL CHECK (decisao IN ('aceito', 'contestado')),
    comentario          TEXT NOT NULL DEFAULT '',
    origem              JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['ptrp_horarios', 'ptrp_regras', 'ptrp_escalas', 'ptrp_feriados', 'ptrp_vinculos',
                             'ptrp_tratamentos', 'ptrp_ciencias', 'ptrp_espelhos', 'ptrp_aceites'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_imutavel', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION ponto_imutavel()',
                       t || '_imutavel', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_sem_truncate', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION ponto_imutavel()',
                       t || '_sem_truncate', t);
    END LOOP;
END $$;
