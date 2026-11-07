"""Arquivo do projeto: <projeto>/.sabia/config/elm/pa_<nome>.json, um por project area.

Leitura procura em PASTAS, nesta ordem (vale a primeira que tiver o pa); gravação vai sempre para PASTA.

Validado pelo pa.schema.json (cópia embutida no pacote). O caminho do projeto vem em cada chamada:
o Kiro não inicia o MCP de um power no workspace.
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

import jsonschema

from .contrato import Erro

PASTA = Path(".sabia") / "config" / "elm"
PASTAS = (PASTA, Path(".kiro") / "config" / "elm", Path(".kiro") / "config" / "sdd")  # as duas últimas: projetos anteriores
_ONDE = ", ".join(map(str, PASTAS))
_VALIDADOR = jsonschema.Draft202012Validator(json.loads(Path(__file__).with_name("pa.schema.json").read_text("utf-8")))
_cache: dict[Path, tuple[int, dict]] = {}


def _raiz(projeto: str) -> Path:
    if not projeto or not os.path.isabs(projeto):
        raise Erro("entrada", "projeto precisa ser o caminho absoluto do workspace", "projeto")
    if not os.path.isdir(projeto):
        raise Erro("entrada", f"pasta do projeto não existe: {projeto}", "projeto")
    return Path(projeto)


def _achados(projeto: str) -> dict[str, Path]:
    """nome → arquivo; o mesmo nome em mais de uma pasta fica com a primeira de PASTAS."""
    raiz, out = _raiz(projeto), {}
    for pasta in PASTAS:
        for p in (raiz / pasta).glob("pa_*.json"):
            out.setdefault(p.stem[3:], p)
    return out


def nomes(projeto: str) -> list[str]:
    return sorted(_achados(projeto))


def arquivo(projeto: str, pa: str | None) -> Path:
    achados = _achados(projeto)
    todos = sorted(achados)
    if pa is None:
        if not todos:
            raise Erro("config", f"nenhum {PASTA}/pa_<nome>.json no projeto (procurado em: {_ONDE})", "pa")
        if len(todos) > 1:
            raise Erro("entrada", f"vários pa no projeto: {', '.join(todos)}; informe pa (o mesmo pa_<nome>.json em "
                                  f"mais de uma pasta vale o da primeira, na ordem: {_ONDE})", "pa")
        pa = todos[0]
    if pa not in achados:
        raise Erro("config", f"{PASTA}/pa_{pa}.json não existe (procurado em: {_ONDE}; há: {', '.join(todos) or 'nenhum'})",
                   "pa")
    return achados[pa]


def faltando(dados) -> list[str]:
    """Campos ausentes ou inválidos segundo o schema, em caminho com ponto: ['ccm.horas.time-code']."""
    out = set()
    for e in _VALIDADOR.iter_errors(dados):
        path = ".".join(str(p) for p in e.absolute_path)
        if e.validator in ("required", "additionalProperties") and (m := re.search(r"'([^']+)'", e.message)):
            path = f"{path}.{m.group(1)}" if path else m.group(1)
        out.add(path or "pa")
    return sorted(out)


def ler(arq: Path) -> dict:
    """JSON do arquivo, com cache pelo mtime."""
    mtime = arq.stat().st_mtime_ns
    hit = _cache.get(arq)
    if hit is None or hit[0] != mtime:
        try:
            hit = _cache[arq] = (mtime, json.loads(arq.read_text("utf-8")))
        except ValueError as exc:
            raise Erro("config", f"{arq.name} não é JSON válido: {exc}", "pa") from exc
    return hit[1]


def carregar(projeto: str, pa: str | None = None) -> dict:
    """pa validado (cópia) + `_arquivo` e `_projeto`. Erro `config` com o primeiro campo faltando."""
    arq = arquivo(projeto, pa)
    dados = ler(arq)
    if erros := faltando(dados):
        raise Erro("config", f"{arq.name}: ausente ou inválido: {', '.join(erros)}", erros[0])
    return {**copy.deepcopy(dados), "_arquivo": str(arq), "_projeto": projeto}


def gravar_ccm(cfg: dict, chave: str, valor, sub: str | None = None) -> None:
    """Grava ccm.<chave> (ou ccm.<chave>[sub]) no arquivo, relido agora (indent=2, ordem das chaves preservada), e
    no cfg. Sempre em PASTA: um pa lido de pasta antiga passa a existir lá (o antigo fica, mas perde a precedência)."""
    origem = Path(cfg["_arquivo"])
    dados = json.loads(origem.read_text("utf-8"))
    arq = Path(cfg["_projeto"]) / PASTA / origem.name
    arq.parent.mkdir(parents=True, exist_ok=True)
    for ccm in (dados["ccm"], cfg["ccm"]):
        if sub is None:
            ccm[chave] = valor
        else:
            ccm.setdefault(chave, {})[sub] = valor
    arq.write_text(json.dumps(dados, indent=2, ensure_ascii=False) + "\n", "utf-8")
    cfg["_arquivo"] = str(arq)


def gravar_iteracao(cfg: dict, nome: str, iteracao_id: str, planos: dict[str, str]) -> None:
    gravar_ccm(cfg, "iteracoes", {"id": iteracao_id, "planos": planos}, nome)
