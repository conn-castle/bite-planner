-- Stand-ins for the objects Neon creates when the Data API and Neon Auth are
-- enabled: the `authenticated` and `anonymous` roles, `auth.uid()`, and the
-- columns of `neon_auth.user` that bite-planner reads. They let
-- migrations and RLS policies run against plain Postgres. Tests pick the
-- signed-in user with `SET test.user_id = '<uuid>'`. Idempotent.
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE anonymous NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE SCHEMA IF NOT EXISTS auth;
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('test.user_id', true), '')::uuid
$$;
CREATE SCHEMA IF NOT EXISTS neon_auth;
CREATE TABLE IF NOT EXISTS neon_auth."user" (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  email text NOT NULL UNIQUE
);
