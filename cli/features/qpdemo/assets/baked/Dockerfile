# Pre-baked demo Postgres: the Orders dataset is generated at image BUILD time
# so provisioning on a user machine skips the 60-90s seed entirely. PGDATA
# lives outside the image's declared VOLUME path so the data actually ships
# in the layers.
FROM public.ecr.aws/docker/library/postgres:16

ENV PGDATA=/var/lib/postgresql/baked

COPY seed.sql /tmp/seed.sql

RUN set -eu; \
    mkdir -p "$PGDATA"; \
    chown -R postgres:postgres "$PGDATA"; \
    su postgres -c 'initdb -D "$PGDATA" --auth-host=scram-sha-256 --auth-local=trust'; \
    su postgres -c 'pg_ctl -D "$PGDATA" -o "-c listen_addresses=''''" -w start'; \
    su postgres -c 'psql -v ON_ERROR_STOP=1 -c "CREATE USER sqp_user WITH PASSWORD '\''sqp_pass'\'' SUPERUSER"'; \
    su postgres -c 'createdb -O sqp_user sqp_test'; \
    su postgres -c 'psql -v ON_ERROR_STOP=1 -d sqp_test -U postgres -f /tmp/seed.sql'; \
    su postgres -c 'psql -d sqp_test -c "SELECT count(*) FROM orders"'; \
    su postgres -c 'pg_ctl -D "$PGDATA" -m fast -w stop'; \
    echo "host all all all scram-sha-256" >> "$PGDATA/pg_hba.conf"; \
    rm /tmp/seed.sql

# Same runtime flags the seeded deploy passes on the command line.
CMD ["postgres", "-c", "wal_level=logical", "-c", "max_replication_slots=10", \
     "-c", "max_wal_senders=10", "-c", "max_parallel_workers_per_gather=0"]
