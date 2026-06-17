from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from app.config import settings
from app.auth.router import router as auth_router
from app.modules.bi.router import router as bi_router
from app.modules.comunicados.router import router as comunicados_router

BASE_DIR = Path(__file__).parent

app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth_router)
app.include_router(bi_router)
app.include_router(comunicados_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/bi")


@app.get("/dashboard")
async def legacy_dashboard():
    return RedirectResponse(url="/bi")
