from pathlib import Path
from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.modules.bi.parser import parse_xls, load_saved, clear_saved, list_reports
from app.modules.bi.config import get_bi_config, save_bi_config, recalculate_all_reports

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["bi"])


def _backfill_bi(d: dict) -> dict:
    """Adiciona campos novos a relatórios gerados pelo parser antigo."""
    if not d or "alcance_meta" in d:
        return d
    from app.modules.bi.parser import META, META_DIARIA
    k   = d.get("kpis", {})
    prod = float(k.get("total_producao", 0))
    evo = d.get("evolucao_diaria", {})
    n   = len(evo.get("labels", []))
    dias = n or 1

    d["meta"]         = META
    d["meta_diaria"]  = round(META_DIARIA, 2)
    d["alcance_meta"] = round(prod / META * 100, 1)
    d["dias"]         = n
    d["media_diaria"] = round(prod / dias, 2)
    d["melhor_dia"]   = "—"
    d["melhor_dia_prod"] = 0.0
    d.setdefault("cross_data",         {"labels_ag": [], "labels_at": [], "matrix": [], "totals_ag": [], "totals_at": []})
    d.setdefault("status_atendimento", {})
    d.setdefault("atendentes",         d.get("agendadores", []))

    evo.setdefault("prod",     [0.0] * n)
    evo.setdefault("acum",     [0.0] * n)
    evo.setdefault("meta_pct", [0.0] * n)

    for p in d.get("profissionais", []):
        p.setdefault("total",      p.get("qtde", 0))
        p.setdefault("final",      p.get("qtde", 0))
        p.setdefault("marcado",    0)
        p.setdefault("prod",       0.0)
        p.setdefault("taxa_final", 0.0)

    for c in d.get("convenios", []):
        c.setdefault("final", c.get("qtde", 0))

    for t in d.get("tipos", []):
        t.setdefault("final", t.get("qtde", 0))

    for a in d.get("atendentes", []):
        a.setdefault("marcado",    a.get("agendamentos", 0) - a.get("finalizados", 0))
        a.setdefault("taxa_final", round(a.get("finalizados", 0) / max(1, a.get("agendamentos", 1)) * 100, 1))

    return d


@router.get("/bi", response_class=HTMLResponse)
async def bi_dashboard(
    request: Request,
    period:  str = None,
    unit:    str = None,
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user

    bi_data_raw = _backfill_bi(load_saved(period_key=period))
    reports     = list_reports()
    bi_cfg      = get_bi_config()

    # Filtro de unidade: substitui os KPIs pelo subconjunto da unidade selecionada
    bi_data       = bi_data_raw
    current_unit  = None
    lista_unidades: list[str] = []

    if bi_data_raw:
        lista_unidades = bi_data_raw.get("lista_unidades", [])
        if unit and unit in bi_data_raw.get("por_unidade", {}):
            unit_data    = bi_data_raw["por_unidade"][unit]
            # Mantém contexto global (periodo, period_key, lista_unidades, etc.)
            # mas substitui KPIs/charts pela unidade selecionada
            bi_data = {
                **bi_data_raw,
                **unit_data,
                "periodo":         bi_data_raw["periodo"],
                "period_key":      bi_data_raw["period_key"],
                "lista_unidades":  lista_unidades,
                "por_unidade":     {},           # não repassar (pesado)
                "unidades_summary": bi_data_raw.get("unidades_summary", []),
            }
            current_unit = unit

    return templates.TemplateResponse("bi.html", {
        "request":         request,
        "user":            user,
        "active_menu":     "bi",
        "bi_data":         bi_data,
        "reports":         reports,
        "current_period":  period,
        "current_unit":    current_unit,
        "lista_unidades":  lista_unidades,
        "bi_cfg":          bi_cfg,
    })


@router.post("/bi/upload")
async def bi_upload(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user

    role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
    if role not in ("admin", "coordenacao"):
        return JSONResponse({"ok": False, "erro": "Sem permissão para importar dados."}, status_code=403)

    if not file.filename.lower().endswith((".xls", ".xlsx", ".html", ".htm")):
        return JSONResponse({"ok": False, "erro": "Formato não suportado. Envie o arquivo .xls exportado pelo sistema."}, status_code=400)

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        return JSONResponse({"ok": False, "erro": "Arquivo muito grande (máx. 50 MB)."}, status_code=400)

    try:
        data = parse_xls(content)
    except ValueError as e:
        return JSONResponse({"ok": False, "erro": str(e)}, status_code=422)
    except Exception as e:
        print("BI UPLOAD ERROR:", repr(e))
        return JSONResponse({"ok": False, "erro": "Erro ao processar o arquivo. Verifique se é o export correto."}, status_code=500)

    k = data["kpis"]
    save_error = data.get("_save_error")
    return JSONResponse({
        "ok":         True,
        "period_key": data["period_key"],
        "periodo":    data["periodo"]["label"],
        "linhas":     data["total_registros"],
        "finalizados": k["total_atendimentos"],
        "producao":   k["total_producao"],
        "save_error": save_error,  # None = salvo, string = erro do Supabase
    })


@router.delete("/bi/reports/{period_key}")
async def bi_delete_report(period_key: str, request: Request, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return user

    role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
    if role not in ("admin", "coordenacao"):
        return JSONResponse({"ok": False, "erro": "Sem permissão."}, status_code=403)

    clear_saved(period_key=period_key)
    return JSONResponse({"ok": True})


@router.post("/bi/parametros")
async def bi_save_parametros(
    request: Request,
    meta_mensal:    float = Form(...),
    dias_uteis_mes: float = Form(...),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user

    role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
    if role not in ("admin", "coordenacao"):
        return JSONResponse({"ok": False, "erro": "Sem permissão."}, status_code=403)

    if meta_mensal <= 0 or dias_uteis_mes <= 0:
        return JSONResponse({"ok": False, "erro": "Valores devem ser maiores que zero."}, status_code=422)

    cfg_err = save_bi_config({"meta_mensal": meta_mensal, "dias_uteis_mes": dias_uteis_mes})

    n_recalc, recalc_err = recalculate_all_reports(meta_mensal, dias_uteis_mes)

    avisos = [e for e in [cfg_err, recalc_err] if e]
    return JSONResponse({
        "ok":          True,
        "recalculados": n_recalc,
        "aviso":       "; ".join(avisos) if avisos else None,
    })
