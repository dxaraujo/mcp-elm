"""Embedding e rerank do índice local: API OpenAI-compatível ou o modelo local (fastembed, sem key).

A API vem do elm.properties (a mesma base e key servem para os dois; só o modelo muda):
`provider_url` + `provider_api_key` ligam o embedding por API (modelo em `provider_embed_model`, padrão
text-embedding-3-small) e `provider_rerank_model` opcional liga o rerank (mesma base/key). Sem `provider_url`
ou `provider_api_key`, o embedding cai no modelo local — o comportamento antigo.

A `assinatura()` identifica o espaço de vetores do índice (base + modelo): se ela mudar, o índice
é limpo e refeito, porque vetores de dimensão/modelo diferentes não podem coexistir na matriz.
"""
from __future__ import annotations

import functools
import time

import numpy as np
import requests

from .config import CredencialError, load_config
from .contrato import Erro
from .http import mensagem

LOCAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
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
    """Credenciais do usuário; sem o arquivo (ou inválido): None -> tudo cai no local."""
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


def _embed_modelo() -> str:
    if _api() is None:
        return LOCAL_MODEL
    return _cfg().provider_embed_model or MODELO_PADRAO


def assinatura() -> str:
    """Identificador estável do espaço de vetores (vai na meta do índice)."""
    api = _api()
    return f"api:{_embed_modelo()}@{api[0]}" if api else f"local:{LOCAL_MODEL}"


def rerank_modelo() -> str | None:
    c = _cfg()
    return c.provider_rerank_model if (c and _api() and c.provider_rerank_model) else None


def tem_rerank() -> bool:
    return rerank_modelo() is not None


def _post(url: str, key: str, payload: dict) -> dict:
    """POST JSON autenticado com até 3 tentativas (backoff) nos status _REPETIR; erros viram Erro do contrato."""
    for espera in (0.0, 0.5, 1.5):
        if espera:
            time.sleep(espera)
        resp = requests.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=TEMPO)
        if resp.status_code not in _REPETIR:
            break
    if resp.status_code >= 400:
        msg = mensagem(resp.text) or f"HTTP {resp.status_code}"
        if resp.status_code in (401, 403):
            raise Erro("credencial", f"api de embeddings: {msg}", "provider_api_key")
        if resp.status_code == 404:
            raise Erro("config", f"api de embeddings: {msg}", "provider_embed_model")
        if resp.status_code in _REPETIR:
            raise Erro("indisponivel", f"api de embeddings: {msg}")
        raise Erro("entrada", f"api de embeddings: {msg}")
    return resp.json()


@functools.cache
def _local_model():
    from fastembed import TextEmbedding  # preguiçoso: o servidor sobe sem carregar o modelo
    return TextEmbedding(LOCAL_MODEL)


def embed(texts: list[str]) -> np.ndarray:
    """(n, dim) float32 com norma 1: produto escalar = cosseno. A query e o índice usam a mesma
    função (e o mesmo modelo): trocar a assinatura do embedder reindexa tudo."""
    api = _api()
    if api:
        data = _post(f"{api[0]}/embeddings", api[1], {"model": _embed_modelo(), "input": texts})["data"]
        v = np.array([item["embedding"] for item in sorted(data, key=lambda d: d["index"])], dtype=np.float32)
    else:
        v = np.array(list(_local_model().embed(texts)), dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True).clip(1e-9)


@functools.lru_cache
def _rerank_fn():
    api, modelo = _api(), rerank_modelo()
    if not (api and modelo):
        return None

    def rerank(query: str, docs: list[str]) -> list[float]:
        """[relevance_score] na ordem de `docs` (contrato Jina/Cohere: results[].index/relevance_score)."""
        data = _post(f"{api[0]}/rerank", api[1], {"model": modelo, "query": query, "documents": docs,
                                                  "top_n": len(docs)})["results"]
        return [item["relevance_score"] for item in sorted(data, key=lambda d: d["index"])]

    return rerank


def rerank(query: str, docs: list[str]) -> list[float] | None:
    """Scores na ordem de `docs`; None sem provider_rerank_model configurado (ou com < 2 documentos)."""
    fn = _rerank_fn()
    return None if fn is None or len(docs) < 2 else fn(query, docs)
