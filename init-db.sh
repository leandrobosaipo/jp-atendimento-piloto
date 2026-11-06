#!/bin/bash
# First initialization only. psql receives role secrets from environment.
set -euo pipefail
for role in chat flow clock; do
    key="PASS_${role^^}"
    export APP_PASS="${!key}"
    psql -v ON_ERROR_STOP=1 -v app="$role" -U postgres -d postgres <<'SQL'
\getenv pw APP_PASS
CREATE ROLE :"app" LOGIN PASSWORD :'pw';
CREATE DATABASE :"app" OWNER :"app";
REVOKE ALL ON DATABASE :"app" FROM PUBLIC;
SQL
done
unset APP_PASS
psql -v ON_ERROR_STOP=1 -U postgres -d chat <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SQL
