"""Sessão HTTP única: headers OSLC, login Jazz (form com fallback Basic), contexto de configuração e XML simples."""
from __future__ import annotations

import json
import re
import threading
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import urljoin, urlsplit

import requests

from .config import ElmConfig, load_config

TIMEOUT = 60
RDF_XML = "application/rdf+xml"
AUTH_HEADER = "X-com-ibm-team-repository-web-auth-msg"
# a mensagem útil de um erro OSLC/Jazz (oslc:message, errorMessage, statusMessage)
_MSG = re.compile(r"<(?:[\w-]+:)?(?:message|errorMessage|statusMessage)\b[^>]*>(.*?)</", re.S | re.I)
_NOISE = re.compile(r"<(script|style|head)\b.*?</\1>", re.S | re.I)


class AuthError(RuntimeError):
    pass


def mensagem(body: str) -> str:
    """Texto curto de uma resposta de erro, sem HTML/XML do servidor."""
    try:
        data = json.loads(body)
        if isinstance(data, dict) and (m := data.get("errorMessage") or data.get("message")):
            body = str(m)
    except ValueError:
        if m := _MSG.search(body):
            body = m.group(1)
    text = unescape(re.sub(r"<[^>]+>", " ", _NOISE.sub(" ", body)))
    return " ".join(text.split())[:200]


class ElmHttpError(RuntimeError):
    def __init__(self, resp: requests.Response):
        self.status_code = resp.status_code
        self.mensagem = mensagem(resp.text)
        super().__init__(f"HTTP {resp.status_code}: {self.mensagem}")


def context_root(url: str) -> str:
    """https://host/ccm/oslc/... -> https://host/ccm"""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/{parts.path.strip('/').split('/', 1)[0]}"


def _needs_auth(resp: requests.Response) -> bool:
    if resp.headers.get(AUTH_HEADER) in ("authrequired", "authfailed") or resp.status_code == 401:
        return True
    # alguns servidores redirecionam para a página de login em vez de responder 401
    return resp.status_code in (301, 302, 303) and "auth" in resp.headers.get("Location", "").lower()


def _login(session: requests.Session, url: str, cfg: ElmConfig, failed: requests.Response) -> None:
    if failed.status_code == 401 and "basic" in failed.headers.get("WWW-Authenticate", "").lower():
        session.auth = (cfg.user, cfg.password)
        return
    root = context_root(url)
    session.get(f"{root}/authenticated/identity", allow_redirects=True)  # JSESSIONID antes do POST
    resp = session.post(f"{root}/j_security_check", data={"j_username": cfg.user, "j_password": cfg.password},
                        allow_redirects=True)
    if resp.headers.get(AUTH_HEADER) == "authfailed" or resp.status_code in (401, 403):
        raise AuthError(f"falha de login em {root} para '{cfg.user}' (HTTP {resp.status_code})")


class ElmSession:
    def __init__(self, cfg: ElmConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"OSLC-Core-Version": "2.0", "Accept": RDF_XML})
        self._lock = threading.Lock()

    def url(self, path: str) -> str:
        # startswith (e não "://" in path): caminhos relativos podem levar URLs na query string
        return path if path.startswith(("http://", "https://")) else urljoin(self.cfg.server + "/", path.lstrip("/"))

    def request(self, method: str, path: str, *, configuration: str | None = None, headers: dict | None = None,
                params: dict | None = None, data=None, ok: tuple[int, ...] = (200, 201, 204)) -> requests.Response:
        url, headers, params = self.url(path), dict(headers or {}), dict(params or {})
        if configuration:  # stream, baseline ou global configuration
            headers["Configuration-Context"] = configuration
            params.setdefault("oslc_config.context", configuration)

        def send() -> requests.Response:
            return self.session.request(method, url, headers=headers, params=params, data=data, timeout=TIMEOUT,
                                        allow_redirects=method == "GET")

        resp = send()
        if _needs_auth(resp):
            with self._lock:
                _login(self.session, url, self.cfg, resp)
            resp = send()
            if _needs_auth(resp):
                raise AuthError(f"não autenticado após login em {context_root(url)}")
        if resp.status_code not in ok:
            raise ElmHttpError(resp)
        return resp

    def ui_headers(self) -> dict:
        """Headers dos serviços internos da UI web (anti-CSRF com o JSESSIONID do /ccm)."""
        jsession = next((c.value for c in self.session.cookies if c.name == "JSESSIONID" and c.path.startswith("/ccm")),
                        "")
        return {"Accept": "text/json", "X-Jazz-CSRF-Prevent": jsession}


_instance: ElmSession | None = None
_instance_lock = threading.Lock()


def get_session() -> ElmSession:
    """Sessão única por processo; lê o elm.properties na primeira chamada."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = ElmSession(load_config())
        return _instance


def set_session(session: ElmSession | None) -> None:
    global _instance
    _instance = session


def get_xml(path: str, params: dict | None = None, *, oslc_v1: bool = False) -> ET.Element:
    """GET com Accept: application/xml (API de processo, Reportable REST). `oslc_v1` omite OSLC-Core-Version."""
    headers = {"Accept": "application/xml"}
    if oslc_v1:
        headers["OSLC-Core-Version"] = None  # None remove o header padrão da sessão
    content = get_session().request("GET", path, params=params, headers=headers).content
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"resposta não é XML: {path}") from exc


def reportable(path: str, fields: str, record: str) -> list[ET.Element]:
    """Registros `record` de uma consulta Reportable REST, seguindo as páginas."""
    url, params, out = path, {"fields": fields, "size": "100"}, []
    while url:  # "next" vem até na última página: para quando a página volta vazia
        root = get_xml(url, params)
        records = root.findall(record)
        out += records
        url, params = (root.get("href") if records and root.get("rel") == "next" else None), None
    return out
