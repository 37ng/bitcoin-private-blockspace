CREATE OR REPLACE TABLE `${dst}.monthly_fee`
OPTIONS (description = "Total transaction fees paid per month, ${start_date} onward.")
AS
SELECT
  block_month,
  COUNT(*)                                        AS txs,
  COUNT(DISTINCT block_number)                    AS blocks,
  SUM(fee)                                        AS fee_sats,
  SUM(virtual_size)                               AS vbytes,
  SAFE_DIVIDE(SUM(fee), SUM(virtual_size))        AS fee_rate_sat_vb
FROM `${dst}.tx_base`
WHERE NOT is_coinbase
GROUP BY block_month
ORDER BY block_month;
