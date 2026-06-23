-- Adiciona coluna de restrição de módulos ao perfil do usuário
-- NULL = acesso definido pelo cargo (role); valor = módulos separados por vírgula (ex: "bi" ou "autorizacoes,comunicados")
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS modulos_permitidos TEXT DEFAULT NULL;
