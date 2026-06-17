-- ============================================================
-- Espaço Physio Intranet — Schema Supabase (PostgreSQL)
-- Execute no SQL Editor do seu projeto Supabase
-- ============================================================

-- Habilita extensão UUID
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Perfis de usuário (estende auth.users do Supabase) ──────
CREATE TABLE public.profiles (
  id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  full_name   TEXT NOT NULL,
  email       TEXT,
  role        TEXT NOT NULL DEFAULT 'recepcao'
                CHECK (role IN ('admin','coordenacao','financeiro','recepcao','fisioterapeuta')),
  active      BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Cria o perfil automaticamente quando um usuário se cadastra
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  INSERT INTO public.profiles (id, full_name, email)
  VALUES (
    NEW.id,
    COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.email),
    NEW.email
  );
  RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ── Pacientes ───────────────────────────────────────────────
CREATE TABLE public.pacientes (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nome             TEXT NOT NULL,
  cpf              TEXT UNIQUE,
  data_nascimento  DATE,
  telefone         TEXT,
  email            TEXT,
  responsavel      TEXT,
  observacoes      TEXT,
  ativo            BOOLEAN NOT NULL DEFAULT true,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by       UUID REFERENCES public.profiles(id)
);

CREATE INDEX idx_pacientes_nome ON public.pacientes(nome);
CREATE INDEX idx_pacientes_ativo ON public.pacientes(ativo);

-- ── Agendamentos ────────────────────────────────────────────
CREATE TABLE public.agendamentos (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  paciente_id       UUID NOT NULL REFERENCES public.pacientes(id) ON DELETE CASCADE,
  profissional_id   UUID NOT NULL REFERENCES public.profiles(id),
  data_hora         TIMESTAMPTZ NOT NULL,
  duracao_minutos   INTEGER DEFAULT 60,
  tipo              TEXT,
  sala              TEXT,
  status            TEXT NOT NULL DEFAULT 'agendado'
                      CHECK (status IN ('agendado','confirmado','realizado','falta','cancelado')),
  observacoes       TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by        UUID REFERENCES public.profiles(id)
);

CREATE INDEX idx_agendamentos_data_hora ON public.agendamentos(data_hora);
CREATE INDEX idx_agendamentos_paciente ON public.agendamentos(paciente_id);
CREATE INDEX idx_agendamentos_profissional ON public.agendamentos(profissional_id);

-- ── Transações financeiras ───────────────────────────────────
CREATE TABLE public.transacoes (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  paciente_id      UUID REFERENCES public.pacientes(id),
  agendamento_id   UUID REFERENCES public.agendamentos(id),
  tipo             TEXT NOT NULL
                     CHECK (tipo IN ('receita','despesa','repasse')),
  descricao        TEXT NOT NULL,
  valor            NUMERIC(10,2) NOT NULL CHECK (valor >= 0),
  status           TEXT NOT NULL DEFAULT 'pendente'
                     CHECK (status IN ('pendente','pago','cancelado')),
  data_vencimento  DATE,
  data_pagamento   DATE,
  forma_pagamento  TEXT
                     CHECK (forma_pagamento IN ('dinheiro','pix','cartao_credito','cartao_debito','convenio') OR forma_pagamento IS NULL),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by       UUID REFERENCES public.profiles(id)
);

CREATE INDEX idx_transacoes_status ON public.transacoes(status);
CREATE INDEX idx_transacoes_tipo ON public.transacoes(tipo);
CREATE INDEX idx_transacoes_created_at ON public.transacoes(created_at);

-- ── Comunicados ─────────────────────────────────────────────
CREATE TABLE public.comunicados (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  titulo        TEXT NOT NULL,
  conteudo      TEXT NOT NULL,
  autor_id      UUID NOT NULL REFERENCES public.profiles(id),
  publico_alvo  TEXT NOT NULL DEFAULT 'todos',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at    TIMESTAMPTZ
);

CREATE INDEX idx_comunicados_created_at ON public.comunicados(created_at DESC);

-- ── Chamados de suporte ──────────────────────────────────────
CREATE TABLE public.chamados (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  titulo      TEXT NOT NULL,
  descricao   TEXT,
  prioridade  TEXT NOT NULL DEFAULT 'media'
                CHECK (prioridade IN ('baixa','media','alta','critica')),
  status      TEXT NOT NULL DEFAULT 'aberto'
                CHECK (status IN ('aberto','em_andamento','resolvido','fechado')),
  autor_id    UUID NOT NULL REFERENCES public.profiles(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chamados_status ON public.chamados(status);
CREATE INDEX idx_chamados_autor ON public.chamados(autor_id);

-- ── Row Level Security (RLS) ─────────────────────────────────
ALTER TABLE public.profiles     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pacientes     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agendamentos  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transacoes    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.comunicados   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chamados      ENABLE ROW LEVEL SECURITY;

-- Profiles: cada um vê o próprio; admins veem todos
CREATE POLICY "profiles_select" ON public.profiles FOR SELECT
  USING (auth.uid() = id OR EXISTS (
    SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role IN ('admin','coordenacao')
  ));

CREATE POLICY "profiles_update_own" ON public.profiles FOR UPDATE
  USING (auth.uid() = id);

-- Pacientes: usuários autenticados com permissão podem ver e editar
CREATE POLICY "pacientes_auth" ON public.pacientes FOR ALL
  USING (EXISTS (
    SELECT 1 FROM public.profiles p
    WHERE p.id = auth.uid() AND p.role IN ('admin','coordenacao','recepcao','fisioterapeuta') AND p.active = true
  ));

-- Agendamentos: usuários autenticados com permissão
CREATE POLICY "agendamentos_auth" ON public.agendamentos FOR ALL
  USING (EXISTS (
    SELECT 1 FROM public.profiles p
    WHERE p.id = auth.uid() AND p.active = true
  ));

-- Transações: apenas financeiro, coordenação e admin
CREATE POLICY "transacoes_auth" ON public.transacoes FOR ALL
  USING (EXISTS (
    SELECT 1 FROM public.profiles p
    WHERE p.id = auth.uid() AND p.role IN ('admin','coordenacao','financeiro') AND p.active = true
  ));

-- Comunicados: todos leem; admin/coordenação criam
CREATE POLICY "comunicados_select" ON public.comunicados FOR SELECT
  USING (EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.active = true));

CREATE POLICY "comunicados_insert" ON public.comunicados FOR INSERT
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role IN ('admin','coordenacao') AND p.active = true
  ));

-- Chamados: cada um vê os próprios; admin/coordenação veem todos
CREATE POLICY "chamados_select" ON public.chamados FOR SELECT
  USING (autor_id = auth.uid() OR EXISTS (
    SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role IN ('admin','coordenacao')
  ));

CREATE POLICY "chamados_insert" ON public.chamados FOR INSERT
  WITH CHECK (EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.active = true));

CREATE POLICY "chamados_update" ON public.chamados FOR UPDATE
  USING (EXISTS (
    SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role IN ('admin','coordenacao')
  ));
