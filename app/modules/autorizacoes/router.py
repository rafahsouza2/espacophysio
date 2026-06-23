import uuid
from pathlib import Path
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth, check_module_access
from app.database import get_supabase_admin

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["autorizacoes"])

ALLOWED_ROLES = ["admin", "coordenacao", "recepcao", "fisioterapeuta"]
WRITE_ROLES   = ["admin", "coordenacao", "recepcao"]

STATUS_LABELS = {
    "para_solicitar": "Para Solicitar",
    "em_analise":     "Em Análise",
    "autorizado":     "Autorizado",
    "impresso":       "Impresso",
    "agendado":       "Agendado",
    "pendencia":      "Pendência",
    "cancelado":      "Cancelado",
}
STATUS_ORDER = list(STATUS_LABELS.keys())

STATUS_COLORS = {
    "para_solicitar": "#9ca3af",
    "em_analise":     "#ffb547",
    "autorizado":     "#31a66a",
    "impresso":       "#3b82f6",
    "agendado":       "#16a34a",
    "pendencia":      "#e5622e",
    "cancelado":      "#d73519",
}


def _role(user) -> str:
    return user.get("role") if isinstance(user, dict) else getattr(user, "role", "")


def _nome(user) -> str:
    return user.get("full_name", "Sistema") if isinstance(user, dict) else getattr(user, "full_name", "Sistema")


# ── Helpers de contexto ────────────────────────────────────────────────────────

def _base_ctx(user) -> dict:
    return {
        "active_menu":   "autorizacoes",
        "status_labels": STATUS_LABELS,
        "status_order":  STATUS_ORDER,
        "status_colors": STATUS_COLORS,
        "can_write":     _role(user) in WRITE_ROLES,
        "is_admin":      _role(user) in ["admin", "coordenacao"],
    }


def _contadores(sb) -> dict:
    rows = sb.table("autorizacoes").select("status").execute().data or []
    cnt  = {s: 0 for s in STATUS_ORDER}
    for r in rows:
        s = r.get("status", "")
        if s in cnt:
            cnt[s] += 1
    return cnt


# ── / → painel ─────────────────────────────────────────────────────────────────

