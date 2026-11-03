-- Ownership calculations, parameterised by date.
--
-- Conventions
--   * A 'split' event multiplies every position of its class dated strictly
--     before the split by split_ratio (2 = 2-for-1, 0.1 = 1-for-10). Events
--     dated the same day as the split are already post-split. The stored
--     shares_delta of a split is informative only and is ignored here, so the
--     result stays correct even if events are back-dated later.
--   * "My shares" = equity investments - shares sold, both adjusted for later
--     splits of their class.
--   * Fully diluted denominator = as-converted shares + latest option pool
--     (granted + ungranted) + outstanding warrants, as of the date. My
--     warrants (investments with instrument 'warrant' and a number of shares)
--     are added to the numerator.
--   * p_entity_id NULL means "all investing entities".

CREATE OR REPLACE AGGREGATE numeric_product(numeric) (
  SFUNC = numeric_mul, STYPE = numeric, INITCOND = '1'
);

-- Cumulative split factor of a class for positions dated p_from, seen at p_to.
CREATE OR REPLACE FUNCTION fn_split_factor(p_share_class_id bigint, p_from date, p_to date)
RETURNS numeric LANGUAGE sql STABLE AS $$
  SELECT COALESCE(numeric_product(se.split_ratio), 1)
  FROM share_events se
  WHERE se.share_class_id = p_share_class_id
    AND se.event_type = 'split'
    AND se.event_date > p_from
    AND se.event_date <= p_to
$$;

-- Shares outstanding per class at a date.
CREATE OR REPLACE FUNCTION fn_class_outstanding(p_as_of date DEFAULT current_date)
RETURNS TABLE (
  company_id bigint, share_class_id bigint, share_class text, is_common boolean,
  seniority int, conversion_ratio numeric, shares_outstanding numeric, has_issuance boolean
) LANGUAGE sql STABLE AS $$
  SELECT sc.company_id, sc.id, sc.name, sc.is_common, sc.seniority, sc.conversion_ratio,
         COALESCE(ev.shares, 0)::numeric,
         COALESCE(ev.has_issuance, false)
  FROM share_classes sc
  LEFT JOIN LATERAL (
    SELECT SUM(se.shares_delta * fn_split_factor(sc.id, se.event_date, p_as_of)) AS shares,
           bool_or(se.event_type IN ('issuance', 'conversion_in')) AS has_issuance
    FROM share_events se
    WHERE se.share_class_id = sc.id
      AND se.event_type <> 'split'
      AND se.event_date <= p_as_of
  ) ev ON true
$$;

-- My shares per company, entity and class at a date.
CREATE OR REPLACE FUNCTION fn_my_shares(p_as_of date DEFAULT current_date)
RETURNS TABLE (company_id bigint, entity_id bigint, share_class_id bigint, my_shares numeric)
LANGUAGE sql STABLE AS $$
  SELECT t.company_id, t.entity_id, t.share_class_id,
         SUM(t.shares * fn_split_factor(t.share_class_id, t.d, p_as_of))
  FROM (
    SELECT i.company_id, i.entity_id, i.share_class_id, i.invest_date AS d, i.shares
    FROM investments i
    WHERE i.instrument = 'equity' AND i.invest_date <= p_as_of
    UNION ALL
    SELECT r.company_id, r.entity_id, r.share_class_id, r.realized_on, -r.shares_sold
    FROM realizations r
    WHERE r.shares_sold IS NOT NULL AND r.realized_on <= p_as_of
  ) t
  GROUP BY t.company_id, t.entity_id, t.share_class_id
$$;

-- Ownership per class, with consistency flags.
CREATE OR REPLACE FUNCTION fn_ownership_by_class(p_as_of date DEFAULT current_date,
                                                 p_entity_id bigint DEFAULT NULL)
