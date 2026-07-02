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

DEFAULTS: dict = {
    "meta_mensal":        1_000_000.0,
    "dias_uteis_mes":     22.0,
    "metas_por_unidade":  {},
    "cidade_geocoding":   "Brasília, DF, Brasil",
}


# ── Leitura ────────────────────────────────────────────────────────────────────
def get_bi_config() -> dict:
    """Retorna parâmetros ativos. Defaults → disco → Supabase (cada camada sobrescreve)."""
    cfg: dict = {
        "meta_mensal":       DEFAULTS["meta_mensal"],
        "dias_uteis_mes":    DEFAULTS["dias_uteis_mes"],
        "metas_por_unidade": {},
        "cidade_geocoding":  DEFAULTS["cidade_geocoding"],
    }

    # 1. Disco (base persistente)
    if _CONFIG_PATH.exists():
        try:
            raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw.get("meta_mensal"), (int, float)):
                cfg["meta_mensal"] = float(raw["meta_mensal"])
            if isinstance(raw.get("dias_uteis_mes"), (int, float)):
                cfg["dias_uteis_mes"] = float(raw["dias_uteis_mes"])
            if isinstance(raw.get("metas_por_unidade"), dict):
                cfg["metas_por_unidade"] = raw["metas_por_unidade"]
            if isinstance(raw.get("cidade_geocoding"), str) and raw["cidade_geocoding"].strip():
                cfg["cidade_geocoding"] = raw["cidade_geocoding"].strip()
        except Exception:
            pass

    # 2. Supabase (sobrescreve disco; sem early-return para não perder metas_por_unidade)
    try:
        from app.database import get_supabase_admin
        sb  = get_supabase_admin()
        res = sb.table("bi_parametros").select("chave,valor").execute()
        for row in (res.data or []):
            chave, valor = row["chave"], row["valor"]
            if chave == "metas_por_unidade":
                try:
                    cfg["metas_por_unidade"] = json.loads(valor)
                except Exception:
                    pass
            elif chave in ("meta_mensal", "dias_uteis_mes"):
                try:
                    cfg[chave] = float(valor)
                except Exception:
                    pass
            elif chave == "cidade_geocoding" and valor.strip():
                cfg["cidade_geocoding"] = valor.strip()
    except Exception as e:
        print("BI CONFIG LOAD (Supabase):", repr(e))

    return cfg


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


def save_bi_config(valores: dict) -> str | None:
    """
    Salva parâmetros. Retorna None se ok, mensagem de erro se falhou.
    Persiste no Supabase E no disco local.
    """
    from datetime import datetime
    rows = []
    disk_cfg: dict = {}

    for k, v in valores.items():
        if k == "metas_por_unidade":
            val_str = json.dumps(v, ensure_ascii=False)
            disk_cfg[k] = v
        elif k in ("meta_mensal", "dias_uteis_mes"):
            val_str = str(float(v))
            disk_cfg[k] = float(v)
        elif k == "cidade_geocoding":
            val_str = str(v).strip()
            disk_cfg[k] = val_str
        else:
            continue
        rows.append({"chave": k, "valor": val_str, "updated_at": datetime.now().isoformat()})

    if not rows:
        return None

    errors = []

    # Supabase
    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        sb.table("bi_parametros").upsert(rows, on_conflict="chave").execute()
        print("BI CONFIG SAVE OK:", [r["chave"] for r in rows])
    except Exception as e:
        errors.append(f"Supabase: {repr(e)}")
        print("BI CONFIG SAVE (Supabase) ERRO:", repr(e))

    # Disco (sempre)
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if _CONFIG_PATH.exists():
            try:
                existing = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.update(disk_cfg)
        _CONFIG_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        errors.append(f"Disco: {repr(e)}")

    return "; ".join(errors) if errors else None
