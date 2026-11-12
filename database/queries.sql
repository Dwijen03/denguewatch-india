-- =====================================================================
-- DengueWatch India - analytical SQL queries
-- Each query starts with "-- name: <name>" so that denguewatch/db/queries.py
-- can run it by name (the API and dashboard use these in Steps 8-9).
-- Parameters are written :like_this and are always passed safely by SQLAlchemy.
-- An optional filter uses the pattern  (:state IS NULL OR state_name = :state).
-- =====================================================================


-- name: outbreaks_by_district_year
-- Q1. Outbreaks and cases per district per year, with change from the previous year
--     (window function LAG over each district's yearly series).
SELECT state_name, district_name, year, outbreaks, cases, deaths,
       outbreaks - LAG(outbreaks) OVER w AS outbreaks_change,
       cases     - LAG(cases)     OVER w AS cases_change
FROM v_district_year
WHERE (CAST(:state AS TEXT) IS NULL OR state_name = :state)
  AND (CAST(:district AS TEXT) IS NULL OR district_name = :district)
WINDOW w AS (PARTITION BY census_code ORDER BY year)
ORDER BY state_name, district_name, year;


-- name: district_year_matrix
-- Q2. District x year comparison table (one column per recent year) using FILTER.
SELECT state_name, district_name,
       COUNT(*) FILTER (WHERE report_year = 2019) AS y2019,
       COUNT(*) FILTER (WHERE report_year = 2020) AS y2020,
       COUNT(*) FILTER (WHERE report_year = 2021) AS y2021,
       COUNT(*) FILTER (WHERE report_year = 2022) AS y2022,
       COUNT(*) FILTER (WHERE report_year = 2023) AS y2023,
       COUNT(*) FILTER (WHERE report_year = 2024) AS y2024,
       COUNT(*) FILTER (WHERE report_year = 2025) AS y2025,
       COUNT(*) FILTER (WHERE report_year = 2026) AS y2026,
       COUNT(*)                                    AS total_all_years
FROM v_outbreaks
WHERE district_name IS NOT NULL
  AND (CAST(:state AS TEXT) IS NULL OR state_name = :state)
GROUP BY state_name, district_name
ORDER BY total_all_years DESC
LIMIT :limit;


-- name: monthly_seasonality
-- Q3. In which months do outbreaks start? (per state, or all India when :state is NULL)
SELECT start_month,
       TO_CHAR(MAKE_DATE(2000, start_month, 1), 'Mon') AS month,
       COUNT(*)                                        AS outbreaks,
       SUM(cases)                                      AS cases,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_outbreaks
FROM v_outbreaks
WHERE start_month IS NOT NULL
  AND (CAST(:state AS TEXT) IS NULL OR state_name = :state)
GROUP BY start_month
ORDER BY start_month;


-- name: repeat_hotspots
-- Q4. Districts with outbreaks in many different years (persistent hotspots).
SELECT state_name, district_name,
       COUNT(DISTINCT report_year)   AS years_with_outbreaks,
       COUNT(*)                      AS outbreaks,
       ROUND(AVG(cases))             AS avg_cases_per_outbreak,
       MIN(report_year)              AS first_year,
       MAX(report_year)              AS last_year
FROM v_outbreaks
WHERE district_name IS NOT NULL
GROUP BY state_name, district_name
HAVING COUNT(DISTINCT report_year) >= :min_years
ORDER BY years_with_outbreaks DESC, outbreaks DESC;


-- name: state_ranking_by_year
-- Q5. Rank states by dengue cases within each year (RANK window function).
SELECT year, state_name, outbreaks, cases, rank_in_year
FROM (
    SELECT report_year AS year, state_name,
           COUNT(*) AS outbreaks, SUM(cases) AS cases,
           RANK() OVER (PARTITION BY report_year ORDER BY SUM(cases) DESC NULLS LAST) AS rank_in_year
    FROM v_outbreaks
    WHERE state_name IS NOT NULL
    GROUP BY report_year, state_name
) ranked
WHERE rank_in_year <= :top_n
ORDER BY year, rank_in_year;


-- name: reporting_lag_by_year
-- Q6. How long after an outbreak starts does it appear in the weekly report? (percentiles)
SELECT report_year AS year,
       COUNT(*)                                                          AS outbreaks,
       PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY report_lag_days)     AS median_lag_days,
       PERCENTILE_CONT(0.9)  WITHIN GROUP (ORDER BY report_lag_days)     AS p90_lag_days
FROM v_outbreaks
WHERE report_lag_days BETWEEN 0 AND 365
GROUP BY report_year
ORDER BY report_year;


-- name: data_freshness
-- Q7. What is the latest data in the system? (shown on the dashboard and /health)
SELECT (SELECT MAX(week_end)  FROM reports)                AS latest_report_week_end,
       (SELECT MAX(year * 100 + week_no) FROM reports)     AS latest_report_yearweek,
       (SELECT COUNT(*) FROM reports)                      AS reports_loaded,
       (SELECT COUNT(*) FROM outbreaks)                    AS outbreaks_loaded,
       (SELECT MAX(obs_date) FROM weather_daily WHERE data_type = 'observed') AS latest_weather_date,
       (SELECT MAX(finished_at) FROM pipeline_runs WHERE status = 'success')  AS last_successful_run;


-- name: data_quality_summary
-- Q8. How many outbreaks were matched to a district, and how many need review?
SELECT match_method,
       COUNT(*)                                   AS outbreaks,
       COUNT(*) FILTER (WHERE needs_review)       AS needs_review,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM outbreaks
GROUP BY match_method
ORDER BY outbreaks DESC;


-- name: rainfall_before_outbreaks
-- Q9. Rainfall and temperature in the 4, 8 and 12 weeks before each outbreak in a district
--     (returns rows once weather is loaded in Step 6).
SELECT o.outbreak_id, o.district_name, o.start_date, o.cases,
       SUM(w.precip_mm) FILTER (WHERE w.obs_date >= o.start_date - 28)  AS rain_4wk_mm,
       SUM(w.precip_mm) FILTER (WHERE w.obs_date >= o.start_date - 56)  AS rain_8wk_mm,
       SUM(w.precip_mm)                                                 AS rain_12wk_mm,
       ROUND(AVG(w.temp_mean_c), 1)                                     AS temp_12wk_c
FROM v_outbreaks o
JOIN districts d     ON d.census_code = o.census_code
JOIN weather_daily w ON w.district_id = d.district_id
                    AND w.obs_date >= o.start_date - 84 AND w.obs_date < o.start_date
WHERE (CAST(:district AS TEXT) IS NULL OR o.district_name = :district)
GROUP BY o.outbreak_id, o.district_name, o.start_date, o.cases
ORDER BY o.start_date DESC
LIMIT :limit;


-- name: current_risk
-- Q10. Latest risk score for every district from the active model (filled in Step 7).
SELECT s.state_name, d.district_name, p.target_week_start, p.risk_score, p.risk_level, p.top_factors
FROM predictions p
JOIN model_versions m ON m.model_id = p.model_id AND m.is_active
JOIN districts d      ON d.district_id = p.district_id
JOIN states s         ON s.state_id = d.state_id
WHERE p.target_week_start = (SELECT MAX(target_week_start) FROM predictions WHERE model_id = m.model_id)
  AND (CAST(:state AS TEXT) IS NULL OR s.state_name = :state)
ORDER BY p.risk_score DESC;
