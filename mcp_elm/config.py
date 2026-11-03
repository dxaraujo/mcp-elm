"""Carrega as credenciais do ELM a partir de elm.properties."""
from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = ("server", "user", "password")


class CredencialError(RuntimeError):
    pass


@dataclass(frozen=True)
class ElmConfig:
    server: str
    user: str
    password: str = field(repr=False)
    # busca semântica do bundle local (API OpenAI-compatível; mesma base/key para embedding e rerank)
    provider_url: str | None = None
    provider_api_key: str = field(repr=False, default="")
    provider_embed_model: str | None = None
    provider_rerank_model: str | None = None


def default_path() -> Path:
    if env := os.environ.get("MCP_ELM_CONFIG"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "mcp-elm" / "elm.properties"
    return Path.home() / ".config" / "mcp-elm" / "elm.properties"


def load_config(path: Path | None = None) -> ElmConfig:
    path = path or default_path()
    if not path.is_file():
        raise CredencialError(f"arquivo de credenciais não encontrado: {path} ([DEFAULT] server, user, password)")
    # interpolation=None: senhas podem conter '%'
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    section = parser.defaults()
    missing = [k for k in REQUIRED if not section.get(k, "").strip()]
    if missing:
        raise CredencialError(f"chave(s) ausente(s) em {path} [DEFAULT]: {', '.join(missing)}")

    def opcional(chave: str) -> str | None:
        return (section.get(chave, "") or "").strip() or None

    return ElmConfig(
        server=section["server"].strip().rstrip("/"),
        user=section["user"].strip(),
        password=section["password"],
        provider_url=opcional("provider_url"),
        provider_api_key=opcional("provider_api_key") or "",
        provider_embed_model=opcional("provider_embed_model"),
        provider_rerank_model=opcional("provider_rerank_model"),
    )
