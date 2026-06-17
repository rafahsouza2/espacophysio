from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from app.config import settings
from app.auth.router import router as auth_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.financeiro.router import router as financeiro_router
from app.modules.agenda.router import router as agenda_router
from app.modules.pacientes.router import router as pacientes_router
from app.modules.equipe.router import router as equipe_router
from app.modules.relatorios.router import router as relatorios_router
from app.modules.comunicados.router import router as comunicados_router
from app.modules.suporte.router import router as suporte_router

BASE_DIR = Path(__file__).parent

app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(financeiro_router)
app.include_router(agenda_router)
app.include_router(pacientes_router)
app.include_router(equipe_router)
app.include_router(relatorios_router)
app.include_router(comunicados_router)
app.include_router(suporte_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")
