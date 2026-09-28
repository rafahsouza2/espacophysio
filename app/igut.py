"""
Integração com a API do iGut Clínicas (https://api.igut.med.br/docs/).

Todas as chamadas levam o header client_token (nome da clínica em Base64); as autenticadas
levam também o Bearer obtido em POST /v2/usuarios/login.
"""
from __future__ import annotations

import asyncio
import base64
import time
import unicodedata

import httpx

from app.config import settings

_TOKEN_TTL   = 30 * 60        # renova o login a cada 30 min
_CACHE_TTL   = 6 * 60 * 60    # CPFs já consultados ficam 6 h em memória
_CONCORRENCIA = 10            # consultas simultâneas ao iGut

_token: dict = {"value": None, "ts": 0.0}
_cpf_cache: dict[str, tuple[float, str | None]] = {}   # nome normalizado → (ts, cpf)


def configurado() -> bool:
    return bool(settings.igut_api_user and settings.igut_api_password)


def _client_token() -> str:
    return base64.b64encode(settings.igut_clinica.encode()).decode()


def _norm(nome: str) -> str:
    """Maiúsculas, sem acentos e com espaços simples — para comparar nomes."""
    s = unicodedata.normalize("NFKD", nome or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.upper().split())


async def _login(client: httpx.AsyncClient, force: bool = False) -> str:
    if not force and _token["value"] and time.time() - _token["ts"] < _TOKEN_TTL:
        return _token["value"]
    r = await client.post("/v2/usuarios/login",
                          json={"username": settings.igut_api_user,
                                "password": settings.igut_api_password},
                          headers={"client_token": _client_token()})
    body = r.json()
    token = (body.get("data") or {}).get("token") if body.get("status") == "success" else None
    if not token:
        raise RuntimeError(f"login iGut falhou: {body.get('message') or r.status_code}")
    _token.update(value=token, ts=time.time())
    return token


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    """GET autenticado; refaz o login uma vez se o token for recusado."""
    for tentativa in range(2):
        token = await _login(client, force=tentativa > 0)
        r = await client.get(path, params=params, headers={"client_token": _client_token(),
                                                           "Authorization": f"Bearer {token}"})
        if r.status_code not in (401, 403):
            r.raise_for_status()
            return r.json()
    r.raise_for_status()
    return {}


async def _buscar_cpf(client: httpx.AsyncClient, nome: str) -> str | None:
    """CPF do paciente com nome idêntico. None se não achar ou se houver homônimos com CPFs diferentes."""
    body = await _get(client, "/v2/pacientes/listar", {"nome": nome})
    data = body.get("data") if isinstance(body.get("data"), list) else []
    alvo = _norm(nome)
    cpfs = {(p.get("cpf") or "").strip()
            for p in ((item.get("Paciente") or item) for item in data if isinstance(item, dict))
            if _norm(p.get("nome", "")) == alvo}
    cpfs.discard("")
    return cpfs.pop() if len(cpfs) == 1 else None


async def cpfs_por_nome(nomes: list[str]) -> dict[str, str]:
    """Retorna {nome: cpf} dos pacientes encontrados no iGut. Nunca levanta exceção:
    em caso de falha da API devolve o que conseguiu (a página segue sem o CPF)."""
    if not configurado():
        return {}

    agora = time.time()
    resultado: dict[str, str] = {}
    pendentes: list[str] = []
    for nome in dict.fromkeys(n for n in nomes if n):
        hit = _cpf_cache.get(_norm(nome))
        if hit and agora - hit[0] < _CACHE_TTL:
            if hit[1]:
                resultado[nome] = hit[1]
        else:
            pendentes.append(nome)
    if not pendentes:
        return resultado

    try:
        async with httpx.AsyncClient(base_url=settings.igut_api_url, timeout=20) as client:
            await _login(client)
            sem = asyncio.Semaphore(_CONCORRENCIA)

            async def _um(nome: str) -> None:
                async with sem:
                    try:
                        cpf = await _buscar_cpf(client, nome)
                    except Exception as e:
                        print("IGUT CPF ERROR:", nome, repr(e))
                        return   # não guarda no cache: tenta de novo na próxima
                _cpf_cache[_norm(nome)] = (time.time(), cpf)
                if cpf:
                    resultado[nome] = cpf

            await asyncio.gather(*(_um(n) for n in pendentes))
    except Exception as e:
        print("IGUT ERROR:", repr(e))
    return resultado
