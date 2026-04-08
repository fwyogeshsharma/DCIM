-- Migration: 005_snmp_walker_dedup
-- Description: Deduplicate existing snmp-walker rows and add partial unique index
-- Date: 2026-04-06

-- Delete older duplicate walker rows, keeping only the most recent per device+metric
DELETE FROM snmp_metrics
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY server_id, device_host, metric_name
                   ORDER BY timestamp DESC
               ) AS rn
        FROM snmp_metrics
        WHERE agent_id = 'snmp-walker'
    ) ranked
    WHERE rn > 1
);

-- Walker topology metrics (reachable, topology_depth, topology_parent) are latest-value data.
-- This partial index ensures only one row per device+metric exists for the walker,
-- while leaving regular agent SNMP time-series data unchanged.
CREATE UNIQUE INDEX IF NOT EXISTS idx_snmp_walker_dedup
ON snmp_metrics (server_id, device_host, metric_name)
WHERE agent_id = 'snmp-walker';
