-- EPOCH_APP_DB_PASSWORD is passed as a psql variable by init-db.sh.
CREATE ROLE eviepoch_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOBYPASSRLS;
