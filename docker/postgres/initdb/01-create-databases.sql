-- Runs once on first init (empty data volume).
-- mem0_app is already created by the POSTGRES_DB env var;
-- this script creates the additional databases needed by other services.

CREATE DATABASE litellm;
