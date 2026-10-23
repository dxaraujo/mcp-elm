"""Bundle local do DOORS Next (<projeto>/<rm.download>, o que o elm_sync baixa): leitura, índice e consulta.

Índice em <bundle>/sqlite3/okf.db (WAL: vários processos podem abrir o mesmo): documentos e chunks com vetor denso
(float32 normalizado) + FTS5 (BM25); busca híbrida cosseno + FTS5 com fusão RRF. Os links entre documentos saem do
disco a cada leitura, não do índice.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import os
import posixpath
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import unquote

import numpy as np
import yaml
from markdown_it import MarkdownIt

from . import sync
from .contrato import Erro

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
RRF_K = 60
# shortcut: orçamento fixo por chamada; tornar parâmetro se algum cliente tiver timeout menor que ~60 s
TIME_BUDGET = 40
SNIPPET_MAX = 800
CHUNK_MAX = 1500
RESERVED = {"index.md", "log.md"}  # reservados pelo OKF em qualquer pasta, não documentos
SKIP = {"sync.md"}  # relatório do elm_sync na raiz
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$", re.M)
_LEADING_ID = re.compile(r"(\d+)(?:[ _:-].*)?")
_LINK_ID = re.compile(r"\s*(\d+)\s*:")  # item de `links`: 'id: título'
_MD = MarkdownIt()
SCHEMA = """
create table if not exists docs(path text primary key, hash text, id text, title text, type text, folder text,
                                description text);
create table if not exists chunks(cid integer primary key, path text, section text, text text, vec blob);
create index if not exists chunks_path on chunks(path);
create virtual table if not exists chunks_fts using fts5(text, tokenize='unicode61 remove_diacritics 2');
"""


# --- documentos no disco

def _frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter YAML completo, com mapas aninhados como `links`; corpo). Sem cabeçalho ou YAML inválido: ({}, texto)."""
    if not text.startswith("---\n") or (end := text.find("\n---\n", 3)) < 0:
        return {}, text
    try:
        head = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return {}, text
    return (head, text[end + 5:]) if isinstance(head, dict) else ({}, text)


def _text(value) -> str | None:
    return " ".join(str(value).split()) if value not in (None, "") else None


def load(root: str, path: str) -> dict:
    """Documento pelo caminho relativo (posix) ao bundle."""
    with open(os.path.join(root, *path.split("/")), "rb") as f:
        raw = f.read()
    head, body = _frontmatter(raw.decode("utf-8"))
    name = posixpath.basename(path)[:-3]
    m = _LEADING_ID.fullmatch(name)
    doc_id = head.get("id") if head.get("id") is not None else (m.group(1) if m else None)
    return {"path": path, "id": str(doc_id) if doc_id is not None else None,
            "title": _text(head.get("title")) or name, "type": _text(head.get("type")),
            "folder": path.split("/")[0] if "/" in path else "", "description": _text(head.get("description")),
            "resource": _text(head.get("resource")), "head": head, "body": body,
            "hash": hashlib.sha256(raw).hexdigest()}


def scan(root: str) -> dict[str, dict]:
    """Todos os .md do bundle (menos index.md/log.md de qualquer pasta e sync.md na raiz), por caminho relativo."""
    docs = {}
    for full in sorted(Path(root).rglob("*.md")):
        parts = full.relative_to(root).parts
        rel = "/".join(parts)
        if full.name in RESERVED or rel in SKIP or any(p.startswith(".") for p in parts):
            continue
        docs[rel] = load(root, rel)
    return docs


def by_id(docs: dict) -> dict[str, str]:
    return {d["id"]: p for p, d in docs.items() if d["id"]}


def resolve(doc_path: str, target: str, ids: dict[str, str], docs: dict) -> str | None:
    """Alvo de link -> caminho de documento do bundle; None se estiver fora dele."""
    target = unquote(target).split("#")[0].strip()
    if not target:
        return None
    if "/rm/resources/" in target:  # URL do ELM: só liga se o artefato estiver no bundle
        return next((p for p, d in docs.items() if d["resource"] == target.split("?")[0]), None)
    if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I):
        return None
    base = "" if target.startswith("/") else posixpath.dirname(doc_path)
    path = posixpath.normpath(posixpath.join(base, target.lstrip("/")))
    if path.startswith("..") or path.startswith("/"):
        return None  # fora do bundle
    if path in docs:
        return path
    m = _LEADING_ID.fullmatch(posixpath.basename(path).removesuffix(".md"))
    return ids.get(m.group(1)) if m else None


def links(doc: dict, docs: dict, ids: dict[str, str]) -> list[dict]:
    """[{rel, path}] sem repetição: do frontmatter `links {rel: ['id: título']}` e do corpo (rel 'cita')."""
    out: dict[tuple[str, str], dict] = {}
    raw = doc["head"].get("links")
    for rel, items in (raw.items() if isinstance(raw, dict) else []):
        for item in items if isinstance(items, list) else [items]:
            m = _LINK_ID.match(str(item))
            if m and (path := ids.get(m.group(1))) and path != doc["path"]:
                out[(str(rel), path)] = {"rel": str(rel), "path": path}
    for token in _MD.parse(doc["body"]):
        for child in token.children or []:
            if child.type == "link_open" and (href := child.attrGet("href")) and \
                    (path := resolve(doc["path"], str(href), ids, docs)) and path != doc["path"]:
                out.setdefault(("cita", path), {"rel": "cita", "path": path})
    return list(out.values())


