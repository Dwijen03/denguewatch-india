-- =====================================================================
-- DengueWatch India - PostgreSQL schema
-- Implements the ER design in docs/diagrams/er_diagram.png (Step 2).
-- Safe to run more than once: every object is created only if missing.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Geography
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS states (
    state_id    SMALLSERIAL PRIMARY KEY,
    state_name  VARCHAR(60) NOT NULL UNIQUE,
    state_code  CHAR(2)     NOT NULL UNIQUE
);

-- Canonical districts: the 640 Census 2011 districts (stable geography for 2009-2026)
CREATE TABLE IF NOT EXISTS districts (
    district_id    SERIAL PRIMARY KEY,
    census_code    INTEGER      NOT NULL UNIQUE,          -- Census 2011 district code
    state_id       SMALLINT     NOT NULL REFERENCES states(state_id),
    district_name  VARCHAR(60)  NOT NULL,
    latitude       NUMERIC(7,4) NOT NULL CHECK (latitude  BETWEEN 6 AND 38),
    longitude      NUMERIC(7,4) NOT NULL CHECK (longitude BETWEEN 68 AND 98),
    UNIQUE (state_id, district_name)
);

-- Other names used in reports (renamed districts, districts created after 2011)
CREATE TABLE IF NOT EXISTS district_aliases (
    alias_id     SERIAL PRIMARY KEY,
    district_id  INTEGER     NOT NULL REFERENCES districts(district_id) ON DELETE CASCADE,
    state_id     SMALLINT    NOT NULL REFERENCES states(state_id),
    alias_name   VARCHAR(80) NOT NULL,
    reason       VARCHAR(80),
    UNIQUE (state_id, alias_name)
);

-- ---------------------------------------------------------------------
-- 2. Surveillance data (IDSP)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS diseases (
    disease_id    SMALLSERIAL PRIMARY KEY,
    disease_name  VARCHAR(40) NOT NULL UNIQUE
);

-- One row per weekly IDSP report
CREATE TABLE IF NOT EXISTS reports (
    report_id      SERIAL PRIMARY KEY,
    year           SMALLINT    NOT NULL CHECK (year BETWEEN 2009 AND 2100),
    week_no        SMALLINT    NOT NULL CHECK (week_no BETWEEN 1 AND 53),
    week_start     DATE        NOT NULL,
    week_end       DATE        NOT NULL,
    source_url     TEXT,
    file_name      VARCHAR(40) NOT NULL UNIQUE,
    file_hash      CHAR(64),
    dengue_rows    INTEGER,
    parse_status   VARCHAR(20) NOT NULL DEFAULT 'parsed',
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (year, week_no),
    CHECK (week_end = week_start + 6)
);

-- One row per dengue outbreak (duplicates across weeks already removed)
CREATE TABLE IF NOT EXISTS outbreaks (
    outbreak_id        BIGSERIAL PRIMARY KEY,
    outbreak_key       VARCHAR(120) NOT NULL UNIQUE,     -- natural key used for safe re-loading
    report_id          INTEGER      NOT NULL REFERENCES reports(report_id),
    district_id        INTEGER      REFERENCES districts(district_id),  -- NULL = state-wide / unmatched
    state_id           SMALLINT     REFERENCES states(state_id),
    disease_id         SMALLINT     NOT NULL REFERENCES diseases(disease_id),
    idsp_unique_id     VARCHAR(40),
    district_name_raw  VARCHAR(120),
    disease_raw        VARCHAR(120),
    is_suspected       BOOLEAN      NOT NULL DEFAULT FALSE,
    is_mixed           BOOLEAN      NOT NULL DEFAULT FALSE,
    cases              INTEGER      CHECK (cases  >= 0),
    deaths             INTEGER      CHECK (deaths >= 0),
    start_date         DATE,
    reporting_date     DATE,
    report_lag_days    INTEGER,
    current_status     VARCHAR(30),
    action_taken       TEXT,
    times_listed       SMALLINT     NOT NULL DEFAULT 1,
    match_method       VARCHAR(20),
    needs_review       BOOLEAN      NOT NULL DEFAULT FALSE,
    review_reasons     TEXT,
    source_page        SMALLINT
);

-- ---------------------------------------------------------------------
-- 3. Weather (Step 6): NASA POWER history + Open-Meteo recent days and forecast
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_daily (
    district_id       INTEGER     NOT NULL REFERENCES districts(district_id) ON DELETE CASCADE,
    obs_date          DATE        NOT NULL,
    precip_mm         NUMERIC(6,1) CHECK (precip_mm >= 0),
    temp_mean_c       NUMERIC(4,1),
    temp_min_c        NUMERIC(4,1),
    temp_max_c        NUMERIC(4,1),
    rel_humidity_pct  NUMERIC(4,1) CHECK (rel_humidity_pct BETWEEN 0 AND 100),
    data_type         VARCHAR(10) NOT NULL DEFAULT 'observed' CHECK (data_type IN ('observed', 'forecast')),
    source            VARCHAR(20) NOT NULL DEFAULT 'nasa_power' CHECK (source IN ('nasa_power', 'open_meteo')),
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (district_id, obs_date)
);
-- (for databases created before Step 6)
ALTER TABLE weather_daily ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'nasa_power'
    CHECK (source IN ('nasa_power', 'open_meteo'));

