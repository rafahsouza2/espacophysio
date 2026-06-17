from pathlib import Path
from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.modules.bi.parser import parse_xls, load_saved

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["bi"])


@router.get("/bi", response_class=HTMLResponse)
async def bi_dashboard(request: Request, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return user

    bi_data = load_saved()   # None se nunca fez upload

    return templates.TemplateResponse("bi.html", {
        "request":  request,
        "user":     user,
        "active_menu": "bi",
        "bi_data":  bi_data,   # None → template usa dados demo
    })


@router.post("/bi/upload")
async def bi_upload(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user

    # Apenas admin e coordenacao podem importar dados
    if user.get("role") not in ("admin", "coordenacao"):
        return JSONResponse({"ok": False, "erro": "Sem permissão para importar dados."}, status_code=403)

    if not file.filename.lower().endswith((".xls", ".xlsx", ".html", ".htm")):
        return JSONResponse({"ok": False, "erro": "Formato não suportado. Envie o arquivo .xls exportado pelo sistema."}, status_code=400)

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:   # 50 MB
        return JSONResponse({"ok": False, "erro": "Arquivo muito grande (máx. 50 MB)."}, status_code=400)

    try:
        data = parse_xls(content)
    except ValueError as e:
        return JSONResponse({"ok": False, "erro": str(e)}, status_code=422)
    except Exception as e:
        print("BI UPLOAD ERROR:", repr(e))
        return JSONResponse({"ok": False, "erro": "Erro ao processar o arquivo. Verifique se é o export correto."}, status_code=500)

    return JSONResponse({
        "ok":      True,
        "periodo": data["periodo"]["label"],
        "linhas":  data["total_registros"],
        "kpis":    data["kpis"],
    })
