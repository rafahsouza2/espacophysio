from pathlib import Path
from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth, check_module_access
from app.modules.bi.parser import parse_xls, load_saved, clear_saved, list_reports
from app.modules.bi.config import get_bi_config, save_bi_config, recalculate_all_reports

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["bi"])


def _backfill_bi(d: dict) -> dict:
    """Adiciona campos novos a relatórios gerados pelo parser antigo."""
    if not d:
        return d

    # Backfill de faturamento em dados sem esses campos
    k = d.get("kpis", {})
    k.setdefault("total_faturamento",  0.0)
    k.setdefault("total_faturado_cnt", 0)
    k.setdefault("pct_faturado",       0.0)
    k.setdefault("a_faturar",          float(k.get("total_producao", 0)))
    k.setdefault("pacientes_com_faltas", 0)
    k.setdefault("total_agendamentos",   k.get("total_agendados", 0))
    k.setdefault("total_confirmados",    0)
    k.setdefault("total_reagendados",    0)
    k.setdefault("total_desmarcados_ag", k.get("total_cancelados", 0))
    k.setdefault("total_faltas_ag",      k.get("total_faltas", 0))

    d.setdefault("perdas_distribuicao",  {})
    d.setdefault("especialidades",       [])
    d.setdefault("faixa_etaria",         {})
    d.setdefault("genero",               {"feminino": 0, "masculino": 0})
    d.setdefault("localidade",           {})
    d.setdefault("convenios_pacientes",  {})
    k.setdefault("idade_media",    0.0)
    k.setdefault("total_acima_60", 0)
    k.setdefault("total_acima_80", 0)
    k.setdefault("pct_acima_60",   0.0)
    k.setdefault("pct_acima_80",   0.0)
    for p in d.get("profissionais", []):
        p.setdefault("desmarcados", p.get("marcado", 0))
        p.setdefault("faltas_ag",   0)
        p.setdefault("reagendados", 0)

    if "alcance_meta" in d:
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
    redir = check_module_access(user, "bi")
    if redir:
        return redir

    bi_data_raw = _backfill_bi(load_saved(period_key=period))
    reports     = list_reports()
    bi_cfg      = get_bi_config()

    # Filtro de unidade: substitui os KPIs pelo subconjunto da unidade selecionada
    bi_data       = bi_data_raw
    current_unit  = None
    lista_unidades: list[str] = []

    if bi_data_raw:
        lista_unidades = bi_data_raw.get("lista_unidades", [])

    # Fallback: usa as unidades das metas já salvas para não esconder a seção de parâmetros
    metas_salvas = bi_cfg.get("metas_por_unidade", {})
    for k in metas_salvas:
        if k not in lista_unidades:
            lista_unidades.append(k)
        if unit and unit in bi_data_raw.get("por_unidade", {}):
            unit_data = bi_data_raw["por_unidade"][unit]
            bi_data = {
                **bi_data_raw,
                **unit_data,
                "periodo":          bi_data_raw["periodo"],
                "period_key":       bi_data_raw["period_key"],
                "lista_unidades":   lista_unidades,
                "por_unidade":      {},
                "unidades_summary": bi_data_raw.get("unidades_summary", []),
            }
            current_unit = unit

            # Aplica meta específica da unidade (se configurada)
            metas_un = bi_cfg.get("metas_por_unidade", {})
            if unit in metas_un and metas_un[unit] > 0:
                meta_u   = float(metas_un[unit])
                dias_u   = float(bi_cfg.get("dias_uteis_mes", 22))
                prod_u   = float(bi_data.get("kpis", {}).get("total_producao", 0))
                meta_dia = meta_u / dias_u if dias_u else meta_u
                bi_data["meta"]         = meta_u
                bi_data["meta_diaria"]  = round(meta_dia, 2)
                bi_data["alcance_meta"] = round(prod_u / meta_u * 100, 1) if meta_u else 0.0
                evo  = bi_data.get("evolucao_diaria", {})
                prods = evo.get("prod", [])
                if prods:
                    evo["meta_pct"] = [round(p / meta_dia * 100, 1) if meta_dia else 0.0 for p in prods]

    return templates.TemplateResponse("bi.html", {
        "request":        request,
        "user":           user,
        "active_menu":    "bi",
        "bi_data":        bi_data,
        "reports":        reports,
        "current_period": period,
        "current_unit":   current_unit,
        "lista_unidades": lista_unidades,
        "bi_cfg":         bi_cfg,
    })


