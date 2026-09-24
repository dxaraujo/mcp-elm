"""Servidor Jazz falso: um adapter do requests que responde a partir de rotas em memória."""
from pathlib import Path

import pytest
import requests
from requests.adapters import BaseAdapter

from mcp_alm.infra.config import AlmConfig
from mcp_alm.infra.http import AlmSession, set_session

FIXTURES = Path(__file__).parent / "fixtures"
SERVER = "https://alm.test"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeAdapter(BaseAdapter):
    """routes: {(METODO, url_sem_query) ou (METODO, url_completa): handler(request) -> (status, headers, body) ou tupla}."""

    def __init__(self, routes):
        super().__init__()
        self.routes = routes
        self.calls: list[requests.PreparedRequest] = []

    def send(self, request, **kwargs):
        self.calls.append(request)
        route = self.routes.get((request.method, request.url)) or self.routes.get(
            (request.method, request.url.split("?")[0]))
        status, headers, body = (route(request) if callable(route) else route) if route else (404, {}, b"not found")
        resp = requests.Response()
        resp.status_code = status
        resp.headers.update(headers)
        resp._content = body if isinstance(body, bytes) else body.encode()
        resp.url = request.url
        resp.request = request
        return resp

    def close(self):
        pass


def _clear_caches():
    from mcp_alm.infra import oslc
    oslc.clear_cache()
    try:
        from mcp_alm.ibm import common
        common.contributors.cache_clear()
    except ImportError:  # antes da Task 3 o módulo não existe
        pass


@pytest.fixture
def fake():
    """Instala uma AlmSession global com FakeAdapter; devolve o adapter para configurar rotas."""
    adapter = FakeAdapter({})
    session = AlmSession(AlmConfig(server=SERVER, user="joao", password="segredo"))
    session.session.mount("https://", adapter)
    set_session(session)
    _clear_caches()
    yield adapter
    set_session(None)
    _clear_caches()


def ok(name: str, **headers):
    return 200, {"Content-Type": "application/rdf+xml", **headers}, fixture(name)


def xml_ok(body: str):
    return 200, {"Content-Type": "application/xml"}, body.encode()
