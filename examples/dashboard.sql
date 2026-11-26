
SELECT date_trunc('month', occurred_at) AS month,
       source_id, currency, SUM(amount) AS orders_amount
FROM mart_sales_documents
WHERE entity_type = 'sales_order'
GROUP BY month, source_id, currency
ORDER BY month, source_id, currency;

SELECT source_id, status, currency, COUNT(*) AS opportunities,
       SUM(amount) AS opportunities_amount
FROM mart_sales_documents
WHERE entity_type = 'opportunity'
GROUP BY source_id, status, currency
ORDER BY source_id, status, currency;

SELECT source_id, external_id, title, amount, currency, updated_at,
       EXTRACT(DAY FROM (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - updated_at)::integer
           AS days_since_update
FROM mart_sales_documents
WHERE entity_type = 'opportunity' AND closed = false
  AND updated_at < (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '30 days'
ORDER BY updated_at, source_id, external_id;