@router.get("/bi/listagem")
async def bi_listagem(
    request:  Request,
    period:       str  = None,
    convenio:     str  = None,
    unidade:      str  = None,
    profissional: str  = None,
    faturado:     str  = None,   # "sim" | "nao" | None
    page:         int  = 1,
    per_page:     int  = 50,
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user
    redir = check_module_access(user, "bi")
    if redir:
        return redir

    try:
        from app.database import get_supabase_admin
        sb  = get_supabase_admin()

        def _apply_filters(q):
            if period:       q = q.eq("period_key", period)
            if convenio:     q = q.ilike("convenio", f"%{convenio}%")
            if unidade:      q = q.ilike("unidade", f"%{unidade}%")
            if profissional: q = q.ilike("profissional", f"%{profissional}%")
            if faturado == "sim": q = q.eq("faturado", True)
            elif faturado == "nao": q = q.eq("faturado", False)
            return q

        qb  = _apply_filters(sb.table("bi_atendimentos").select(
            "data_atend,paciente,profissional,especialidade,convenio,unidade,valor,faturado,protocolo_lote,status",
            count="exact"
        ))
        offset = (page - 1) * per_page
        qb = qb.order("data_atend", desc=True).range(offset, offset + per_page - 1)
        res   = qb.execute()
        rows  = res.data or []
        total = res.count or 0

        # Totais do filtro atual (sem paginação) para os cards de resumo
        qtot = _apply_filters(sb.table("bi_atendimentos").select("valor,faturado", count="exact"))
        # limita a 10k para não sobrecarregar
        tres  = qtot.limit(10000).execute()
        trows = tres.data or []
        soma_total    = round(sum(r["valor"] or 0 for r in trows), 2)
        soma_faturado = round(sum(r["valor"] or 0 for r in trows if r["faturado"]), 2)

        return JSONResponse({
            "ok": True, "rows": rows, "total": total,
            "page": page, "per_page": per_page,
            "pages": max(1, -(-total // per_page)),
            "soma_total": soma_total,
            "soma_faturado": soma_faturado,
            "soma_a_faturar": round(soma_total - soma_faturado, 2),
        })
    except Exception as e:
        return JSONResponse({"ok": False, "erro": str(e)}, status_code=500)


@router.get("/bi/listagem/convenios")
async def bi_listagem_convenios(request: Request, period: str = None, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return user
    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        q  = sb.table("bi_atendimentos").select("convenio")
        if period:
            q = q.eq("period_key", period)
        res = q.limit(5000).execute()
        items = sorted({r["convenio"] for r in (res.data or []) if r.get("convenio")})
        return JSONResponse({"ok": True, "items": items})
    except Exception as e:
        return JSONResponse({"ok": False, "items": [], "erro": str(e)})


@router.get("/bi/listagem/profissionais")
async def bi_listagem_profissionais(request: Request, period: str = None, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return user
    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        q  = sb.table("bi_atendimentos").select("profissional")
        if period:
            q = q.eq("period_key", period)
        res = q.limit(5000).execute()
        items = sorted({r["profissional"] for r in (res.data or []) if r.get("profissional")})
        return JSONResponse({"ok": True, "items": items})
    except Exception as e:
        return JSONResponse({"ok": False, "items": [], "erro": str(e)})


@router.get("/bi/listagem/export")
async def bi_listagem_export(
    request:  Request,
    period:       str = None,
    convenio:     str = None,
    unidade:      str = None,
    profissional: str = None,
    faturado:     str = None,
    user=Depends(require_auth),
):
    """Exporta a listagem filtrada como CSV."""
    import io as _io
    import csv as _csv
    from fastapi.responses import StreamingResponse

    if isinstance(user, RedirectResponse):
        return user
    redir = check_module_access(user, "bi")
    if redir:
        return redir

    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        qb = sb.table("bi_atendimentos").select(
            "data_atend,paciente,profissional,especialidade,convenio,unidade,valor,faturado,protocolo_lote,status"
        )
        if period:       qb = qb.eq("period_key", period)
        if convenio:     qb = qb.ilike("convenio", f"%{convenio}%")
        if unidade:      qb = qb.ilike("unidade",  f"%{unidade}%")
        if profissional: qb = qb.ilike("profissional", f"%{profissional}%")
        if faturado == "sim": qb = qb.eq("faturado", True)
        elif faturado == "nao": qb = qb.eq("faturado", False)
        res  = qb.order("data_atend", desc=True).limit(50000).execute()
        rows = res.data or []

        buf = _io.StringIO()
        writer = _csv.DictWriter(buf, fieldnames=[
            "data_atend","paciente","profissional","especialidade",
            "convenio","unidade","valor","faturado","protocolo_lote","status"
        ])
        writer.writeheader()
        for r in rows:
            r["faturado"] = "Sim" if r.get("faturado") else "Não"
            writer.writerow(r)

        fname = f"listagem_{period or 'todos'}.csv"
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    except Exception as e:
        return JSONResponse({"ok": False, "erro": str(e)}, status_code=500)


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


@router.post("/bi/metas-unidades")
async def bi_save_metas_unidades(
    request: Request,
    metas_json: str = Form(...),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user
    role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
    if role not in ("admin", "coordenacao"):
        return JSONResponse({"ok": False, "erro": "Sem permissão."}, status_code=403)

    import json as _json
    try:
        metas = _json.loads(metas_json)
        if not isinstance(metas, dict):
            raise ValueError("Esperado objeto JSON")
        metas = {k: float(v) for k, v in metas.items() if v}
    except Exception as e:
        return JSONResponse({"ok": False, "erro": f"Dados inválidos: {e}"}, status_code=422)

    err = save_bi_config({"metas_por_unidade": metas})
    return JSONResponse({"ok": True, "aviso": err})


@router.post("/bi/parametros")
async def bi_save_parametros(
    request: Request,
    meta_mensal:      float = Form(...),
    dias_uteis_mes:   float = Form(...),
    cidade_geocoding: str   = Form(""),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user

    role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
    if role not in ("admin", "coordenacao"):
        return JSONResponse({"ok": False, "erro": "Sem permissão."}, status_code=403)

    if meta_mensal <= 0 or dias_uteis_mes <= 0:
        return JSONResponse({"ok": False, "erro": "Valores devem ser maiores que zero."}, status_code=422)

    cfg_payload = {"meta_mensal": meta_mensal, "dias_uteis_mes": dias_uteis_mes}
    if cidade_geocoding.strip():
        cfg_payload["cidade_geocoding"] = cidade_geocoding.strip()
    cfg_err = save_bi_config(cfg_payload)

    n_recalc, recalc_err = recalculate_all_reports(meta_mensal, dias_uteis_mes)

    avisos = [e for e in [cfg_err, recalc_err] if e]
    return JSONResponse({
        "ok":          True,
        "recalculados": n_recalc,
        "aviso":       "; ".join(avisos) if avisos else None,
    })
