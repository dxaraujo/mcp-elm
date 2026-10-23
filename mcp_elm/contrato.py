"""Contrato das tools: envelope JSON compacto {ok, dados} | {ok, erro{codigo, detalhe, campo?}}."""
from __future__ import annotations

import functools
import json
import re

import requests

from .config import CredencialError
from .http import AuthError, ElmHttpError, mensagem

CODIGOS = ("config", "credencial", "nao_encontrado", "permissao", "precondicao", "conflito", "entrada")
# atributo citado entre aspas na mensagem do servidor ("O atributo 'Estimativa' é obrigatório")
_ATRIBUTO = re.compile(r"['\"“«]([^'\"”»]{1,80})['\"”»]")
_OBRIGATORIO = re.compile(r"obrigat|required", re.I)


class Erro(Exception):
    def __init__(self, codigo: str, detalhe: str, campo: str | None = None):
        assert codigo in CODIGOS, codigo
        super().__init__(detalhe)
        self.codigo, self.detalhe, self.campo = codigo, mensagem(detalhe) or codigo, campo


def _json(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _http(exc: ElmHttpError) -> Erro:
    msg, status = exc.mensagem or f"HTTP {exc.status_code}", exc.status_code
    if status == 401:
        return Erro("credencial", msg)
    codigo = ("precondicao" if status == 409 or _OBRIGATORIO.search(msg) else "permissao" if status == 403
              else "nao_encontrado" if status in (404, 410) else "conflito" if status == 412 else "entrada")
    found = _ATRIBUTO.search(msg) if codigo in ("precondicao", "permissao") else None
    return Erro(codigo, msg, found.group(1) if found else None)


def erro(exc: Exception) -> Erro | None:
    """Exceção -> Erro do contrato; None se for falha inesperada (bug: sobe como erro do MCP)."""
    if isinstance(exc, Erro):
        return exc
    if isinstance(exc, ElmHttpError):
        return _http(exc)
    if isinstance(exc, (CredencialError, AuthError)):
        return Erro("credencial", str(exc))
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return Erro("credencial", f"servidor inacessível: {exc}")
    if isinstance(exc, LookupError):
        return Erro("nao_encontrado", str(exc))
    if isinstance(exc, ValueError):
        return Erro("entrada", str(exc))
    return None


def resposta_erro(e: Erro) -> str:
    return _json({"ok": False, "erro": {"codigo": e.codigo, "detalhe": e.detalhe,
                                        **({"campo": e.campo} if e.campo else {})}})


def operacao(fn):
    """Tool -> envelope. Retorno str (Markdown) passa direto; o resto vira {"ok":true,"dados":...}."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            if (e := erro(exc)) is None:
                raise
            return resposta_erro(e)
        return result if isinstance(result, str) else _json({"ok": True, "dados": result})
    return wrapper