-- ---------------------------------------------------------------------
-- 4. Machine learning (filled in Step 7)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_versions (
    model_id      SERIAL PRIMARY KEY,
    algorithm     VARCHAR(60) NOT NULL,
    train_period  VARCHAR(40),
    test_metrics  JSONB,
    file_path     TEXT,
    trained_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active     BOOLEAN     NOT NULL DEFAULT FALSE
);
-- Only one model can be active at a time
CREATE UNIQUE INDEX IF NOT EXISTS one_active_model ON model_versions (is_active) WHERE is_active;

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id      BIGSERIAL PRIMARY KEY,
    model_id           INTEGER  NOT NULL REFERENCES model_versions(model_id) ON DELETE CASCADE,
    district_id        INTEGER  NOT NULL REFERENCES districts(district_id),
    target_week_start  DATE     NOT NULL,
    horizon_weeks      SMALLINT NOT NULL DEFAULT 4,
    risk_score         NUMERIC(5,4) NOT NULL CHECK (risk_score BETWEEN 0 AND 1),
    risk_level         VARCHAR(10)  NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    top_factors        JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_id, district_id, target_week_start)
);

-- ---------------------------------------------------------------------
-- 5. Optional AI agent (Step 11) and operations log
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS news_signals (
    signal_id            BIGSERIAL PRIMARY KEY,
    district_id          INTEGER REFERENCES districts(district_id),
    published_at         TIMESTAMPTZ,
    headline             TEXT NOT NULL,
    url                  TEXT NOT NULL UNIQUE,
    extracted_cases      INTEGER CHECK (extracted_cases >= 0),
    verification_status  VARCHAR(20) NOT NULL DEFAULT 'unverified'
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id       BIGSERIAL PRIMARY KEY,
    job_name     VARCHAR(40) NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       VARCHAR(10) NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'success', 'failed')),
    rows_added   INTEGER,
    message      TEXT
);

-- ---------------------------------------------------------------------
-- 6. Indexes for the queries the API and dashboard run most
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_outbreaks_district_start ON outbreaks (district_id, start_date);
CREATE INDEX IF NOT EXISTS idx_outbreaks_state_start    ON outbreaks (state_id, start_date);
CREATE INDEX IF NOT EXISTS idx_outbreaks_report         ON outbreaks (report_id);
CREATE INDEX IF NOT EXISTS idx_districts_state          ON districts (state_id);
CREATE INDEX IF NOT EXISTS idx_weather_date             ON weather_daily (obs_date);
CREATE INDEX IF NOT EXISTS idx_predictions_week         ON predictions (target_week_start, risk_level);

-- ---------------------------------------------------------------------
-- 7. Views (re-created every time so they always match the tables)
-- ---------------------------------------------------------------------

-- Outbreaks with readable names and calendar fields
CREATE OR REPLACE VIEW v_outbreaks AS
SELECT o.outbreak_id,
       s.state_name,
       d.district_name,
       d.census_code,
       o.district_name_raw,
       r.year                                   AS report_year,
       r.week_no                                AS report_week,
       o.start_date,
       EXTRACT(YEAR  FROM o.start_date)::INT    AS start_year,
       EXTRACT(MONTH FROM o.start_date)::INT    AS start_month,
       o.cases, o.deaths, o.is_suspected, o.is_mixed,
       o.current_status, o.report_lag_days, o.needs_review, o.idsp_unique_id
FROM outbreaks o
JOIN reports r        ON r.report_id = o.report_id
LEFT JOIN states s    ON s.state_id = o.state_id
LEFT JOIN districts d ON d.district_id = o.district_id;

-- One row per district per year
CREATE OR REPLACE VIEW v_district_year AS
SELECT state_name, district_name, census_code, report_year AS year,
       COUNT(*)              AS outbreaks,
       SUM(cases)            AS cases,
       SUM(deaths)           AS deaths
FROM v_outbreaks
WHERE district_name IS NOT NULL
GROUP BY state_name, district_name, census_code, report_year;

-- Weekly weather per district (ISO weeks, the same weeks IDSP uses)
CREATE OR REPLACE VIEW v_weather_weekly AS
SELECT district_id,
       date_trunc('week', obs_date)::DATE AS week_start,
       SUM(precip_mm)                     AS rain_mm,
       ROUND(AVG(temp_mean_c), 1)         AS temp_mean_c,
       MIN(temp_min_c)                    AS temp_min_c,
       MAX(temp_max_c)                    AS temp_max_c,
       ROUND(AVG(rel_humidity_pct), 1)    AS humidity_pct,
       COUNT(*)                           AS days,
       BOOL_OR(data_type = 'forecast')    AS includes_forecast
FROM weather_daily
GROUP BY district_id, date_trunc('week', obs_date);
