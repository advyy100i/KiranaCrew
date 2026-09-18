-- SELECT-only role for n8n / dashboards (ADR-004). Password is deliberately weak: bound to 127.0.0.1 only.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kirana_ro') THEN
    CREATE ROLE kirana_ro LOGIN PASSWORD 'kirana_ro';
  END IF;
END $$;
GRANT CONNECT ON DATABASE kirana TO kirana_ro;
GRANT USAGE ON SCHEMA public TO kirana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO kirana_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO kirana_ro;
