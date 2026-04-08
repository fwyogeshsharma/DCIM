-- Migration: 004_snmp_walker_dedup

-- Description: Remove accumulated duplicate snmp-walker rows (keep latest per device+metric)
-- Date: 2026-04-06
-- NOTE: TimescaleDB hypertables require timestamp in every unique index, so deduplication
-- is enforced at the application layer (DataSyncService DELETE + INSERT per sync cycle).
-- This migration is a one-time cleanup of existing duplicates.

DELETE FROM snmp_metrics
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY server_id, agent_id, device_ip, metric_name
                   ORDER BY timestamp DESC
               ) AS rn
        FROM snmp_metrics
        WHERE agent_id = 'snmp-walker'
    ) ranked
    WHERE rn > 1
);
