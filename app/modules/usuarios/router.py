from pathlib import Path
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.database import get_supabase_admin
from app.modules.usuarios.permissions import set_permission, delete_permission

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(tags=["usuarios"])

MODULES = [
    {"key": "bi",           "label": "B.I — Indicadores"},
    {"key": "autorizacoes", "label": "Autorizações"},
    {"key": "comunicados",  "label": "Comunicados"},
]

ROLE_LABELS = {
    "admin":         "Administrador",
    "coordenacao":   "Coordenação",
    "financeiro":    "Financeiro",
    "recepcao":      "Recepção",
    "fisioterapeuta":"Fisioterapeuta",
}


def _is_admin(user: dict) -> bool:
    return user.get("role") in ("admin", "coordenacao")


@router.get("/usuarios", response_class=HTMLResponse)
async def usuarios_lista(request: Request, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return user
    if not _is_admin(user):
        return RedirectResponse(url="/bi", status_code=302)

    sb = get_supabase_admin()
    resp = sb.table("profiles").select("*").neq("id", user["id"]).order("full_name").execute()
    usuarios = resp.data or []

    return templates.TemplateResponse("usuarios.html", {
        "request":     request,
        "user":        user,
        "active_menu": "usuarios",
        "usuarios":    usuarios,
        "modules":     MODULES,
        "role_labels": ROLE_LABELS,
    })


@router.post("/usuarios/criar")
async def usuarios_criar(
    request: Request,
    full_name:          str = Form(...),
    email:              str = Form(...),
    password:           str = Form(...),
    role:               str = Form(...),
    modulos_permitidos: str = Form(""),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user
    if not _is_admin(user):
        return JSONResponse({"ok": False, "erro": "Sem permissão."}, status_code=403)

    if role not in ROLE_LABELS:
        return JSONResponse({"ok": False, "erro": "Papel inválido."}, status_code=422)

    sb = get_supabase_admin()

    mods = modulos_permitidos.strip() or None
    if role in ("admin", "coordenacao"):
        mods = None

    try:
        auth_resp = sb.auth.admin.create_user({
            "email":          email,
            "password":       password,
            "email_confirm":  True,
            "user_metadata":  {"full_name": full_name, "modulos_permitidos": mods},
        })
        new_id = auth_resp.user.id
    except Exception as e:
        msg = str(e)
        if "already registered" in msg or "already been registered" in msg:
            msg = "Este e-mail já está cadastrado."
        return JSONResponse({"ok": False, "erro": msg}, status_code=400)

    profile_data = {
        "id":       new_id,
        "full_name": full_name,
        "email":    email,
        "role":     role,
        "active":   True,
    }
    if mods is not None:
        profile_data["modulos_permitidos"] = mods

    try:
        sb.table("profiles").upsert(profile_data).execute()
    except Exception as e:
        err_msg = str(e)
        if "modulos_permitidos" in err_msg:
            profile_data.pop("modulos_permitidos", None)
            try:
                sb.table("profiles").upsert(profile_data).execute()
            except Exception as e2:
                return JSONResponse({"ok": False, "erro": f"Usuário criado mas erro no perfil: {e2}"}, status_code=500)
        else:
            return JSONResponse({"ok": False, "erro": f"Usuário criado mas erro no perfil: {e}"}, status_code=500)

    # Salva restrição de módulo em arquivo local (funciona sem migration SQL)
    set_permission(new_id, mods)

    return JSONResponse({"ok": True})


@router.post("/usuarios/{user_id}/editar")
async def usuarios_editar(
    request:    Request,
    user_id:    str,
    full_name:          str = Form(...),
    role:               str = Form(...),
    modulos_permitidos: str = Form(""),
    ativo:              str = Form("true"),
    nova_senha:         str = Form(""),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user
    if not _is_admin(user):
        return JSONResponse({"ok": False, "erro": "Sem permissão."}, status_code=403)

    # admin/coordenacao têm acesso total
    mods = modulos_permitidos.strip() or None
    if role in ("admin", "coordenacao"):
        mods = None

    sb = get_supabase_admin()
    update_data = {
        "full_name": full_name,
        "role":      role,
        "active":    ativo == "true",
    }
    if mods is not None:
        update_data["modulos_permitidos"] = mods

    try:
        sb.table("profiles").update(update_data).eq("id", user_id).execute()
    except Exception as e:
        err_msg = str(e)
        if "modulos_permitidos" in err_msg:
            update_data.pop("modulos_permitidos", None)
            try:
                sb.table("profiles").update(update_data).eq("id", user_id).execute()
            except Exception as e2:
                return JSONResponse({"ok": False, "erro": str(e2)}, status_code=500)
        else:
            return JSONResponse({"ok": False, "erro": str(e)}, status_code=500)

    # Atualiza senha se necessário
    if nova_senha.strip():
        try:
            sb.auth.admin.update_user_by_id(user_id, {"password": nova_senha.strip()})
        except Exception as e:
            return JSONResponse({"ok": False, "erro": f"Perfil salvo, mas erro na senha: {e}"}, status_code=500)

    # Salva restrição de módulo em arquivo local (garantido, sem depender do schema)
    set_permission(user_id, mods)

    return JSONResponse({"ok": True})


@router.post("/usuarios/{user_id}/excluir")
async def usuarios_excluir(
    request: Request,
    user_id: str,
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user
    if not _is_admin(user):
        return JSONResponse({"ok": False, "erro": "Sem permissão."}, status_code=403)
    if user_id == user["id"]:
        return JSONResponse({"ok": False, "erro": "Não é possível excluir o próprio usuário."}, status_code=400)

    sb = get_supabase_admin()
    try:
        sb.table("profiles").delete().eq("id", user_id).execute()
        sb.auth.admin.delete_user(user_id)
    except Exception as e:
        return JSONResponse({"ok": False, "erro": str(e)}, status_code=500)

    delete_permission(user_id)
    return JSONResponse({"ok": True})


@router.post("/usuarios/{user_id}/toggle-ativo")
async def usuarios_toggle_ativo(
    request: Request,
    user_id: str,
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user
    if not _is_admin(user):
        return JSONResponse({"ok": False, "erro": "Sem permissão."}, status_code=403)

    sb = get_supabase_admin()
    resp = sb.table("profiles").select("active").eq("id", user_id).limit(1).execute()
    if not resp.data:
        return JSONResponse({"ok": False, "erro": "Usuário não encontrado."}, status_code=404)

    novo = not resp.data[0]["active"]
    sb.table("profiles").update({"active": novo}).eq("id", user_id).execute()
    return JSONResponse({"ok": True, "active": novo})
