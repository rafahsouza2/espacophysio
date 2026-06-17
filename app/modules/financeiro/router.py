from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth, require_role
from app.database import get_supabase

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/financeiro", tags=["financeiro"])

ALLOWED_ROLES = ["admin", "coordenacao", "financeiro"]


@router.get("", response_class=HTMLResponse)
async def financeiro_index(request: Request):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ALLOWED_ROLES:
        return RedirectResponse(url="/dashboard", status_code=302)

    supabase = get_supabase()
    try:
        transacoes = (
            supabase.table("transacoes")
            .select("*, pacientes(nome)")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        items = transacoes.data or []
    except Exception:
        items = []

    receita_total = sum(t["valor"] for t in items if t.get("tipo") == "receita" and t.get("status") == "pago")
    pendentes = [t for t in items if t.get("status") == "pendente"]

    return templates.TemplateResponse(
        "modules/financeiro.html",
        {
            "request": request,
            "user": user,
            "active_menu": "financeiro",
            "transacoes": items,
            "receita_total": receita_total,
            "total_pendentes": len(pendentes),
        },
    )


@router.post("/transacao")
async def criar_transacao(
    request: Request,
    descricao: str = Form(...),
    valor: float = Form(...),
    tipo: str = Form(...),
    status: str = Form("pendente"),
    forma_pagamento: str = Form(None),
):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ALLOWED_ROLES:
        return RedirectResponse(url="/dashboard", status_code=302)

    supabase = get_supabase()
    supabase.table("transacoes").insert({
        "descricao": descricao,
        "valor": valor,
        "tipo": tipo,
        "status": status,
        "forma_pagamento": forma_pagamento,
        "created_by": user["id"],
    }).execute()

    return RedirectResponse(url="/financeiro", status_code=302)
