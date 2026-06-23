from pathlib import Path
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(tags=["home"])

MODULES_INFO = {
    "bi":           {"label": "B.I — Indicadores", "icon": "📊", "url": "/bi",           "desc": "Acompanhe produção, metas e KPIs por período e unidade"},
    "autorizacoes": {"label": "Autorizações",       "icon": "📄", "url": "/autorizacoes", "desc": "Gerencie autorizações de convênio no fluxo Kanban"},
    "comunicados":  {"label": "Comunicados",        "icon": "📢", "url": "/comunicados",  "desc": "Fique por dentro dos avisos e informações da equipe"},
    "usuarios":     {"label": "Usuários",           "icon": "👥", "url": "/usuarios",     "desc": "Gerencie acessos e permissões da equipe"},
}


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return user

    modulos = user.get("modulos_permitidos")
    role = user.get("role", "recepcao")
    is_admin = role in ("admin", "coordenacao")

    if modulos and not is_admin:
        allowed_keys = [m.strip() for m in modulos.split(",") if m.strip()]
    else:
        # sem restrição — mostra todos exceto usuários (só para admin)
        allowed_keys = ["bi", "autorizacoes", "comunicados"]
        if is_admin:
            allowed_keys.append("usuarios")

    cards = [MODULES_INFO[k] for k in allowed_keys if k in MODULES_INFO]

    return templates.TemplateResponse("home.html", {
        "request": request,
        "user":    user,
        "active_menu": "home",
        "cards":   cards,
    })
