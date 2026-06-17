from pathlib import Path
from datetime import date
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.database import get_supabase

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/agenda", tags=["agenda"])

ALLOWED_ROLES = ["admin", "coordenacao", "recepcao", "fisioterapeuta"]


@router.get("", response_class=HTMLResponse)
async def agenda_index(request: Request, data: str = None):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ALLOWED_ROLES:
        return RedirectResponse(url="/dashboard", status_code=302)

    filtro_data = data or date.today().isoformat()
    supabase = get_supabase()

    try:
        agendamentos = (
            supabase.table("agendamentos")
            .select("*, pacientes(nome, telefone), profiles(full_name)")
            .gte("data_hora", f"{filtro_data}T00:00:00")
            .lte("data_hora", f"{filtro_data}T23:59:59")
            .order("data_hora")
            .execute()
        )
        items = agendamentos.data or []
    except Exception:
        items = []

    try:
        pacientes_resp = supabase.table("pacientes").select("id, nome").eq("ativo", True).order("nome").execute()
        pacientes = pacientes_resp.data or []
    except Exception:
        pacientes = []

    try:
        profissionais_resp = (
            supabase.table("profiles")
            .select("id, full_name")
            .in_("role", ["fisioterapeuta", "coordenacao", "admin"])
            .eq("active", True)
            .execute()
        )
        profissionais = profissionais_resp.data or []
    except Exception:
        profissionais = []

    return templates.TemplateResponse(
        "modules/agenda.html",
        {
            "request": request,
            "user": user,
            "active_menu": "agenda",
            "agendamentos": items,
            "filtro_data": filtro_data,
            "pacientes": pacientes,
            "profissionais": profissionais,
        },
    )


@router.post("/novo")
async def novo_agendamento(
    request: Request,
    paciente_id: str = Form(...),
    profissional_id: str = Form(...),
    data_hora: str = Form(...),
    tipo: str = Form(...),
    sala: str = Form(None),
    observacoes: str = Form(None),
):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ALLOWED_ROLES:
        return RedirectResponse(url="/dashboard", status_code=302)

    supabase = get_supabase()
    supabase.table("agendamentos").insert({
        "paciente_id": paciente_id,
        "profissional_id": profissional_id,
        "data_hora": data_hora,
        "tipo": tipo,
        "sala": sala,
        "observacoes": observacoes,
        "status": "agendado",
        "created_by": user["id"],
    }).execute()

    data_filtro = data_hora[:10] if data_hora else date.today().isoformat()
    return RedirectResponse(url=f"/agenda?data={data_filtro}", status_code=302)


@router.post("/{agendamento_id}/status")
async def atualizar_status(
    request: Request,
    agendamento_id: str,
    status: str = Form(...),
):
    user = await require_auth(request)
    if isinstance(user, RedirectResponse):
        return user

    supabase = get_supabase()
    supabase.table("agendamentos").update({"status": status}).eq("id", agendamento_id).execute()
    return RedirectResponse(url="/agenda", status_code=302)
