"""
Integração com a API do iGut Clínicas (https://api.igut.med.br/docs/).
Fornece CPF, data de nascimento, endereço e carteirinha dos pacientes para o B.I.

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
_CACHE_TTL   = 6 * 60 * 60    # pacientes já consultados ficam 6 h em memória
_CONCORRENCIA = 10            # consultas simultâneas ao iGut

_token: dict = {"value": None, "ts": 0.0}
_pac_cache: dict[str, tuple[float, dict | None]] = {}   # nome normalizado → (ts, dados)


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


async def _req(client: httpx.AsyncClient, method: str, path: str, **kw) -> dict:
    """Chamada autenticada; refaz o login uma vez se o token for recusado."""
    for tentativa in range(2):
        token = await _login(client, force=tentativa > 0)
        r = await client.request(method, path, headers={"client_token": _client_token(),
                                                        "Authorization": f"Bearer {token}"}, **kw)
        if r.status_code not in (401, 403):
            r.raise_for_status()
            return r.json()
    r.raise_for_status()
    return {}


def _nascimento_iso(v: str | None) -> str | None:
    """"dd/mm/aaaa" (formato do iGut) → "aaaa-mm-dd"; None se vazio ou inválido."""
    try:
        d, m, a = (int(x) for x in (v or "").strip().split("/"))
        return f"{a:04d}-{m:02d}-{d:02d}" if a > 1900 and 1 <= m <= 12 and 1 <= d <= 31 else None
    except ValueError:
        return None


def _endereco(p: dict) -> str | None:
    """Endereço, bairro, cidade/UF e CEP em uma linha."""
    limpo = lambda v: " ".join(str(v or "").split())
    cidade, uf = limpo(p.get("cidade")), limpo(p.get("estado"))
    partes = [limpo(p.get("endereco")), limpo(p.get("bairro")),
              f"{cidade}/{uf}" if cidade and uf else cidade or uf, limpo(p.get("cep"))]
    return ", ".join(x for x in partes if x) or None


async def _carteirinha(client: httpx.AsyncClient, ids: list, data: str) -> str | None:
    """Número da carteirinha do convênio. Só vem nos agendamentos (Paciente.numerocarteiraconvenio);
    filtrar pelo dia de um atendimento conhecido traz 1 agendamento em vez do histórico todo."""
    for pid in ids:
        body = await _req(client, "POST", "/v2/consultas/buscar",
                          json={"Paciente.id": str(pid), "Agendamento.data": data,
                                "Agendamento.data_fim": data})
        for item in body.get("data") if isinstance(body.get("data"), list) else []:
            num = str((item.get("Paciente") or {}).get("numerocarteiraconvenio") or "").strip()
            if num:
                return num
    return None


async def _buscar_paciente(client: httpx.AsyncClient, nome: str,
                           data: str | None) -> tuple[dict | None, bool]:
    """({cpf, data_nascimento, endereco, carteirinha}, completo) do paciente com nome idêntico.
    Dados None se não achar ou se houver homônimos (CPFs diferentes, ou mais de um cadastro
    sem CPF). data = dia de um atendimento do paciente, usado para achar a carteirinha.
    completo=False quando a carteirinha falhou (o resultado não vai para o cache)."""
    body = await _req(client, "GET", "/v2/pacientes/listar", params={"nome": nome})
    itens = body.get("data") if isinstance(body.get("data"), list) else []
    alvo = _norm(nome)
    iguais = [p for p in ((item.get("Paciente") or item) for item in itens if isinstance(item, dict))
              if _norm(p.get("nome", "")) == alvo]
    com_cpf = [p for p in iguais if (p.get("cpf") or "").strip()]
    if len({p["cpf"].strip() for p in com_cpf}) > 1 or (not com_cpf and len(iguais) != 1):
        return None, True
    p = (com_cpf or iguais)[0]

    # Cadastros duplicados com o mesmo CPF: o agendamento pode estar em qualquer um deles
    carteirinha, completo = None, True
    if data:
        try:
            carteirinha = await _carteirinha(client, [q["id"] for q in (com_cpf or iguais)], data)
        except Exception as e:
            print("IGUT CARTEIRINHA ERROR:", nome, repr(e))
            completo = False
    return {
        "cpf":             (p.get("cpf") or "").strip() or None,
        "data_nascimento": _nascimento_iso(p.get("datadenascimento")),
        "endereco":        _endereco(p),
        "carteirinha":     carteirinha,
    }, completo


async def dados_por_nome(pacientes: dict[str, str | None]) -> dict[str, dict]:
    """Recebe {nome: dia (AAAA-MM-DD) de um atendimento do paciente} e retorna
    {nome: {cpf, data_nascimento, endereco, carteirinha}} dos encontrados no iGut.
    Nunca levanta exceção: em caso de falha da API devolve o que conseguiu."""
    if not configurado():
        return {}

    agora = time.time()
    resultado: dict[str, dict] = {}
    pendentes: list[str] = []
    for nome in (n for n in pacientes if n):
        hit = _pac_cache.get(_norm(nome))
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
                        dados, completo = await _buscar_paciente(client, nome, pacientes[nome])
                    except Exception as e:
                        print("IGUT PACIENTE ERROR:", nome, repr(e))
                        return   # não guarda no cache: tenta de novo na próxima
                if completo:
                    _pac_cache[_norm(nome)] = (time.time(), dados)
                if dados:
                    resultado[nome] = dados

            await asyncio.gather(*(_um(n) for n in pendentes))
    except Exception as e:
        print("IGUT ERROR:", repr(e))
    return resultado
