"""Embedding e rerank do índice local, via API OpenAI-compatível do elm.properties.

`provider_url` + `provider_api_key` são obrigatórios para a busca semântica, e o mesmo par serve
para os dois serviços (só o modelo muda): `provider_embed_model` (padrão text-embedding-3-small)
é o embedding e `provider_rerank_model` opcional liga o rerank na busca. Sem as duas chaves,
indexar e buscar falham com erro de config apontando o elm.properties — não existe fallback local.

A `assinatura()` identifica o espaço de vetores do índice (base + modelo): se ela mudar, o índice
é limpo e refeito, porque vetores de dimensão/modelo diferentes não podem coexistir na matriz —
e a busca em índice com assinatura diferente da atual falha com erro claro (o reindex resolve).
O rerank não entra na assinatura (não reindexa): é refinamento na busca, e por isso funciona em
qualquer índice (reordena os candidatos a texto, não os vetores).
"""
from __future__ import annotations

import functools
import time

import numpy as np
import requests

from .config import CredencialError, load_config
from .contrato import Erro
from .http import mensagem

MODELO_PADRAO = "text-embedding-3-small"
TEMPO = 60
# status que valem nova tentativa (rate limit / instabilidade do gateway)
_REPETIR = (408, 429, 500, 502, 503, 504)


def clear_cache() -> None:
    """Zera a leitura do elm.properties (testes trocam MCP_ELM_CONFIG)."""
    _cfg.cache_clear()
    _api.cache_clear()
    _rerank_fn.cache_clear()


@functools.lru_cache
def _cfg():
    """Credenciais do usuário; sem o arquivo (ou inválido): None."""
    try:
        return load_config()
    except CredencialError:
        return None


@functools.lru_cache
def _api():
    """(base, key) da API OpenAI-compatível; None sem provider_url/provider_api_key."""
    c = _cfg()
    if c and c.provider_url and c.provider_api_key:
        return c.provider_url.rstrip("/"), c.provider_api_key
    return None


def _api_ou_erro() -> tuple[str, str]:
    api = _api()
    if api is None:
        raise Erro("config", "busca semântica requer provider_url e provider_api_key no elm.properties",
                   "provider_url")
    return api


def _modelo_embed() -> str:
    return _cfg().provider_embed_model or MODELO_PADRAO


def assinatura() -> str:
    """Identificador estável do espaço de vetores (vai na meta do índice); erro claro sem provider."""
    base, _ = _api_ou_erro()
    return f"api:{_modelo_embed()}@{base}"


def rerank_modelo() -> str | None:
    """Modelo do rerank ativo (opcional, por API): `provider_rerank_model` + o provider configurado."""
    c = _cfg()
    m = c.provider_rerank_model if c else None
    return m if (m and _api()) else None


def tem_rerank() -> bool:
    return rerank_modelo() is not None


def rerank_assinatura() -> str | None:
    """Identificador do rerank ativo (sai no elm_status): `api:<modelo>@<base>` ou None (inativo)."""
    m = rerank_modelo()
    return f"api:{m}@{_api()[0]}" if m else None


def _post(url: str, key: str, payload: dict, campo_modelo: str = "provider_embed_model") -> dict:
    """POST JSON autenticado com até 3 tentativas (backoff) nos status _REPETIR e em falha de rede
    transitória (timeout/conexão); erros viram Erro do contrato, nunca traceback de rede."""
    rede = None
    resp = None
    for espera in (0.0, 0.5, 1.5):
        if espera:
            time.sleep(espera)
        rede = None
        try:
            resp = requests.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=TEMPO)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            rede = f"{type(exc).__name__}: {exc}"
            continue
        if resp.status_code not in _REPETIR:
            break
    if rede:
        raise Erro("indisponivel", f"api do provider: {rede}")
    if resp.status_code >= 400:
        msg = mensagem(resp.text) or f"HTTP {resp.status_code}"
        if resp.status_code in (401, 403):
            raise Erro("credencial", f"api do provider: {msg}", "provider_api_key")
        if resp.status_code == 404:
            raise Erro("config", f"api do provider: {msg}", campo_modelo)
        if resp.status_code in _REPETIR:
            raise Erro("indisponivel", f"api do provider: {msg}")
        raise Erro("entrada", f"api do provider: {msg}")
    return resp.json()


def embed(texts: list[str]) -> np.ndarray:
    """(n, dim) float32 com norma 1: produto escalar = cosseno. A query e o índice usam a mesma
    função (e o mesmo modelo): trocar a assinatura do embedder reindexa tudo."""
    base, key = _api_ou_erro()
    data = _post(f"{base}/embeddings", key, {"model": _modelo_embed(), "input": texts})["data"]
    v = np.array([item["embedding"] for item in sorted(data, key=lambda d: d["index"])], dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True).clip(1e-9)


@functools.lru_cache
def _rerank_fn():
    api, modelo = _api(), rerank_modelo()
    if not (api and modelo):
        return None

    def rerank(query: str, docs: list[str]) -> list[float]:
        """[relevance_score] na ordem de `docs` (contrato Jina/Cohere: results[].index/relevance_score)."""
        data = _post(f"{api[0]}/rerank", api[1], {"model": modelo, "query": query, "documents": docs,
                                                  "top_n": len(docs)}, campo_modelo="provider_rerank_model")["results"]
        return [item["relevance_score"] for item in sorted(data, key=lambda d: d["index"])]

    return rerank


def rerank(query: str, docs: list[str]) -> list[float] | None:
    """[relevance_score] 0..1 na ordem de `docs` (contrato Jina/Cohere); None sem rerank ativo
    (ou com < 2 documentos)."""
    if len(docs) < 2 or rerank_modelo() is None:
        return None
    fn = _rerank_fn()
    return None if fn is None else fn(query, docs)
