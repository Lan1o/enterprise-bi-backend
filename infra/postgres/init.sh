#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  --set=writer_password="$WAREHOUSE_PASSWORD" \
  --set=metabase_password="$METABASE_PASSWORD" \
  --set=reader_password="$BI_READER_PASSWORD" <<'SQL'
CREATE ROLE warehouse LOGIN PASSWORD :'writer_password';
CREATE DATABASE warehouse OWNER warehouse;
REVOKE ALL ON DATABASE warehouse FROM PUBLIC;
CREATE ROLE metabase LOGIN PASSWORD :'metabase_password';
CREATE DATABASE metabase OWNER metabase;
REVOKE ALL ON DATABASE metabase FROM PUBLIC;
CREATE ROLE bi_reader LOGIN PASSWORD :'reader_password';
GRANT CONNECT ON DATABASE warehouse TO bi_reader;
\connect warehouse
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO bi_reader;
SQL