def _split(text: str) -> list[str]:
    """Quebra por parágrafo em pedaços de até CHUNK_MAX (parágrafo maior que isso vai inteiro)."""
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) > CHUNK_MAX:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    return parts + ([cur] if cur.strip() else [])


def chunks(doc: dict) -> list[dict]:
    """[{section, text}] por heading (#..###); `text` já com o prefixo de contexto para o embedding."""
    body, sections = doc["body"], []
    marks = list(_HEADING.finditer(body))
    starts = [(0, "")] + [(m.start(), m.group(2)) for m in marks]
    for i, (start, title) in enumerate(starts):
        end = marks[i].start() if i < len(marks) else len(body)
        if text := body[start:end].strip():
            sections += [(title, part.strip()) for part in _split(text) if part.strip()]
    prefix = " ".join(filter(None, [doc["type"], doc["id"], "—", doc["title"]]))
    return [{"section": s, "text": f"{prefix} | {doc['folder']} | {s}\n{t}"} for s, t in sections] or \
        [{"section": "", "text": f"{prefix} | {doc['folder']}\n{doc['description'] or doc['title']}"}]


# --- índice

def db_file(root: str) -> Path:
    return Path(root) / "sqlite3" / "okf.db"


@contextlib.contextmanager
def connect(root: str):
    """Conexão ao índice do bundle; commit ao sair sem erro."""
    path = db_file(root)
    if not path.parent.is_dir():
        path.parent.mkdir()
        (path.parent / ".gitignore").write_text("*\n")  # .db/-wal/-shm são locais, não vão para o git
    con = sqlite3.connect(path, timeout=30)
    try:
        con.execute("pragma journal_mode=wal")
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


@functools.cache
def _model():
    from fastembed import TextEmbedding  # preguiçoso: o servidor sobe sem carregar o modelo
    return TextEmbedding(MODEL)


def embed(texts: list[str]) -> np.ndarray:
    """(n, dim) float32 com norma 1: produto escalar = cosseno."""
    v = np.array(list(_model().embed(texts)), dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True).clip(1e-9)


def _delete(con: sqlite3.Connection, path: str) -> None:
    con.execute("delete from chunks_fts where rowid in (select cid from chunks where path = ?)", (path,))
    con.execute("delete from chunks where path = ?", (path,))
    con.execute("delete from docs where path = ?", (path,))


def _write(con: sqlite3.Connection, doc: dict) -> None:
    """Troca os chunks do documento e grava a linha em `docs` (com o hash: marca como indexado)."""
    parts = chunks(doc)
    vecs = embed([c["text"] for c in parts])  # antes de apagar: falha no modelo não deixa o documento sem chunks
    _delete(con, doc["path"])
    con.execute("insert into docs values (?, ?, ?, ?, ?, ?, ?)", tuple(doc[k] for k in (
        "path", "hash", "id", "title", "type", "folder", "description")))
    for c, v in zip(parts, vecs):
        cid = con.execute("insert into chunks(path, section, text, vec) values (?, ?, ?, ?)",
                          (doc["path"], c["section"], c["text"], v.tobytes())).lastrowid
        con.execute("insert into chunks_fts(rowid, text) values (?, ?)", (cid, c["text"]))


def _indexar(root: str, docs: dict, forcar: bool = False) -> dict:
    """Remove do índice os apagados e (re)indexa os novos ou alterados (sha256) até o TIME_BUDGET."""
    with connect(root) as con:
        if forcar:
            con.execute("update docs set hash = ''")  # continua buscável enquanto reindexa
        indexed = dict(con.execute("select path, hash from docs"))
        removed = [p for p in indexed if p not in docs]
        for p in removed:
            _delete(con, p)
        con.commit()
        pending = [p for p, d in docs.items() if indexed.get(p) != d["hash"]]
        done, deadline = 0, time.monotonic() + TIME_BUDGET
        for p in pending:
            if time.monotonic() > deadline:
                break
            _write(con, docs[p])
            con.commit()  # por documento: reinício do MCP no meio do lote não perde o progresso
            done += 1
    return {"documentos": len(docs), "indexados": done, "removidos": len(removed), "restantes": len(pending) - done}


