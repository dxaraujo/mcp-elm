"""Servidor Jazz falso: um adapter do requests que responde a partir de rotas em memória."""
import json
import re
import shutil
import zlib
from pathlib import Path

import numpy as np
import pytest
import requests
from requests.adapters import BaseAdapter

from mcp_elm import ewm, local, oslc
from mcp_elm.config import ElmConfig
from mcp_elm.http import ElmSession, set_session

FIXTURES = Path(__file__).parent / "fixtures"
SERVER = "https://elm.test"
NS = " ".join(f'xmlns:{p}="{u}"' for p, u in {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "dcterms": "http://purl.org/dc/terms/", "oslc": "http://open-services.net/ns/core#",
    "oslc_cm": "http://open-services.net/ns/cm#", "oslc_cmx": "http://open-services.net/ns/cm-x#",
    "oslc_rm": "http://open-services.net/ns/rm#", "oslc_qm": "http://open-services.net/ns/qm#",
    "rtc_cm": "http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/", "rtc_ext": "http://jazz.net/xmlns/prod/jazz/rtc/ext/1.0/",
    "jazz_rm": "http://jazz.net/ns/rm#", "nav": "http://jazz.net/ns/rm/navigation#",
    "oslc_config": "http://open-services.net/ns/config#", "process": "http://jazz.net/ns/process#"}.items())


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def rdf(inner: str, **headers):
    return 200, {"Content-Type": "application/rdf+xml", **headers}, f'<?xml version="1.0"?><rdf:RDF {NS}>{inner}</rdf:RDF>'


def xml(body: str):
    return 200, {"Content-Type": "application/xml"}, body


def js(obj, status: int = 200):
    return status, {"Content-Type": "application/json"}, json.dumps(obj)


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

    def bodies(self, method: str, fragment: str) -> list[str]:
        return [c.body.decode() if isinstance(c.body, bytes) else (c.body or "") for c in self.calls
                if c.method == method and fragment in c.url]


def fake_embed(texts):
    """Saco de palavras com hash (sem baixar modelo): textos com palavras em comum ficam próximos."""
    v = np.zeros((len(texts), 64), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in re.findall(r"\w+", t.lower()):
            v[i, zlib.crc32(w.encode()) % 64] += 1.0
    v[:, 0] += 1e-3
    return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.fixture(autouse=True)
def _sem_modelo(monkeypatch):
    monkeypatch.setattr(local, "embed", fake_embed)
    monkeypatch.setattr(local, "SINCRONO", True)  # indexa na própria chamada; o teste do segundo plano desliga


def _clear_caches():
    oslc.clear_cache()
    ewm._logins.cache_clear()
    ewm._workflow.cache_clear()


@pytest.fixture
def fake():
    """Instala uma ElmSession global com FakeAdapter; devolve o adapter para configurar rotas."""
    adapter = FakeAdapter({})
    session = ElmSession(ElmConfig(server=SERVER, user="joao", password="segredo"))
    session.session.mount("https://", adapter)
    set_session(session)
    _clear_caches()
    yield adapter
    set_session(None)
    _clear_caches()


@pytest.fixture
def projeto(tmp_path):
    """Workspace com um pa fictício (pa_alfa.json)."""
    pasta = tmp_path / ".kiro" / "config" / "sdd"
    pasta.mkdir(parents=True)
    shutil.copy(FIXTURES / "pa_alfa.json", pasta / "pa_alfa.json")
    return str(tmp_path)


@pytest.fixture
def cfg(projeto):
    from mcp_elm import projeto as prj
    return prj.carregar(projeto)


def chamar(_tool: str, **args):
    """Chama a tool pelo MCP (como o cliente): um TextContent, sem structuredContent; JSON vira dict."""
    import asyncio

    from mcp_elm.server import mcp
    res = asyncio.run(mcp.call_tool(_tool, args))
    assert len(res.content) == 1 and res.structured_content is None
    text = res.content[0].text
    return json.loads(text) if text.startswith("{") else text
