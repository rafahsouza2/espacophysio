from pathlib import Path
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from supabase import Client

from app.database import get_supabase

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": ["dashboard", "financeiro", "agenda", "pacientes", "equipe", "relatorios", "comunicados", "suporte"],
    "coordenacao": ["dashboard", "financeiro", "agenda", "pacientes", "equipe", "relatorios", "comunicados", "suporte"],
    "financeiro": ["dashboard", "financeiro", "relatorios"],
    "recepcao": ["dashboard", "agenda", "pacientes", "comunicados"],
    "fisioterapeuta": ["dashboard", "agenda", "pacientes"],
}


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
        profile_resp = (
            supabase.table("profiles")
            .select("*")
            .eq("id", user.id)
            .single()
            .execute()
        )
        profile = profile_resp.data or {}
        return {
            "id": user.id,
            "email": user.email,
            "full_name": profile.get("full_name", user.email),
            "role": profile.get("role", "recepcao"),
            "active": profile.get("active", True),
        }
    except Exception:
        raise HTTPException(status_code=401, detail="Sessão expirada")


async def require_auth(request: Request) -> dict:
    try:
        return await get_current_user(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)


def require_role(allowed_roles: list[str]):
    async def checker(request: Request) -> dict:
        user = await require_auth(request)
        if isinstance(user, RedirectResponse):
            return user
        if user["role"] not in allowed_roles:
            raise HTTPException(status_code=403, detail="Acesso negado")
        return user
    return checker