def _search(con: sqlite3.Connection, query: str, wide: int, folder: str | None, type_: str | None) -> list[dict]:
    """Até `wide` chunks, do mais relevante: [{path, section, text, id, title, type, folder, score}]."""
    where, args = "1 = 1", []
    for col, value in (("folder", folder), ("type", type_)):
        if value is not None:
            where += f" and d.{col} = ?"
            args.append(value)
    # shortcut: vetores lidos a cada consulta; cachear a matriz se o bundle passar de ~50k chunks
    rows = con.execute(f"select c.cid, c.vec from chunks c join docs d on d.path = c.path where {where}",
                       args).fetchall()
    dense = []
    if rows:
        scores = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1) \
            @ embed([query])[0]
        dense = [rows[i][0] for i in np.argsort(-scores)[:wide]]
    # shortcut: FTS5 sem stemming PT (prefixo * cobre plurais); tokenizer próprio se o recall lexical cair
    terms = re.findall(r"\w+", query)
    lexical = [r[0] for r in con.execute(
        f"select f.rowid from chunks_fts f join chunks c on c.cid = f.rowid join docs d on d.path = c.path "
        f"where chunks_fts match ? and {where} order by bm25(chunks_fts) limit ?",
        [" OR ".join(f'"{t}"*' for t in terms), *args, wide])] if terms else []
    rrf: dict[int, float] = {}
    for ranking in (dense, lexical):
        for rank, cid in enumerate(ranking):
            rrf[cid] = rrf.get(cid, 0) + 1 / (RRF_K + rank + 1)
    best = sorted(rrf, key=rrf.get, reverse=True)[:wide]
    found = {r[0]: r for r in con.execute(
        f"select c.cid, c.path, c.section, c.text, d.id, d.title, d.type, d.folder from chunks c "
        f"join docs d on d.path = c.path where c.cid in ({','.join('?' * len(best))})", best)}
    return [dict(zip(("path", "section", "text", "id", "title", "type", "folder"), found[cid][1:]),
                 score=rrf[cid]) for cid in best]


# --- operações (as tools elm_*_local)

def _bundle(cfg: dict) -> tuple[str, dict]:
    """(raiz, documentos) do bundle; sem nada baixado: nao_encontrado com a saída (baixar ou ir ao ELM remoto)."""
    root = sync._raiz(cfg)
    docs = scan(root) if os.path.isdir(root) else {}
    if not docs:
        raise Erro("nao_encontrado", f"nada baixado em {cfg['rm']['download']}: baixe com elm_sync (direcao baixar) "
                   "ou pesquise no ELM remoto com elm_buscar_req/elm_ler_req", "bundle")
    return root, docs


def reindexar(root: str) -> dict | None:
    """Fim do elm_sync baixar: indexa o que mudou no bundle (None se ele estiver vazio)."""
    docs = scan(root)
    return _indexar(root, docs) if docs else None


def indexar(cfg: dict, forcar: bool = False) -> dict:
    return _indexar(*_bundle(cfg), forcar)


def buscar(cfg: dict, texto: str, limite: int = 8, pasta: str | None = None, tipo: str | None = None) -> dict:
    if not texto.strip():
        raise Erro("entrada", "texto vazio", "texto")
    limite = max(1, limite)
    root, docs = _bundle(cfg)
    idx = _indexar(root, docs)
    with connect(root) as con:
        hits = _search(con, texto, limite * 5, pasta, tipo)
    out: dict[str, dict] = {}
    for h in hits:
        r = out.get(h["path"])
        if r is None:
            if len(out) >= limite:
                continue
            r = out[h["path"]] = {"path": h["path"], "id": h["id"], "titulo": h["title"], "tipo": h["type"],
                                  "pasta": h["folder"], "score": round(h["score"], 4), "trechos": []}
        if len(r["trechos"]) < 2:
            r["trechos"].append({"secao": h["section"], "texto": h["text"].split("\n", 1)[-1][:SNIPPET_MAX]})
    return {"itens": list(out.values()),
            **({"indexando": True, "restantes": idx["restantes"]} if idx["restantes"] else {})}


def listar(cfg: dict, pasta: str | None = None, tipo: str | None = None) -> dict:
    root, docs = _bundle(cfg)
    with connect(root) as con:
        indexed = dict(con.execute("select path, hash from docs"))
    return {"itens": [{"path": p, "id": d["id"], "titulo": d["title"], "tipo": d["type"], "pasta": d["folder"],
                       "descricao": d["description"], "indexado": indexed.get(p) == d["hash"]}
                      for p, d in docs.items()
                      if (not pasta or d["folder"] == pasta) and (not tipo or d["type"] == tipo)]}


def ler(cfg: dict, ref) -> dict:
    """{path, markdown (arquivo inteiro), links {saida, entrada}: [{rel, path, id, titulo}]}, por path ou id."""
    root, docs = _bundle(cfg)
    ids, ref = by_id(docs), str(ref)
    path = ref if ref in docs else ids.get(ref)
    if path is None:
        raise Erro("nao_encontrado", f"{ref} não está no bundle local; leia no ELM com elm_ler_req", "id")

    def item(rel: str, p: str) -> dict:
        return {"rel": rel, "path": p, "id": docs[p]["id"], "titulo": docs[p]["title"]}
    # shortcut: entrada relê os links de todo o bundle a cada leitura; guardar no índice se ficar lento
    saida = [item(link["rel"], link["path"]) for link in links(docs[path], docs, ids)]
    entrada = [item(link["rel"], p) for p, d in docs.items() if p != path
               for link in links(d, docs, ids) if link["path"] == path]
    with open(os.path.join(root, *path.split("/")), encoding="utf-8") as f:
        return {"path": path, "markdown": f.read(), "links": {"saida": saida, "entrada": entrada}}
