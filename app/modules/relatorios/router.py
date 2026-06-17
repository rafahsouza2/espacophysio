from pathlib import Path
from datetime import date, timedelta
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.database import get_supabase

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/relatorios", tags=["relatorios"])

ALLOWED_ROLES = ["admin", "coordenacao", "financeiro"]


@router.get("", response_class=HTMLResponse)
async def relatorios_index(request: Request):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ALLOWED_ROLES:
        return RedirectResponse(url="/dashboard", status_code=302)

    supabase = get_supabase()
    hoje = date.today()
    inicio_mes = hoje.replace(day=1).isoformat()
    fim_mes = hoje.isoformat()

    try:
        transacoes_mes = (
            supabase.table("transacoes")
            .select("*")
            .gte("created_at", inicio_mes)
            .lte("created_at", fim_mes)
            .execute()
        )
        transacoes = transacoes_mes.data or []
    except Exception:
        transacoes = []

    receita_mes = sum(t["valor"] for t in transacoes if t.get("tipo") == "receita" and t.get("status") == "pago")
    despesa_mes = sum(t["valor"] for t in transacoes if t.get("tipo") == "despesa" and t.get("status") == "pago")
    pendentes_mes = sum(t["valor"] for t in transacoes if t.get("status") == "pendente")

    try:
        agenda_mes = (
            supabase.table("agendamentos")
            .select("status", count="exact")
            .gte("data_hora", inicio_mes)
            .lte("data_hora", fim_mes)
            .execute()
        )
        total_agendamentos_mes = agenda_mes.count or 0
    except Exception:
        total_agendamentos_mes = 0

    return templates.TemplateResponse(
        "modules/relatorios.html",
        {
            "request": request,
            "user": user,
            "active_menu": "relatorios",
            "receita_mes": receita_mes,
            "despesa_mes": despesa_mes,
            "pendentes_mes": pendentes_mes,
            "total_agendamentos_mes": total_agendamentos_mes,
            "mes_referencia": hoje.strftime("%B %Y"),
        },
    )
