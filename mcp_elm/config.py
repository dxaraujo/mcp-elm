"""Carrega credenciais, provider e certificado do ELM das variáveis de ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = ("ELM_SERVER", "ELM_USER", "ELM_PASSWORD")


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


def ca_bundle() -> Path | None:
    """Cadeia PEM extra apontada por ELM_CA_BUNDLE, somada às CAs da máquina."""
    valor = os.environ.get("ELM_CA_BUNDLE", "").strip()
    if not valor:
        return None
    path = Path(valor).expanduser()
    if not path.is_file():
        raise CredencialError(f"ELM_CA_BUNDLE aponta para um arquivo que não existe: {path}")
    return path


def load_config() -> ElmConfig:
    missing = [k for k in REQUIRED if not os.environ.get(k, "").strip()]
    if missing:
        raise CredencialError(f"variável(is) de ambiente ausente(s): {', '.join(missing)}")
    ca_bundle()  # arquivo errado aparece já na carga

    def opcional(chave: str) -> str | None:
        return (os.environ.get(chave, "") or "").strip() or None

    return ElmConfig(
        server=os.environ["ELM_SERVER"].strip().rstrip("/"),
        user=os.environ["ELM_USER"].strip(),
        password=os.environ["ELM_PASSWORD"],
        provider_url=opcional("ELM_PROVIDER_URL"),
        provider_api_key=opcional("ELM_PROVIDER_API_KEY") or "",
        provider_embed_model=opcional("ELM_PROVIDER_EMBED_MODEL"),
        provider_rerank_model=opcional("ELM_PROVIDER_RERANK_MODEL"),
    )
