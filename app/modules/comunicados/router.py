from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.database import get_supabase

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/comunicados", tags=["comunicados"])


@router.get("", response_class=HTMLResponse)
async def comunicados_index(request: Request):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user

    supabase = get_supabase()
    try:
        result = (
            supabase.table("comunicados")
            .select("*, profiles(full_name)")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        items = result.data or []
    except Exception:
        items = []

    pode_criar = user["role"] in ["admin", "coordenacao"]

    return templates.TemplateResponse(
        "modules/comunicados.html",
        {
            "request": request,
            "user": user,
            "active_menu": "comunicados",
            "comunicados": items,
            "pode_criar": pode_criar,
        },
    )


@router.post("/novo")
async def criar_comunicado(
    request: Request,
    titulo: str = Form(...),
    conteudo: str = Form(...),
    publico_alvo: str = Form("todos"),
):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ["admin", "coordenacao"]:
        return RedirectResponse(url="/comunicados", status_code=302)

    supabase = get_supabase()
    supabase.table("comunicados").insert({
        "titulo": titulo,
        "conteudo": conteudo,
        "publico_alvo": publico_alvo,
        "autor_id": user["id"],
    }).execute()

    return RedirectResponse(url="/comunicados", status_code=302)
