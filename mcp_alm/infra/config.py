"""Carrega as credenciais do ALM a partir de alm.properties."""
from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = ("server", "user", "password")


@dataclass(frozen=True)
class AlmConfig:
    server: str
    user: str
    password: str = field(repr=False)


def default_path() -> Path:
    if env := os.environ.get("MCP_ALM_CONFIG"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "mcp-alm" / "alm.properties"
    return Path.home() / ".config" / "mcp-alm" / "alm.properties"


def load_config(path: Path | None = None) -> AlmConfig:
    path = path or default_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Arquivo de credenciais não encontrado: {path}\n"
            "Crie-o com:\n[DEFAULT]\nserver = https://alm.SEU-SERVIDOR\nuser = SEU_USUARIO\npassword = SUA_SENHA"
        )
    # interpolation=None: senhas podem conter '%'
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    section = parser.defaults()
    missing = [k for k in REQUIRED if not section.get(k, "").strip()]
    if missing:
        raise ValueError(f"Chave(s) ausente(s) em {path} [DEFAULT]: {', '.join(missing)}")
    return AlmConfig(
        server=section["server"].strip().rstrip("/"),
        user=section["user"].strip(),
        password=section["password"],
    )
