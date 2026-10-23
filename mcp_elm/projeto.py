"""Arquivo do projeto: <projeto>/.kiro/config/sdd/pa_<nome>.json, um por project area.

Validado pelo pa.schema.json do power sdd (cópia embutida no pacote). O caminho do projeto vem em cada chamada:
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

PASTA = Path(".kiro") / "config" / "sdd"
_VALIDADOR = jsonschema.Draft202012Validator(json.loads(Path(__file__).with_name("pa.schema.json").read_text("utf-8")))
_cache: dict[Path, tuple[int, dict]] = {}


def _raiz(projeto: str) -> Path:
    if not projeto or not os.path.isabs(projeto):
        raise Erro("entrada", "projeto precisa ser o caminho absoluto do workspace", "projeto")
    if not os.path.isdir(projeto):
        raise Erro("entrada", f"pasta do projeto não existe: {projeto}", "projeto")
    return Path(projeto)


def nomes(projeto: str) -> list[str]:
    return sorted(p.stem[3:] for p in (_raiz(projeto) / PASTA).glob("pa_*.json"))


def arquivo(projeto: str, pa: str | None) -> Path:
    todos = nomes(projeto)
    if pa is None:
        if not todos:
            raise Erro("config", "nenhum .kiro/config/sdd/pa_<nome>.json no projeto (rode a sdd-setup)", "pa")
        if len(todos) > 1:
            raise Erro("entrada", f"vários pa no projeto: {', '.join(todos)}; informe pa", "pa")
        pa = todos[0]
    if pa not in todos:
        raise Erro("config", f"pa_{pa}.json não existe (há: {', '.join(todos) or 'nenhum'})", "pa")
    return _raiz(projeto) / PASTA / f"pa_{pa}.json"


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
    """JSON do arquivo, com cache pelo mtime (a sdd-setup pode regravá-lo com o MCP no ar)."""
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


def gravar_iteracao(cfg: dict, nome: str, iteracao_id: str, planos: dict[str, str]) -> None:
    """Grava ccm.iteracoes[nome] no arquivo, relido agora (indent=2, ordem das chaves preservada)."""
    arq = Path(cfg["_arquivo"])
    dados = json.loads(arq.read_text("utf-8"))
    dados["ccm"].setdefault("iteracoes", {})[nome] = {"id": iteracao_id, "planos": planos}
    arq.write_text(json.dumps(dados, indent=2, ensure_ascii=False) + "\n", "utf-8")
    cfg["ccm"].setdefault("iteracoes", {})[nome] = {"id": iteracao_id, "planos": planos}
