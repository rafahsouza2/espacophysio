-- Compilação de uploads semanais do B.I.
-- Guarda em cada atendimento as informações do export do iGut que antes só existiam no
-- relatório do mês, para que um mês montado com vários arquivos (ex.: um por semana)
-- tenha os mesmos indicadores do arquivo do mês inteiro (aba Agendamentos, atendentes...).
-- Rodar uma vez no SQL Editor do Supabase. Pode rodar de novo sem problema.

ALTER TABLE public.bi_atendimentos
  ADD COLUMN IF NOT EXISTS status_atendimento TEXT,   -- texto original (FINALIZADA, CONSULTA MARCADA...)
  ADD COLUMN IF NOT EXISTS status_agendamento TEXT,   -- Confirmada, Faltou, Desmarcou...
  ADD COLUMN IF NOT EXISTS agendador          TEXT,   -- Usuário que Agendou
  ADD COLUMN IF NOT EXISTS como_achou         TEXT;   -- Como nos achou

-- Cada upload apaga/conta os atendimentos dos dias do arquivo e o painel filtra por data
CREATE INDEX IF NOT EXISTS bi_atendimentos_data_atend_idx ON public.bi_atendimentos (data_atend);
CREATE INDEX IF NOT EXISTS bi_atendimentos_period_key_idx ON public.bi_atendimentos (period_key);
