-- Add protocol column to agents table
ALTER TABLE agents ADD COLUMN IF NOT EXISTS protocol VARCHAR(100);
