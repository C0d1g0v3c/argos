-- =====================================================================
-- ARGOS · Esquema de datos
-- Vigilancia conductual de leaderboards de copy trading
-- Motor: PostgreSQL 16 + TimescaleDB 2.x
--
-- Principio de diseño: las tablas de evidencia son append-only.
-- Si se pueden editar, el experimento no vale. Los triggers al final
-- del archivo lo hacen cumplir a nivel de motor, no de disciplina.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS argos;
SET search_path TO argos, public;


-- =====================================================================
-- 1. REGISTRO DE LÍDERES
-- =====================================================================

CREATE TABLE leaders (
    leader_id       BIGSERIAL PRIMARY KEY,
    platform        TEXT NOT NULL,              -- 'binance' | 'bybit'
    platform_uid    TEXT NOT NULL,              -- id en la plataforma
    display_name    TEXT,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    instrument_type TEXT,                       -- 'spot' | 'perp' | 'mixto'
    UNIQUE (platform, platform_uid)
);

COMMENT ON COLUMN leaders.instrument_type IS
  'Determina el tratamiento fiscal: spot = enajenación de bienes, perp = régimen de derivados.';


-- =====================================================================
-- 2. COHORTE CONGELADO — el grupo de control
-- =====================================================================
-- Se escribe UNA sola vez, el día 1. Nunca se re-selecciona.
-- El hash sella la composición: si alguien la altera, deja de coincidir.

CREATE TABLE cohort_snapshot (
    snapshot_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    frozen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    platform        TEXT NOT NULL,
    criteria        JSONB NOT NULL,             -- criterio exacto de selección
    member_count    INT  NOT NULL,
    composition_hash TEXT NOT NULL              -- sha256 de los uids ordenados
);

CREATE TABLE cohort_member (
    snapshot_id     UUID NOT NULL REFERENCES cohort_snapshot(snapshot_id),
    leader_id       BIGINT NOT NULL REFERENCES leaders(leader_id),
    rank_at_freeze  INT NOT NULL,
    metrics_at_freeze JSONB NOT NULL,           -- lo que mostraba el leaderboard ese día
    PRIMARY KEY (snapshot_id, leader_id)
);


-- =====================================================================
-- 3. SERIES TEMPORALES
-- =====================================================================

CREATE TABLE market_data (
    ts          TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    open        NUMERIC(20,8) NOT NULL,
    high        NUMERIC(20,8) NOT NULL,
    low         NUMERIC(20,8) NOT NULL,
    close       NUMERIC(20,8) NOT NULL,
    volume      NUMERIC(28,8) NOT NULL,
    PRIMARY KEY (symbol, ts)
);
SELECT create_hypertable('market_data','ts', chunk_time_interval => INTERVAL '7 days');

-- Profundidad del libro: insumo del modelo de slippage.
CREATE TABLE orderbook_depth (
    ts              TIMESTAMPTZ NOT NULL,
    symbol          TEXT NOT NULL,
    bid_px          NUMERIC(20,8) NOT NULL,
    ask_px          NUMERIC(20,8) NOT NULL,
    bid_qty_5bp     NUMERIC(28,8),              -- volumen dentro de 5 pb del mid
    ask_qty_5bp     NUMERIC(28,8),
    daily_volume    NUMERIC(28,8),              -- V para G ≈ σ·(Q/V)^0.5
    realized_vol    NUMERIC(12,8),              -- σ
    PRIMARY KEY (symbol, ts)
);
SELECT create_hypertable('orderbook_depth','ts', chunk_time_interval => INTERVAL '1 day');

-- Trades observados de los líderes.
CREATE TABLE leader_trade (
    ts              TIMESTAMPTZ NOT NULL,       -- momento de apertura del líder
    leader_id       BIGINT NOT NULL REFERENCES leaders(leader_id),
    trade_uid       TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('long','short')),
    entry_px        NUMERIC(20,8) NOT NULL,
    exit_px         NUMERIC(20,8),
    closed_at       TIMESTAMPTZ,
    notional_usd    NUMERIC(20,4),
    leverage        NUMERIC(8,2),
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),  -- cuándo LO VIMOS
    PRIMARY KEY (leader_id, trade_uid, ts)
);
SELECT create_hypertable('leader_trade','ts', chunk_time_interval => INTERVAL '30 days');

