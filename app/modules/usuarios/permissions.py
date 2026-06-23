"""
Armazena restrições de módulos por usuário em disco local.
Não depende de migration SQL nem de user_metadata do Supabase.
Formato: { "user_uuid": "autorizacoes", "outro_uuid": "bi,comunicados" }
"""
from __future__ import annotations
import json
from pathlib import Path

_FILE = Path(__file__).parent.parent.parent / "data" / "user_permissions.json"


def _load() -> dict:
    if not _FILE.exists():
        return {}
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_permission(user_id: str) -> str | None:
    return _load().get(user_id) or None


def set_permission(user_id: str, modulos: str | None) -> None:
    data = _load()
    if modulos:
        data[user_id] = modulos
    else:
        data.pop(user_id, None)
    _save(data)


def delete_permission(user_id: str) -> None:
    set_permission(user_id, None)
