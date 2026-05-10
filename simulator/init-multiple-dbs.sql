-- Creates additional databases on TimescaleDB startup
-- dcim_aggregator is created by POSTGRES_DB env var
-- This script creates the DCIM Server database

SELECT 'CREATE DATABASE dcim_db'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dcim_db')\gexec
