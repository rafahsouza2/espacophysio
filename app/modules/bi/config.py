"""
Parâmetros de configuração do módulo BI.
Persiste no Supabase (tabela bi_parametros) com fallback em JSON local.

Tabela Supabase (criar uma vez):
  CREATE TABLE IF NOT EXISTS bi_parametros (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
  );
  ALTER TABLE bi_parametros ENABLE ROW LEVEL SECURITY;
  CREATE POLICY "service_role_all" ON bi_parametros FOR ALL TO service_role USING (true);
"""
from __future__ import annotations
import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent.parent / "data" / "bi_config.json"

DEFAULTS: dict[str, float] = {
    "meta_mensal":   1_000_000.0,
    "dias_uteis_mes": 22.0,
}


# ── Leitura ────────────────────────────────────────────────────────────────────
def get_bi_config() -> dict[str, float]:
    """Retorna parâmetros ativos. Supabase → disco → defaults."""
    # 1. Supabase
    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        res = sb.table("bi_parametros").select("chave,valor").execute()
        if res.data:
            cfg = dict(DEFAULTS)
            for row in res.data:
                if row["chave"] in cfg:
                    cfg[row["chave"]] = float(row["valor"])
            return cfg
    except Exception as e:
        print("BI CONFIG LOAD (Supabase):", repr(e))

    # 2. Disco
    if _CONFIG_PATH.exists():
        try:
            return {**DEFAULTS, **json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))}
        except Exception:
            pass

    return dict(DEFAULTS)


# ── Escrita ────────────────────────────────────────────────────────────────────
def recalculate_all_reports(meta: float, dias_uteis: float) -> tuple[int, str | None]:
    """
    Recalcula os campos dependentes da meta em todos os relatórios salvos.
    Não re-parseia os arquivos — apenas atualiza alcance_meta, meta_diaria e meta_pct/dia.
    Retorna (qtd_atualizados, mensagem_de_erro | None).
    """
    meta_diaria = meta / dias_uteis if dias_uteis else meta / 22

    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()

        res = sb.table("bi_reports").select("period_key, data").execute()
        reports = res.data or []

        updated = 0
        for row in reports:
            d    = row["data"]
            prod = float(d.get("kpis", {}).get("total_producao", 0))
            alcance = round(prod / meta * 100, 1) if meta else 0.0

            d["meta"]         = meta
            d["meta_diaria"]  = round(meta_diaria, 2)
            d["alcance_meta"] = alcance
            if "kpis" in d:
                d["kpis"]["alcance_meta"] = alcance

            # Recalcula % da meta por dia
            evo   = d.get("evolucao_diaria", {})
            prods = evo.get("prod", [])
            if prods:
                evo["meta_pct"] = [
                    round(p / meta_diaria * 100, 1) if meta_diaria else 0.0
                    for p in prods
                ]

            sb.table("bi_reports").update({"data": d}).eq("period_key", row["period_key"]).execute()
            updated += 1

        return updated, None

    except Exception as e:
        print("BI RECALC ERROR:", repr(e))
        return 0, repr(e)


def save_bi_config(valores: dict[str, float]) -> str | None:
    """
    Salva parâmetros. Retorna None se ok, mensagem de erro se falhou.
    Persiste no Supabase E no disco local.
    """
    cfg = {k: float(v) for k, v in valores.items() if k in DEFAULTS}

    errors = []

    # Supabase
    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        from datetime import datetime
        rows = [{"chave": k, "valor": str(v), "updated_at": datetime.now().isoformat()} for k, v in cfg.items()]
        sb.table("bi_parametros").upsert(rows).execute()
    except Exception as e:
        errors.append(f"Supabase: {repr(e)}")
        print("BI CONFIG SAVE (Supabase):", repr(e))

    # Disco (sempre)
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        errors.append(f"Disco: {repr(e)}")

    return "; ".join(errors) if errors else None
