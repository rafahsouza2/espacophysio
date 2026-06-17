-- Tabela multi-período (substitui bi_cache de linha única)
CREATE TABLE IF NOT EXISTS public.bi_reports (
  period_key          TEXT PRIMARY KEY,        -- ex: "2026-05"
  periodo_label       TEXT NOT NULL,           -- ex: "Mai/2026"
  periodo_inicio      TEXT,                    -- ex: "01/05/2026"
  periodo_fim         TEXT,                    -- ex: "31/05/2026"
  total_registros     INTEGER DEFAULT 0,
  total_atendimentos  INTEGER DEFAULT 0,
  total_producao      NUMERIC(14,2) DEFAULT 0,
  data                JSONB NOT NULL,
  updated_at          TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.bi_reports ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all" ON public.bi_reports
  FOR ALL TO service_role USING (true) WITH CHECK (true);
