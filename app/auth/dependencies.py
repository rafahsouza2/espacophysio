from pathlib import Path
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from supabase import Client

from app.database import get_supabase, get_supabase_admin

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": ["dashboard", "financeiro", "agenda", "pacientes", "equipe", "relatorios", "comunicados", "suporte"],
    "coordenacao": ["dashboard", "financeiro", "agenda", "pacientes", "equipe", "relatorios", "comunicados", "suporte"],
    "financeiro": ["dashboard", "financeiro", "relatorios"],
    "recepcao": ["dashboard", "agenda", "pacientes", "comunicados"],
    "fisioterapeuta": ["dashboard", "agenda", "pacientes"],
}


def _fetch_profile(user_id: str, email: str) -> dict:
    try:
        admin = get_supabase_admin()
        resp = admin.table("profiles").select("*").eq("id", user_id).limit(1).execute()
        if resp and resp.data:
            return resp.data[0]
        # Perfil não existe — cria com role padrão
        admin.table("profiles").upsert({
            "id": user_id,
            "full_name": email,
            "email": email,
            "role": "recepcao",
        }).execute()
        return {"role": "recepcao", "full_name": email, "active": True}
    except Exception as e:
        print("PROFILE FETCH ERROR:", repr(e))
        # Retorna dados mínimos para não bloquear o acesso
        return {"role": "recepcao", "full_name": email, "active": True}


async def get_current_user(request: Request) -> dict:
    access_token = request.cookies.get("access_token")
    if not access_token:
        raise HTTPException(status_code=401, detail="Não autenticado")

    try:
        supabase: Client = get_supabase()
        user_resp = supabase.auth.get_user(access_token)
        if not user_resp or not user_resp.user:
            raise HTTPException(status_code=401, detail="Token inválido")

        user = user_resp.user
        profile = _fetch_profile(user.id, user.email)

        return {
            "id": user.id,
            "email": user.email,
            "full_name": profile.get("full_name", user.email),
            "role": profile.get("role", "recepcao"),
            "active": profile.get("active", True),
        }
    except HTTPException:
        raise
    except Exception as e:
        print("AUTH ERROR:", repr(e))
        raise HTTPException(status_code=401, detail="Sessão expirada")


async def require_auth(request: Request) -> dict:
    try:
        return await get_current_user(request)
    except HTTPException:
        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie("access_token")
        response.delete_cookie("refresh_token")
        return response


def require_role(allowed_roles: list[str]):
    async def checker(request: Request) -> dict:
        user = await require_auth(request)
        if isinstance(user, RedirectResponse):
            return user
        if user["role"] not in allowed_roles:
            raise HTTPException(status_code=403, detail="Acesso negado")
        return user
    return checker
