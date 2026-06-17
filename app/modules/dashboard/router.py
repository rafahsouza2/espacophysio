from pathlib import Path
from datetime import date
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.database import get_supabase

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user

    supabase = get_supabase()
    today = date.today().isoformat()

    try:
        agenda_hoje = (
            supabase.table("agendamentos")
            .select("id", count="exact")
            .gte("data_hora", f"{today}T00:00:00")
            .lte("data_hora", f"{today}T23:59:59")
            .execute()
        )
        total_agendamentos = agenda_hoje.count or 0
    except Exception:
        total_agendamentos = 0

    try:
        pendencias = (
            supabase.table("transacoes")
            .select("id", count="exact")
            .eq("status", "pendente")
            .execute()
        )
        total_pendencias = pendencias.count or 0
    except Exception:
        total_pendencias = 0

    try:
        proximos = (
            supabase.table("agendamentos")
            .select("*, pacientes(nome), profiles(full_name)")
            .gte("data_hora", f"{today}T00:00:00")
            .lte("data_hora", f"{today}T23:59:59")
            .order("data_hora")
            .limit(5)
            .execute()
        )
        agendamentos_hoje = proximos.data or []
    except Exception:
        agendamentos_hoje = []

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "active_menu": "dashboard",
            "total_agendamentos": total_agendamentos,
            "total_pendencias": total_pendencias,
            "agendamentos_hoje": agendamentos_hoje,
        },
    )