COMMENT ON COLUMN leader_trade.observed_at IS
  'observed_at - ts = latencia de observación. Es el piso de la latencia de copia: '
  'no puedes actuar antes de haberlo visto. Métrica central del experimento.';


-- =====================================================================
-- 4. EJECUCIÓN
-- =====================================================================

-- Fills reales. Aquí viven los 500 pesos.
CREATE TABLE real_fill (
    fill_id         BIGSERIAL PRIMARY KEY,
    leader_id       BIGINT REFERENCES leaders(leader_id),
    trade_uid       TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    leader_ts       TIMESTAMPTZ,                -- apertura del líder
    my_ts           TIMESTAMPTZ NOT NULL,       -- mi fill
    leader_px       NUMERIC(20,8),
    my_px           NUMERIC(20,8) NOT NULL,
    qty             NUMERIC(28,8) NOT NULL,
    fee_mxn         NUMERIC(14,4) NOT NULL DEFAULT 0,
    fx_dof          NUMERIC(12,6),              -- TC del DOF, para el ledger fiscal
    notes           TEXT
);

-- Columnas derivadas: el resultado del experimento de calibración.
CREATE VIEW fill_slippage AS
SELECT
    fill_id, symbol, side,
    EXTRACT(EPOCH FROM (my_ts - leader_ts))              AS copy_latency_s,
    (my_px - leader_px) / NULLIF(leader_px,0) * 10000    AS slippage_bp_signed,
    CASE WHEN side='long' THEN  (my_px - leader_px)/NULLIF(leader_px,0)*10000
         ELSE                   (leader_px - my_px)/NULLIF(leader_px,0)*10000
    END                                                   AS slippage_bp_adverse,
    qty, fee_mxn
FROM real_fill
WHERE leader_px IS NOT NULL;

