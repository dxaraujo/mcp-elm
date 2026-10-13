"""Sessão HTTP compartilhada: headers OSLC, re-login, contexto de configuração e leitura de XML simples."""
from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import requests

from . import auth
from .config import AlmConfig, load_config

TIMEOUT = 60
RDF_XML = "application/rdf+xml"


class AlmHttpError(RuntimeError):
    def __init__(self, resp: requests.Response):
        body = resp.text[:500].strip()
        super().__init__(f"HTTP {resp.status_code} em {resp.request.method} {resp.url}: {body}")
        self.status_code = resp.status_code


class AlmSession:
    def __init__(self, cfg: AlmConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"OSLC-Core-Version": "2.0", "Accept": RDF_XML})
        self._lock = threading.Lock()

    def url(self, path: str) -> str:
        """Resolve caminho relativo ('/ccm/rootservices') contra o servidor."""
        # startswith (e não "://" in path): caminhos relativos podem levar URLs na query string
        return path if path.startswith(("http://", "https://")) else urljoin(self.cfg.server + "/", path.lstrip("/"))

    def request(
        self,
        method: str,
        path: str,
        *,
        configuration: str | None = None,
        headers: dict | None = None,
        params: dict | None = None,
        data: bytes | str | None = None,
        ok: tuple[int, ...] = (200, 201, 204),
    ) -> requests.Response:
        url = self.url(path)
        headers = dict(headers or {})
        params = dict(params or {})
        if configuration:  # stream, baseline, change set ou global configuration
            headers["Configuration-Context"] = configuration
            params.setdefault("oslc_config.context", configuration)

        def send() -> requests.Response:
            return self.session.request(
                method, url, headers=headers, params=params, data=data,
                timeout=TIMEOUT, allow_redirects=method == "GET",
            )

        resp = send()
        if auth.needs_auth(resp):
            with self._lock:
                auth.login(self.session, url, self.cfg, resp)
            resp = send()
            if auth.needs_auth(resp):
                raise auth.AuthError(f"Não autenticado após login em {auth.context_root(url)}.")
        if resp.status_code not in ok:
            raise AlmHttpError(resp)
        return resp


_instance: AlmSession | None = None
_instance_lock = threading.Lock()


def get_session() -> AlmSession:
    """Sessão única por processo; carrega o alm.properties na primeira chamada."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = AlmSession(load_config())
        return _instance


def set_session(session: AlmSession | None) -> None:
    """Substitui a sessão global (testes)."""
    global _instance
    _instance = session


def get_xml(path: str, params: dict | None = None, *, oslc_v1: bool = False) -> ET.Element:
    """GET com Accept: application/xml (API de processo, Reportable REST).
    `oslc_v1=True` omite OSLC-Core-Version (formato OSLC CM 1.0)."""
    headers = {"Accept": "application/xml"}
    if oslc_v1:
        headers["OSLC-Core-Version"] = None  # None remove o header padrão da sessão
    content = get_session().request("GET", path, params=params, headers=headers).content
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"Resposta não é XML de {path}: {exc}") from exc


def reportable(path: str, fields: str, record: str) -> list[ET.Element]:
    """Todos os registros `record` de uma consulta Reportable REST (/ccm/rpt/repository/...), seguindo as páginas."""
    url, params, out = path, {"fields": fields, "size": "100"}, []
    while url:  # "next" vem até na última página: para quando a página volta vazia
        root = get_xml(url, params)
        records = root.findall(record)
        out += records
        url, params = (root.get("href") if records and root.get("rel") == "next" else None), None
    return out
