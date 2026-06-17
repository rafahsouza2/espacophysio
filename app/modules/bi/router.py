from pathlib import Path
from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.modules.bi.parser import parse_xls, load_saved, clear_saved, list_reports

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["bi"])


@router.get("/bi", response_class=HTMLResponse)
async def bi_dashboard(request: Request, period: str = None, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return user

    bi_data = load_saved(period_key=period)
    reports  = list_reports()

    return templates.TemplateResponse("bi.html", {
        "request":        request,
        "user":           user,
        "active_menu":    "bi",
        "bi_data":        bi_data,
        "reports":        reports,
        "current_period": period,
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
    return JSONResponse({
        "ok":         True,
        "period_key": data["period_key"],
        "periodo":    data["periodo"]["label"],
        "linhas":     data["total_registros"],
        "finalizados": k["total_atendimentos"],
        "producao":   k["total_producao"],
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
