-- Isolated database for pytest (avoids migrate_schema contention with running API).
CREATE DATABASE maestro_test OWNER maestro;

\c maestro_test
CREATE EXTENSION IF NOT EXISTS vector;

GRANT CONNECT ON DATABASE maestro_test TO contextual_app;
GRANT USAGE, CREATE ON SCHEMA public TO contextual_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO contextual_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO contextual_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO contextual_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO contextual_app;
