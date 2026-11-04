"""Embedder/rerank: provider OpenAI-compatível como única fonte (sem fallback local): assinatura,
contrato, retry e erros."""
import json

import pytest
import requests

from mcp_elm import embedder
from mcp_elm.contrato import Erro

BASE = "[DEFAULT]\nserver = https://elm.test\nuser = u\npassword = p\n"


def _props(tmp_path, **extra):
    p = tmp_path / "elm.properties"
    p.write_text(BASE + "".join(f"{k} = {v}\n" for k, v in extra.items()), encoding="utf-8")
    return p


def _resp(status, body):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(body).encode()
    r.headers["Content-Type"] = "application/json"
    return r


def _api(monkeypatch, tmp_path, **extra):
    monkeypatch.setenv("MCP_ELM_CONFIG", str(_props(tmp_path, **extra)))
    embedder.clear_cache()


@pytest.fixture(autouse=True)
def _isolado(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ELM_CONFIG", str(tmp_path / "nao-existe.properties"))
    embedder.clear_cache()
    yield
    embedder.clear_cache()


def test_sem_config_erro_claro():
    """Sem provider_url/provider_api_key: assinatura e embed apontam o elm.properties; rerank inativo."""
    with pytest.raises(Erro) as e:
        embedder.assinatura()
    assert e.value.codigo == "config" and e.value.campo == "provider_url"
    with pytest.raises(Erro) as e:
        embedder.embed(["a"])
    assert e.value.campo == "provider_url"
    assert embedder.rerank_modelo() is None and embedder.tem_rerank() is False
    assert embedder.rerank_assinatura() is None
    assert embedder.rerank("q", ["a", "b"]) is None


def test_assinatura_reflete_base_e_modelo(monkeypatch, tmp_path):
    _api(monkeypatch, tmp_path, provider_url="https://api.test/v1/", provider_api_key="k")
    assert embedder.assinatura() == "api:text-embedding-3-small@https://api.test/v1"  # trailing / some
    _api(monkeypatch, tmp_path, provider_url="https://api.test/v1", provider_api_key="k", provider_embed_model="bge-m3")
    assert embedder.assinatura() == "api:bge-m3@https://api.test/v1"
    _api(monkeypatch, tmp_path, provider_url="https://api.test/v1")  # sem key: erro claro, sem modo local
    with pytest.raises(Erro) as e:
        embedder.assinatura()
    assert e.value.campo == "provider_url"


def test_embed_por_api_preserva_ordem_e_normaliza(monkeypatch, tmp_path):
    _api(monkeypatch, tmp_path, provider_url="https://api.test/v1", provider_api_key="k")
    chamadas = []

    def _post(url, key, payload):
        chamadas.append((url, key, payload))
        return {"data": [{"embedding": [0.0, 2.0], "index": 1}, {"embedding": [3.0, 0.0], "index": 0}]}

    monkeypatch.setattr(embedder, "_post", _post)
    v = embedder.embed(["a", "b"])
    assert chamadas == [("https://api.test/v1/embeddings", "k",
                         {"model": "text-embedding-3-small", "input": ["a", "b"]})]
    assert v[0] == pytest.approx([1.0, 0.0]) and v[1] == pytest.approx([0.0, 1.0])  # pela ordem de `index`, norma 1


def test_rerank_chama_a_api_na_ordem_de_docs(monkeypatch, tmp_path):
    _api(monkeypatch, tmp_path, provider_url="https://api.test/v1", provider_api_key="k", provider_rerank_model="rerank-1")
    assert embedder.rerank_modelo() == "rerank-1" and embedder.tem_rerank()
    assert embedder.rerank_assinatura() == "api:rerank-1@https://api.test/v1"
    chamadas = []

    def _post(url, key, payload, campo_modelo="provider_embed_model"):
        chamadas.append((url, payload))
        return {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]}

    monkeypatch.setattr(embedder, "_post", _post)
    assert embedder.rerank("q", ["a", "b"]) == [0.1, 0.9]  # na ordem de `docs`, não da resposta
    assert chamadas[0] == ("https://api.test/v1/rerank",
                           {"model": "rerank-1", "query": "q", "documents": ["a", "b"], "top_n": 2})
    assert embedder.rerank("q", ["a"]) is None  # um documento: nada a reordenar
    _api(monkeypatch, tmp_path, provider_url="https://api.test/v1", provider_api_key="k")  # sem provider_rerank_model
    assert embedder.rerank("q", ["a", "b"]) is None and embedder.rerank_assinatura() is None