@router.get("/autorizacoes", response_class=HTMLResponse)
async def autorizacoes_root(request: Request, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return user
    if _role(user) not in ALLOWED_ROLES:
        return RedirectResponse(url="/bi", status_code=302)
    redir = check_module_access(user, "autorizacoes")
    if redir:
        return redir
    return RedirectResponse(url="/autorizacoes/painel", status_code=302)


# ── Painel ─────────────────────────────────────────────────────────────────────

@router.get("/autorizacoes/painel", response_class=HTMLResponse)
async def autorizacoes_painel(request: Request, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return user
    if _role(user) not in ALLOWED_ROLES:
        return RedirectResponse(url="/bi", status_code=302)

    sb   = get_supabase_admin()
    cnt  = _contadores(sb)

    from datetime import date
    _MESES = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
              "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    hoje = date.today()
    mes_atual  = hoje.strftime("%Y-%m")
    mes_label  = f"{_MESES[hoje.month - 1]}/{hoje.year}"
    all_rows   = sb.table("autorizacoes").select("status,created_at").execute().data or []
    total_mes  = sum(1 for r in all_rows if (r.get("created_at") or "").startswith(mes_atual))

    recentes = (
        sb.table("autorizacoes")
        .select("id,paciente_nome,procedimento,status,created_at,convenios(nome)")
        .order("created_at", desc=True)
        .limit(10)
        .execute()
        .data or []
    )

    limite_48h = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    atrasadas = (
        sb.table("autorizacoes")
        .select("id,paciente_nome,data_autorizacao,convenios(nome)")
        .eq("status", "autorizado")
        .lt("data_autorizacao", limite_48h)
        .order("data_autorizacao")
        .execute()
        .data or []
    )

    return templates.TemplateResponse("autorizacoes_painel.html", {
        "request":    request,
        "user":       user,
        "active_sub": "painel",
        "contadores": cnt,
        "total_mes":  total_mes,
        "mes_label":  mes_label,
        "recentes":   recentes,
        "atrasadas":  atrasadas,
        **_base_ctx(user),
    })


# ── Lista (Kanban) ─────────────────────────────────────────────────────────────

@router.get("/autorizacoes/lista", response_class=HTMLResponse)
async def autorizacoes_lista(request: Request, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return user
    if _role(user) not in ALLOWED_ROLES:
        return RedirectResponse(url="/bi", status_code=302)

    sb = get_supabase_admin()

    todos = (
        sb.table("autorizacoes")
        .select("id,paciente_nome,procedimento,status,num_guia,created_at,data_autorizacao,convenios(nome)")
        .order("created_at", desc=True)
        .execute()
        .data or []
    )

    by_status: dict[str, list] = {s: [] for s in STATUS_ORDER}
    for a in todos:
        s = a.get("status", "para_solicitar")
        if s in by_status:
            by_status[s].append(a)

    convenios = (
        sb.table("convenios").select("id,nome").eq("ativo", True).order("nome").execute().data or []
    )

    return templates.TemplateResponse("autorizacoes_lista.html", {
        "request":    request,
        "user":       user,
        "active_sub": "lista",
        "by_status":  by_status,
        "convenios":  convenios,
        **_base_ctx(user),
    })


# ── Cadastros ──────────────────────────────────────────────────────────────────

@router.get("/autorizacoes/cadastros", response_class=HTMLResponse)
async def autorizacoes_cadastros(request: Request, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return user
    if _role(user) not in ["admin", "coordenacao"]:
        return RedirectResponse(url="/autorizacoes/painel", status_code=302)

    sb = get_supabase_admin()
    convenios = sb.table("convenios").select("*").eq("ativo", True).order("nome").execute().data or []

    return templates.TemplateResponse("autorizacoes_cadastros.html", {
        "request":   request,
        "user":      user,
        "active_sub": "cadastros",
        "convenios": convenios,
        **_base_ctx(user),
    })


# ── API: list (AJAX) ───────────────────────────────────────────────────────────

@router.get("/autorizacoes/api/lista")
async def autorizacoes_api_lista(
    request: Request,
    status: str = None,
    q: str = None,
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return JSONResponse({"erro": "Não autenticado"}, status_code=401)

    sb = get_supabase_admin()
    query = (
        sb.table("autorizacoes")
        .select("id,paciente_nome,procedimento,status,num_guia,created_at,convenios(nome)")
        .order("created_at", desc=True)
    )
    if status:
        query = query.eq("status", status)
    if q:
        query = query.ilike("paciente_nome", f"%{q}%")

    return JSONResponse(query.execute().data or [])


# ── API: convenios list ────────────────────────────────────────────────────────

@router.get("/autorizacoes/api/convenios")
async def convenios_api_lista(request: Request, user=Depends(require_auth)):
    if isinstance(user, RedirectResponse):
        return JSONResponse({"erro": "Não autenticado"}, status_code=401)
    sb = get_supabase_admin()
    return JSONResponse(
        sb.table("convenios").select("*").eq("ativo", True).order("nome").execute().data or []
    )


# ── Detail HTML fragment (AJAX) ────────────────────────────────────────────────

@router.get("/autorizacoes/detalhe/{auth_id}", response_class=HTMLResponse)
async def autorizacoes_detalhe(
    auth_id: str,
    request: Request,
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user

    sb = get_supabase_admin()
    result = sb.table("autorizacoes").select("*,convenios(id,nome)").eq("id", auth_id).single().execute()
    if not result.data:
        return HTMLResponse(
            "<div class='auth-detail-empty'>"
            "<div class='auth-detail-empty-icon'>🔍</div>"
            "<p>Autorização não encontrada.</p></div>",
            status_code=404,
        )

    log = (
        sb.table("autorizacao_log")
        .select("*")
        .eq("autorizacao_id", auth_id)
        .order("created_at", desc=True)
        .execute()
        .data or []
    )
    convenios = (
        sb.table("convenios").select("id,nome").eq("ativo", True).order("nome").execute().data or []
    )
    arquivos = (
        sb.table("autorizacao_arquivos")
        .select("*")
        .eq("autorizacao_id", auth_id)
        .order("created_at")
        .execute()
        .data or []
    )

    return templates.TemplateResponse("autorizacoes_detalhe.html", {
        "request":       request,
        "user":          user,
        "auth":          result.data,
        "log":           log,
        "convenios":     convenios,
        "arquivos":      arquivos,
        "status_labels": STATUS_LABELS,
        "status_order":  STATUS_ORDER,
        "can_write":     _role(user) in WRITE_ROLES,
        "is_admin":      _role(user) in ["admin", "coordenacao"],
    })


# ── Create ─────────────────────────────────────────────────────────────────────

@router.post("/autorizacoes/novo")
async def autorizacoes_novo(
    request: Request,
    paciente_nome:    str = Form(...),
    convenio_id:      str = Form(None),
    procedimento:     str = Form(None),
    num_guia:         str = Form(None),
    data_solicitacao: str = Form(None),
    observacoes:      str = Form(None),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user
    if _role(user) not in WRITE_ROLES:
        return JSONResponse({"ok": False, "erro": "Sem permissão"}, status_code=403)

    sb  = get_supabase_admin()
    now = datetime.now().isoformat()

    payload = {
        "paciente_nome": paciente_nome.strip(),
        "status":        "para_solicitar",
        "criado_por":    _nome(user),
        "created_at":    now,
        "updated_at":    now,
        "convenio_id":   convenio_id or None,
        "procedimento":  (procedimento or "").strip() or None,
        "num_guia":      (num_guia or "").strip() or None,
        "data_solicitacao": data_solicitacao or None,
        "observacoes":   (observacoes or "").strip() or None,
    }

    res = sb.table("autorizacoes").insert(payload).execute()
    if not res.data:
        return JSONResponse({"ok": False, "erro": "Erro ao salvar"}, status_code=500)

    auth_id = res.data[0]["id"]
    sb.table("autorizacao_log").insert({
        "autorizacao_id": auth_id,
        "status_anterior": None,
        "status_novo":     "para_solicitar",
        "usuario":         _nome(user),
        "created_at":      now,
    }).execute()

    return JSONResponse({"ok": True, "id": auth_id})


# ── Change status ──────────────────────────────────────────────────────────────

@router.post("/autorizacoes/{auth_id}/status")
async def autorizacoes_muda_status(
    auth_id: str,
    request: Request,
    novo_status: str = Form(...),
    obs:         str = Form(None),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user
    if _role(user) not in WRITE_ROLES:
        return JSONResponse({"ok": False, "erro": "Sem permissão"}, status_code=403)
    if novo_status not in STATUS_ORDER:
        return JSONResponse({"ok": False, "erro": "Status inválido"}, status_code=400)

    sb  = get_supabase_admin()
    cur = sb.table("autorizacoes").select("status").eq("id", auth_id).single().execute()
    if not cur.data:
        return JSONResponse({"ok": False, "erro": "Não encontrado"}, status_code=404)

    now    = datetime.now().isoformat()
    update = {"status": novo_status, "updated_at": now}
    if novo_status == "autorizado":
        update["data_autorizacao"] = now

    sb.table("autorizacoes").update(update).eq("id", auth_id).execute()
    sb.table("autorizacao_log").insert({
        "autorizacao_id":  auth_id,
        "status_anterior": cur.data["status"],
        "status_novo":     novo_status,
        "usuario":         _nome(user),
        "obs":             (obs or "").strip() or None,
        "created_at":      now,
    }).execute()

    return JSONResponse({"ok": True, "status": novo_status})


# ── Edit fields ────────────────────────────────────────────────────────────────

@router.post("/autorizacoes/{auth_id}/editar")
async def autorizacoes_editar(
    auth_id: str,
    request: Request,
    paciente_nome:    str = Form(...),
    convenio_id:      str = Form(None),
    procedimento:     str = Form(None),
    num_guia:         str = Form(None),
    data_solicitacao: str = Form(None),
    observacoes:      str = Form(None),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user
    if _role(user) not in WRITE_ROLES:
        return JSONResponse({"ok": False, "erro": "Sem permissão"}, status_code=403)

    sb = get_supabase_admin()
    sb.table("autorizacoes").update({
        "paciente_nome":  paciente_nome.strip(),
        "convenio_id":    convenio_id or None,
        "procedimento":   (procedimento or "").strip() or None,
        "num_guia":       (num_guia or "").strip() or None,
        "data_solicitacao": data_solicitacao or None,
        "observacoes":    (observacoes or "").strip() or None,
        "updated_at":     datetime.now().isoformat(),
    }).eq("id", auth_id).execute()

    return JSONResponse({"ok": True})


# ── Upload arquivo ─────────────────────────────────────────────────────────────

BUCKET        = "autorizacoes"
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

@router.post("/autorizacoes/{auth_id}/arquivos")
async def autorizacoes_upload_arquivo(
    auth_id: str,
    request: Request,
    arquivo: UploadFile = File(...),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return JSONResponse({"ok": False}, status_code=401)
    if _role(user) not in WRITE_ROLES:
        return JSONResponse({"ok": False, "erro": "Sem permissão"}, status_code=403)

    content = await arquivo.read()
    if len(content) > MAX_FILE_SIZE:
        return JSONResponse({"ok": False, "erro": "Arquivo muito grande (máx. 20 MB)"}, status_code=413)

    safe_name  = f"{uuid.uuid4().hex[:8]}_{arquivo.filename}"
    path       = f"{auth_id}/{safe_name}"
    content_type = arquivo.content_type or "application/octet-stream"

    sb = get_supabase_admin()
    try:
        sb.storage.from_(BUCKET).upload(path, content, {"content-type": content_type})
        url = sb.storage.from_(BUCKET).get_public_url(path)
    except Exception as exc:
        return JSONResponse({"ok": False, "erro": f"Erro no storage: {exc}"}, status_code=500)

    row = sb.table("autorizacao_arquivos").insert({
        "autorizacao_id": auth_id,
        "nome":        arquivo.filename,
        "path":        path,
        "url":         url,
        "tipo":        content_type,
        "tamanho":     len(content),
        "criado_por":  _nome(user),
        "created_at":  datetime.now().isoformat(),
    }).execute()

    return JSONResponse({"ok": True, "arquivo": row.data[0] if row.data else {}})


# ── Delete arquivo ──────────────────────────────────────────────────────────────

@router.delete("/autorizacoes/arquivos/{file_id}")
async def autorizacoes_delete_arquivo(
    file_id: str,
    request: Request,
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return JSONResponse({"ok": False}, status_code=401)
    if _role(user) not in WRITE_ROLES:
        return JSONResponse({"ok": False, "erro": "Sem permissão"}, status_code=403)

    sb  = get_supabase_admin()
    row = sb.table("autorizacao_arquivos").select("path").eq("id", file_id).single().execute()
    if not row.data:
        return JSONResponse({"ok": False, "erro": "Não encontrado"}, status_code=404)

    try:
        sb.storage.from_(BUCKET).remove([row.data["path"]])
    except Exception:
        pass

    sb.table("autorizacao_arquivos").delete().eq("id", file_id).execute()
    return JSONResponse({"ok": True})


# ── Delete ─────────────────────────────────────────────────────────────────────

@router.delete("/autorizacoes/{auth_id}")
async def autorizacoes_deletar(
    auth_id: str,
    request: Request,
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return JSONResponse({"ok": False}, status_code=401)
    if _role(user) not in ["admin", "coordenacao"]:
        return JSONResponse({"ok": False, "erro": "Sem permissão"}, status_code=403)

    sb = get_supabase_admin()
    sb.table("autorizacoes").delete().eq("id", auth_id).execute()
    return JSONResponse({"ok": True})


# ── Convênios CRUD ─────────────────────────────────────────────────────────────

@router.post("/autorizacoes/convenios")
async def convenios_criar(
    request: Request,
    nome:   str = Form(...),
    codigo: str = Form(None),
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return user
    if _role(user) not in ["admin", "coordenacao"]:
        return JSONResponse({"ok": False, "erro": "Sem permissão"}, status_code=403)

    sb  = get_supabase_admin()
    res = sb.table("convenios").insert({
        "nome":   nome.strip(),
        "codigo": (codigo or "").strip() or None,
        "ativo":  True,
    }).execute()
    return JSONResponse({"ok": True, "convenio": res.data[0] if res.data else {}})


@router.delete("/autorizacoes/convenios/{conv_id}")
async def convenios_desativar(
    conv_id: str,
    request: Request,
    user=Depends(require_auth),
):
    if isinstance(user, RedirectResponse):
        return JSONResponse({"ok": False}, status_code=401)
    if _role(user) not in ["admin", "coordenacao"]:
        return JSONResponse({"ok": False, "erro": "Sem permissão"}, status_code=403)

    sb = get_supabase_admin()
    sb.table("convenios").update({"ativo": False}).eq("id", conv_id).execute()
    return JSONResponse({"ok": True})
