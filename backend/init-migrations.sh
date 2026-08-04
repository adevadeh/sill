#!/bin/bash
set -e
for sql_file in /app/migrations/[0-9][0-9][0-9]_*.sql /app/migrations/zzz_*.sql; do
    if [ -f "$sql_file" ]; then
        echo "Running $sql_file..."
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$sql_file"
    fi
done
echo "All migrations completed"