def test_rerank_exige_o_provider(monkeypatch, tmp_path):
    """provider_rerank_model sem provider_url/provider_api_key: inativo (não existe rerank local)."""
    _api(monkeypatch, tmp_path, provider_rerank_model="rerank-1")
    assert embedder.rerank_modelo() is None and embedder.tem_rerank() is False
    assert embedder.rerank_assinatura() is None and embedder.rerank("q", ["a", "b"]) is None


def test_repete_rate_limit_e_5xx(monkeypatch):
    posts = []

    def _post(url, headers=None, json=None, timeout=None):
        posts.append(url)
        return _resp(429, {"error": "rate limit"}) if len(posts) == 1 else \
            _resp(200, {"data": [{"embedding": [1.0], "index": 0}]})

    monkeypatch.setattr(embedder.requests, "post", _post)
    monkeypatch.setattr(embedder.time, "sleep", lambda s: None)
    data = embedder._post("https://api.test/v1/embeddings", "k", {})
    assert posts == ["https://api.test/v1/embeddings"] * 2 and data["data"][0]["embedding"] == [1.0]


def test_timeout_de_rede_recupera_na_segunda_tentativa(monkeypatch):
    posts = []

    def _post(url, headers=None, json=None, timeout=None):
        posts.append(url)
        if len(posts) == 1:
            raise requests.exceptions.ConnectionError("connection reset")
        return _resp(200, {"data": [{"embedding": [1.0], "index": 0}]})

    monkeypatch.setattr(embedder.requests, "post", _post)
    monkeypatch.setattr(embedder.time, "sleep", lambda s: None)
    data = embedder._post("https://api.test/v1/embeddings", "k", {})
    assert len(posts) == 2 and data["data"][0]["embedding"] == [1.0]


def test_conexao_resetada_na_leitura_recupera_na_segunda_tentativa(monkeypatch):
    """A gateway segura a conexão por minutos e corta no meio do corpo (ChunkedEncodingError, que não é
    ConnectionError): mesma falha transitória, mesma recuperação."""
    posts = []

    def _post(url, headers=None, json=None, timeout=None):
        posts.append(url)
        if len(posts) == 1:
            raise requests.exceptions.ChunkedEncodingError(
                "Connection broken: ConnectionResetError(54, 'Connection reset by peer')")
        return _resp(200, {"data": [{"embedding": [1.0], "index": 0}]})

    monkeypatch.setattr(embedder.requests, "post", _post)
    monkeypatch.setattr(embedder.time, "sleep", lambda s: None)
    data = embedder._post("https://api.test/v1/embeddings", "k", {})
    assert len(posts) == 2 and data["data"][0]["embedding"] == [1.0]


def test_falhas_de_rede_sem_recuperar_viram_indisponivel(monkeypatch):
    posts = []

    def _post(url, headers=None, json=None, timeout=None):
        posts.append(url)
        raise requests.exceptions.ReadTimeout("read timed out (read timeout=60)")

    monkeypatch.setattr(embedder.requests, "post", _post)
    monkeypatch.setattr(embedder.time, "sleep", lambda s: None)
    with pytest.raises(Erro) as e:
        embedder._post("https://api.test/v1/embeddings", "k", {})
    assert len(posts) == 3 and e.value.codigo == "indisponivel" and "ReadTimeout" in e.value.detalhe


@pytest.mark.parametrize("status,codigo,campo", [(401, "credencial", "provider_api_key"),
                                                  (404, "config", "provider_embed_model"),
                                                  (400, "entrada", None),
                                                  (503, "indisponivel", None)])
def test_erros_da_api_viram_contrato(monkeypatch, status, codigo, campo):
    monkeypatch.setattr(embedder.requests, "post",
                        lambda *a, **k: _resp(status, {"error": {"message": "falhou"}}))
    monkeypatch.setattr(embedder.time, "sleep", lambda s: None)
    with pytest.raises(Erro) as e:
        embedder._post("https://api.test/v1/embeddings", "k", {})
    assert e.value.codigo == codigo and e.value.campo == campo and "falhou" in e.value.detalhe
