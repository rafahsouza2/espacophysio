from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.database import get_supabase

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    access_token = request.cookies.get("access_token")
    if access_token:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    try:
        supabase = get_supabase()
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})

        if not response.session:
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "E-mail ou senha inválidos."},
                status_code=401,
            )

        redirect = RedirectResponse(url="/dashboard", status_code=302)
        from app.config import settings
        is_prod = settings.app_env == "production"
        redirect.set_cookie(
            key="access_token",
            value=response.session.access_token,
            httponly=True,
            secure=is_prod,
            samesite="lax",
            max_age=60 * 60 * 8,  # 8 hours
        )
        redirect.set_cookie(
            key="refresh_token",
            value=response.session.refresh_token,
            httponly=True,
            secure=is_prod,
            samesite="lax",
            max_age=60 * 60 * 24 * 7,  # 7 days
        )
        return redirect

    except Exception as e:
        import traceback
        print("LOGIN ERROR:", repr(e))
        traceback.print_exc()
        error_msg = str(e)
        if "Invalid login" in error_msg or "invalid_credentials" in error_msg:
            error_msg = "E-mail ou senha inválidos."
        elif "Email not confirmed" in error_msg:
            error_msg = "Por favor, confirme seu e-mail antes de acessar."
        else:
            error_msg = f"Erro: {str(e)}"

        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": error_msg},
            status_code=400,
        )


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response