-- Fills simulados, para contrastar contra los reales.
CREATE TABLE paper_fill (
    fill_id         BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL,
    leader_id       BIGINT REFERENCES leaders(leader_id),
    trade_uid       TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    latency_scenario_s INT NOT NULL,            -- 1 | 5 | 30
    modeled_px      NUMERIC(20,8) NOT NULL,
    modeled_slip_bp NUMERIC(12,4) NOT NULL,     -- lo que predijo el modelo
    qty             NUMERIC(28,8) NOT NULL,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- =====================================================================
-- 5. VIGILANCIA — el módulo que conecta con SOC
-- =====================================================================

CREATE TABLE detection_rule (
    rule_id         TEXT PRIMARY KEY,           -- 'ARG-001'
    name            TEXT NOT NULL,
    technique       TEXT NOT NULL,              -- 'benford' | 'roundedness' | 'graph_cycle' | 'volume_spike'
    description     TEXT,
    severity        TEXT CHECK (severity IN ('info','low','medium','high')),
    version         INT NOT NULL DEFAULT 1,
    enabled         BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE surveillance_alert (
    alert_id        BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    leader_id       BIGINT NOT NULL REFERENCES leaders(leader_id),
    rule_id         TEXT NOT NULL REFERENCES detection_rule(rule_id),
    score           NUMERIC(8,5) NOT NULL,
    evidence        JSONB NOT NULL,             -- qué disparó la regla
    -- triage: el problema de falsos positivos, igual que en un SOC
    triage_state    TEXT NOT NULL DEFAULT 'nuevo'
                    CHECK (triage_state IN ('nuevo','revisado','confirmado','falso_positivo')),
    triaged_at      TIMESTAMPTZ,
    triage_note     TEXT
);
CREATE INDEX ON surveillance_alert (leader_id, ts DESC);
CREATE INDEX ON surveillance_alert (triage_state) WHERE triage_state = 'nuevo';

COMMENT ON TABLE surveillance_alert IS
  'Etiquetar triage_state a mano genera el dataset supervisado para el meta-modelo '
  'de reducción de falsos positivos. Es el mismo problema que la fatiga de alertas en un SOC.';


-- =====================================================================
-- 6. AUDITORÍA DE FALSIFICACIÓN
-- =====================================================================

CREATE TABLE falsification_run (
    run_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    pipeline_hash   TEXT NOT NULL,              -- hash del código evaluado
    environment     TEXT NOT NULL CHECK (environment IN ('martingala','placebo_micro','real')),
    n_specs_tried   INT NOT NULL,
    best_sharpe_is  NUMERIC(10,5),
    best_sharpe_oos NUMERIC(10,5),
    deflated_sharpe NUMERIC(10,5),
    verdict         TEXT NOT NULL CHECK (verdict IN ('pasa','falsificado')),
    detail          JSONB
);

COMMENT ON TABLE falsification_run IS
  'Si el pipeline produce resultados significativos sobre entornos sin señal, '
  'verdict = falsificado y ningún resultado real debe reportarse.';


-- =====================================================================
-- 7. BITÁCORA DE DECISIONES — append-only
-- =====================================================================

CREATE TABLE decision_log (
    decision_id     BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor           TEXT NOT NULL,              -- 'metalabel_v3' | 'manual' | 'baseline'
    action          TEXT NOT NULL,              -- 'seguir' | 'saltar' | 'cerrar'
    leader_id       BIGINT REFERENCES leaders(leader_id),
    trade_uid       TEXT,
    inputs          JSONB NOT NULL,             -- features exactos en ese instante
    model_version   TEXT,
    confidence      NUMERIC(6,5),
    -- se llena DESPUÉS; la predicción se registra antes del resultado
    outcome         NUMERIC(14,6),
    outcome_ts      TIMESTAMPTZ
);
CREATE INDEX ON decision_log (ts DESC);


-- =====================================================================
-- 8. LEDGER FISCAL
-- =====================================================================
-- Cada enajenación es un evento gravable, incluidos los swaps cripto-cripto.
-- Registrarlo al momento cuesta poco; reconstruirlo en abril cuesta mucho.

CREATE TABLE tax_event (
    event_id        BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL,
    event_type      TEXT NOT NULL CHECK (event_type IN
                      ('compra','venta','swap','fee','funding','liquidacion')),
    regime          TEXT CHECK (regime IN ('enajenacion_bienes','derivados','otros')),
    symbol_out      TEXT,
    qty_out         NUMERIC(28,8),
    symbol_in       TEXT,
    qty_in          NUMERIC(28,8),
    fx_dof          NUMERIC(12,6) NOT NULL,     -- TC del DOF de la fecha
    value_mxn       NUMERIC(16,4) NOT NULL,
    cost_basis_mxn  NUMERIC(16,4),
    gain_mxn        NUMERIC(16,4) GENERATED ALWAYS AS
                      (value_mxn - COALESCE(cost_basis_mxn,0)) STORED,
    fiscal_year     INT NOT NULL,
    source_fill_id  BIGINT REFERENCES real_fill(fill_id)
);
CREATE INDEX ON tax_event (fiscal_year, regime);


-- =====================================================================
-- 9. INMUTABILIDAD
-- =====================================================================

CREATE OR REPLACE FUNCTION argos.deny_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
      'Tabla append-only: % no admite % (integridad del experimento)',
      TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'cohort_snapshot','cohort_member','falsification_run','real_fill','tax_event'
    ] LOOP
        EXECUTE format(
          'CREATE TRIGGER %I_immutable BEFORE UPDATE OR DELETE ON argos.%I
           FOR EACH ROW EXECUTE FUNCTION argos.deny_mutation()', t, t);
    END LOOP;
END $$;

-- decision_log permite UPDATE solo para llenar el resultado, nunca los inputs.
CREATE OR REPLACE FUNCTION argos.decision_log_guard() RETURNS trigger AS $$
BEGIN
    IF NEW.inputs IS DISTINCT FROM OLD.inputs
       OR NEW.action IS DISTINCT FROM OLD.action
       OR NEW.ts IS DISTINCT FROM OLD.ts THEN
        RAISE EXCEPTION 'Los inputs y la acción registrada no se pueden modificar';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER decision_log_guard BEFORE UPDATE ON decision_log
FOR EACH ROW EXECUTE FUNCTION argos.decision_log_guard();


-- =====================================================================
-- 10. RETENCIÓN Y COMPRESIÓN
-- =====================================================================

ALTER TABLE market_data SET (timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol');
SELECT add_compression_policy('market_data', INTERVAL '30 days');

ALTER TABLE orderbook_depth SET (timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol');
SELECT add_compression_policy('orderbook_depth', INTERVAL '7 days');
