"""Autenticação Jazz: form (j_security_check) com fallback para Basic."""
from __future__ import annotations

from urllib.parse import urlsplit

import requests

from .config import AlmConfig

AUTH_HEADER = "X-com-ibm-team-repository-web-auth-msg"


class AuthError(RuntimeError):
    pass


def context_root(url: str) -> str:
    """https://host/ccm/oslc/... -> https://host/ccm"""
    parts = urlsplit(url)
    first = parts.path.strip("/").split("/", 1)[0]
    return f"{parts.scheme}://{parts.netloc}/{first}"


def needs_auth(resp: requests.Response) -> bool:
    if resp.headers.get(AUTH_HEADER) in ("authrequired", "authfailed"):
        return True
    if resp.status_code == 401:
        return True
    # alguns servidores redirecionam para a página de login em vez de responder 401
    return resp.status_code in (301, 302, 303) and "auth" in resp.headers.get("Location", "").lower()


def login(session: requests.Session, url: str, cfg: AlmConfig, failed: requests.Response) -> None:
    """Autentica a sessão para o context root de `url`. Lança AuthError se falhar."""
    if failed.status_code == 401 and "basic" in failed.headers.get("WWW-Authenticate", "").lower():
        session.auth = (cfg.user, cfg.password)
        return

    root = context_root(url)
    # garante o JSESSIONID do context root antes do POST
    session.get(f"{root}/authenticated/identity", allow_redirects=True)
    resp = session.post(
        f"{root}/j_security_check",
        data={"j_username": cfg.user, "j_password": cfg.password},
        allow_redirects=True,
    )
    if resp.headers.get(AUTH_HEADER) == "authfailed" or resp.status_code in (401, 403):
        raise AuthError(f"Falha de login em {root} para o usuário '{cfg.user}' (HTTP {resp.status_code}).")
