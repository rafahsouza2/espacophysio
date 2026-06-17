-- Tabela de cache do BI (usada pelo Vercel para persistir os dados de upload)
CREATE TABLE IF NOT EXISTS public.bi_cache (
  id   INTEGER PRIMARY KEY DEFAULT 1,
  data JSONB   NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Permite apenas 1 linha (constraint check)
ALTER TABLE public.bi_cache
  ADD CONSTRAINT bi_cache_single_row CHECK (id = 1);

-- Row Level Security
ALTER TABLE public.bi_cache ENABLE ROW LEVEL SECURITY;

-- Apenas service_role pode ler e escrever (nunca exposto ao browser)
CREATE POLICY "service_role_all" ON public.bi_cache
  FOR ALL TO service_role USING (true) WITH CHECK (true);
