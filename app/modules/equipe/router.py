from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.database import get_supabase, get_supabase_admin

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/equipe", tags=["equipe"])

ALLOWED_ROLES = ["admin", "coordenacao"]


@router.get("", response_class=HTMLResponse)
async def equipe_index(request: Request):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ALLOWED_ROLES:
        return RedirectResponse(url="/dashboard", status_code=302)

    supabase = get_supabase()
    try:
        result = supabase.table("profiles").select("*").order("full_name").execute()
        membros = result.data or []
    except Exception:
        membros = []

    return templates.TemplateResponse(
        "modules/equipe.html",
        {
            "request": request,
            "user": user,
            "active_menu": "equipe",
            "membros": membros,
        },
    )


@router.post("/convidar")
async def convidar_usuario(
    request: Request,
    email: str = Form(...),
    full_name: str = Form(...),
    role: str = Form("recepcao"),
):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ["admin"]:
        return RedirectResponse(url="/equipe", status_code=302)

    try:
        admin_client = get_supabase_admin()
        invite = admin_client.auth.admin.invite_user_by_email(email)
        if invite.user:
            admin_client.table("profiles").upsert({
                "id": invite.user.id,
                "full_name": full_name,
                "role": role,
            }).execute()
    except Exception:
        pass

    return RedirectResponse(url="/equipe", status_code=302)


@router.post("/{profile_id}/role")
async def atualizar_role(
    request: Request,
    profile_id: str,
    role: str = Form(...),
):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] != "admin":
        return RedirectResponse(url="/equipe", status_code=302)

    supabase = get_supabase()
    supabase.table("profiles").update({"role": role}).eq("id", profile_id).execute()
    return RedirectResponse(url="/equipe", status_code=302)
