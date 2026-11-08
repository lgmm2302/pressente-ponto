-- Permissões de produção: o programa de tratamento (PTRP) só LÊ o REP-P.
--
-- Rodar como dono do banco, depois que os dois esquemas existirem. As senhas
-- dos dois usuários são definidas pelo Luiz no servidor (nunca aqui).
--
--   ponto_rep  → o REP-P: grava registros e comprovantes (as travas do banco
--                continuam impedindo UPDATE e DELETE).
--   ponto_ptrp → o tratamento: lê o REP-P e grava só nas tabelas ptrp_*.

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ponto_rep') THEN CREATE ROLE ponto_rep LOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ponto_ptrp') THEN CREATE ROLE ponto_ptrp LOGIN; END IF;
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ponto_rep, ponto_ptrp;

GRANT SELECT, INSERT, UPDATE ON estabelecimentos, empregados TO ponto_rep;
GRANT SELECT, INSERT ON registros, comprovantes, provas_config, acessos_a_fotos TO ponto_rep;
GRANT SELECT, INSERT, UPDATE ON cercas TO ponto_rep;
GRANT SELECT, INSERT, DELETE ON fotos TO ponto_rep;  -- DELETE só passa depois do prazo (trigger)
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO ponto_rep;

GRANT SELECT ON estabelecimentos, empregados, registros TO ponto_ptrp;
GRANT SELECT, INSERT ON ptrp_horarios, ptrp_regras, ptrp_escalas, ptrp_feriados, ptrp_vinculos,
    ptrp_tratamentos, ptrp_ciencias, ptrp_espelhos, ptrp_aceites TO ponto_ptrp;
GRANT USAGE ON SEQUENCE ptrp_horarios_id_seq, ptrp_regras_id_seq, ptrp_escalas_id_seq, ptrp_feriados_id_seq,
    ptrp_tratamentos_id_seq, ptrp_ciencias_id_seq, ptrp_espelhos_id_seq, ptrp_aceites_id_seq TO ponto_ptrp;
