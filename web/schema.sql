CREATE TABLE IF NOT EXISTS public.users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS email_verified boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS public.email_verifications (
    token_hash text PRIMARY KEY,
    user_id bigint NOT NULL UNIQUE REFERENCES public.users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '1 hour'
);
CREATE TABLE IF NOT EXISTS public.sessions (
    token_hash text PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '1 day'
);
CREATE TABLE IF NOT EXISTS public.tasks (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 500),
    completed boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.tasks ADD COLUMN IF NOT EXISTS user_id bigint
    REFERENCES public.users(id) ON DELETE CASCADE;
ALTER TABLE public.tasks ALTER COLUMN user_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS tasks_user_id_id_idx
    ON public.tasks (user_id, id DESC);
