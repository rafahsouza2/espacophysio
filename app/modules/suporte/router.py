from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.database import get_supabase

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/suporte", tags=["suporte"])


@router.get("", response_class=HTMLResponse)
async def suporte_index(request: Request):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user

    supabase = get_supabase()

    query = supabase.table("chamados").select("*, profiles(full_name)").order("created_at", desc=True)
    if user["role"] not in ["admin", "coordenacao"]:
        query = query.eq("autor_id", user["id"])

    try:
        result = query.limit(30).execute()
        chamados = result.data or []
    except Exception:
        chamados = []

    return templates.TemplateResponse(
        "modules/suporte.html",
        {
            "request": request,
            "user": user,
            "active_menu": "suporte",
            "chamados": chamados,
        },
    )


@router.post("/novo")
async def abrir_chamado(
    request: Request,
    titulo: str = Form(...),
    descricao: str = Form(...),
    prioridade: str = Form("media"),
):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user

    supabase = get_supabase()
    supabase.table("chamados").insert({
        "titulo": titulo,
        "descricao": descricao,
        "prioridade": prioridade,
        "status": "aberto",
        "autor_id": user["id"],
    }).execute()

    return RedirectResponse(url="/suporte", status_code=302)


@router.post("/{chamado_id}/status")
async def atualizar_chamado(
    request: Request,
    chamado_id: str,
    status: str = Form(...),
):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ["admin", "coordenacao"]:
        return RedirectResponse(url="/suporte", status_code=302)

    supabase = get_supabase()
    supabase.table("chamados").update({"status": status}).eq("id", chamado_id).execute()
    return RedirectResponse(url="/suporte", status_code=302)
