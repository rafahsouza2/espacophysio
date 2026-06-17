from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.database import get_supabase

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/pacientes", tags=["pacientes"])

ALLOWED_ROLES = ["admin", "coordenacao", "recepcao", "fisioterapeuta"]


@router.get("", response_class=HTMLResponse)
async def pacientes_index(request: Request, q: str = None):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ALLOWED_ROLES:
        return RedirectResponse(url="/dashboard", status_code=302)

    supabase = get_supabase()
    try:
        query = supabase.table("pacientes").select("*").eq("ativo", True).order("nome")
        if q:
            query = query.ilike("nome", f"%{q}%")
        result = query.execute()
        pacientes = result.data or []
    except Exception:
        pacientes = []

    return templates.TemplateResponse(
        "modules/pacientes.html",
        {
            "request": request,
            "user": user,
            "active_menu": "pacientes",
            "pacientes": pacientes,
            "busca": q or "",
        },
    )


@router.get("/novo", response_class=HTMLResponse)
async def novo_paciente_form(request: Request):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ALLOWED_ROLES:
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse(
        "modules/paciente_form.html",
        {"request": request, "user": user, "active_menu": "pacientes", "paciente": None},
    )


@router.post("/novo")
async def criar_paciente(
    request: Request,
    nome: str = Form(...),
    cpf: str = Form(None),
    data_nascimento: str = Form(None),
    telefone: str = Form(None),
    email: str = Form(None),
    responsavel: str = Form(None),
    observacoes: str = Form(None),
):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user

    supabase = get_supabase()
    supabase.table("pacientes").insert({
        "nome": nome,
        "cpf": cpf or None,
        "data_nascimento": data_nascimento or None,
        "telefone": telefone,
        "email": email,
        "responsavel": responsavel,
        "observacoes": observacoes,
        "created_by": user["id"],
    }).execute()

    return RedirectResponse(url="/pacientes", status_code=302)


@router.get("/{paciente_id}", response_class=HTMLResponse)
async def ver_paciente(request: Request, paciente_id: str):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ALLOWED_ROLES:
        return RedirectResponse(url="/dashboard", status_code=302)

    supabase = get_supabase()
    try:
        result = supabase.table("pacientes").select("*").eq("id", paciente_id).single().execute()
        paciente = result.data
    except Exception:
        return RedirectResponse(url="/pacientes", status_code=302)

    try:
        agenda_result = (
            supabase.table("agendamentos")
            .select("*, profiles(full_name)")
            .eq("paciente_id", paciente_id)
            .order("data_hora", desc=True)
            .limit(10)
            .execute()
        )
        historico = agenda_result.data or []
    except Exception:
        historico = []

    return templates.TemplateResponse(
        "modules/paciente_detalhe.html",
        {
            "request": request,
            "user": user,
            "active_menu": "pacientes",
            "paciente": paciente,
            "historico": historico,
        },
    )