RETURNS TABLE (
  company_id bigint, share_class_id bigint, share_class text, is_common boolean,
  seniority int, conversion_ratio numeric, shares_outstanding numeric, my_shares numeric,
  pct_of_class numeric, inconsistent boolean, missing_issuance boolean, negative_position boolean
) LANGUAGE sql STABLE AS $$
  WITH mine AS (
    SELECT m.share_class_id, SUM(m.my_shares) AS my_shares
    FROM fn_my_shares(p_as_of) m
    WHERE p_entity_id IS NULL OR m.entity_id = p_entity_id
    GROUP BY m.share_class_id
  ),
  mine_all AS (  -- consistency is always checked against all entities together
    SELECT m.share_class_id, SUM(m.my_shares) AS my_shares
    FROM fn_my_shares(p_as_of) m GROUP BY m.share_class_id
  )
  SELECT o.company_id, o.share_class_id, o.share_class, o.is_common, o.seniority,
         o.conversion_ratio, o.shares_outstanding,
         COALESCE(m.my_shares, 0),
         ROUND(100.0 * COALESCE(m.my_shares, 0) / NULLIF(o.shares_outstanding, 0), 4),
         COALESCE(a.my_shares, 0) > o.shares_outstanding,
         COALESCE(a.my_shares, 0) > 0 AND NOT o.has_issuance,
         COALESCE(a.my_shares, 0) < 0
  FROM fn_class_outstanding(p_as_of) o
  LEFT JOIN mine m ON m.share_class_id = o.share_class_id
  LEFT JOIN mine_all a ON a.share_class_id = o.share_class_id
$$;

-- Ownership of the whole company: basic as-converted and fully diluted.
CREATE OR REPLACE FUNCTION fn_ownership_company(p_as_of date DEFAULT current_date,
                                                p_entity_id bigint DEFAULT NULL)
RETURNS TABLE (
  company_id bigint, name text, my_as_converted numeric, total_as_converted numeric,
  option_pool bigint, warrants bigint, my_warrants numeric, fully_diluted_total numeric,
  pct_basic numeric, pct_fully_diluted numeric
) LANGUAGE sql STABLE AS $$
  WITH cls AS (
    SELECT * FROM fn_ownership_by_class(p_as_of, p_entity_id)
  ),
  tot AS (
    SELECT cls.company_id,
           SUM(cls.shares_outstanding * cls.conversion_ratio) AS total_as_converted,
           SUM(cls.my_shares * cls.conversion_ratio) AS my_as_converted
    FROM cls GROUP BY cls.company_id
  ),
  pool AS (
    SELECT DISTINCT ON (op.company_id) op.company_id, op.authorized, op.warrants
    FROM option_pools op
    WHERE op.as_of <= p_as_of
    ORDER BY op.company_id, op.as_of DESC
  ),
  my_w AS (
    SELECT i.company_id, SUM(i.shares) AS shares
    FROM investments i
    WHERE i.instrument = 'warrant' AND i.shares IS NOT NULL AND i.invest_date <= p_as_of
      AND (p_entity_id IS NULL OR i.entity_id = p_entity_id)
    GROUP BY i.company_id
  ),
  fd AS (
    SELECT tot.company_id,
           tot.total_as_converted + COALESCE(pool.authorized, 0) + COALESCE(pool.warrants, 0) AS total
    FROM tot LEFT JOIN pool ON pool.company_id = tot.company_id
  )
  SELECT c.id, c.name, tot.my_as_converted, tot.total_as_converted,
         COALESCE(pool.authorized, 0), COALESCE(pool.warrants, 0), COALESCE(my_w.shares, 0),
         fd.total,
         ROUND(100.0 * tot.my_as_converted / NULLIF(tot.total_as_converted, 0), 4),
         ROUND(100.0 * (tot.my_as_converted + COALESCE(my_w.shares, 0)) / NULLIF(fd.total, 0), 4)
  FROM companies c
  JOIN tot ON tot.company_id = c.id
  JOIN fd ON fd.company_id = c.id
  LEFT JOIN pool ON pool.company_id = c.id
  LEFT JOIN my_w ON my_w.company_id = c.id
$$;

-- Views from the reference schema, as of today (superset of the original columns).
CREATE OR REPLACE VIEW v_class_outstanding AS
  SELECT company_id, share_class_id, share_class, conversion_ratio, shares_outstanding
  FROM fn_class_outstanding(current_date);

CREATE OR REPLACE VIEW v_my_shares AS
  SELECT company_id, share_class_id, SUM(my_shares) AS my_shares
  FROM fn_my_shares(current_date)
  GROUP BY company_id, share_class_id;

CREATE OR REPLACE VIEW v_ownership_by_class AS
  SELECT company_id, share_class_id, share_class, shares_outstanding, my_shares,
         pct_of_class, inconsistent, missing_issuance
  FROM fn_ownership_by_class(current_date, NULL);

CREATE OR REPLACE VIEW v_ownership_company AS
  SELECT company_id, name, my_as_converted, total_as_converted, pct_basic, pct_fully_diluted,
         option_pool, warrants, my_warrants, fully_diluted_total
  FROM fn_ownership_company(current_date, NULL);
