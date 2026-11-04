"""Bundle local do DOORS Next (<projeto>/<rm.download>, o que o elm_sync baixa): leitura, índice e consulta.

Índice em <bundle>/sqlite3/okf.db (WAL: vários processos podem abrir o mesmo): documentos e chunks com vetor denso
(float32 normalizado) + FTS5 (BM25); busca híbrida cosseno + FTS5 com fusão RRF, e rerank opcional no fim (falhou,
a busca segue na ordem RRF e devolve `erro_rerank`), type-boost no pool inteiro antes do slicing (Caso de Uso
> História de Usuário > Regra) e piso de título (o query inteiro no título — ou id == query — garante slot no
top, mesmo com chunks fora do pool do RRF). O vetor vem da API configurada no elm.properties (embedder, sem
fallback local); a assinatura do embedder e a versão do chunking ficam na meta do índice — sem proveniência
(meta sem o embedder), com outro modelo ou com formato de chunk antigo, o índice é limpo e refeito; e a busca
em índice de outro embedder falha com erro claro (cossinos de espaço diferente são inválidos — o reindex
resolve). Os links entre documentos saem do disco a cada leitura, não do índice.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import posixpath
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path
from urllib.parse import unquote

import numpy as np
import yaml
from markdown_it import MarkdownIt

from . import embedder, sync
from .contrato import Erro

RRF_K = 60
RERANK_WIDE = 100  # candidatos do RRF para o rerank refinar o top
RERANK_MAX = 8000  # chars por chunk no rerank: margem de segurança no contexto de 8192 tokens do modelo
# type-boost: multiplicativo pós-rerank, no pool inteiro antes do slicing; a chave é o `type` exato do
# frontmatter ("Regra de Negócio" não é "Regra"); o score devolvido é o ajustado
TYPE_BOOST = {"Caso de Uso": 1.03, "História de Usuário": 1.02, "Regra": 1.01}
# 4º estágio (piso de título): o documento cujo título contém todos os termos do query (ou id == query)
# ocupa um dos slots do top, mesmo com os chunks fora do pool do RRF (o corpo pode desenvolver pouco o que
# o título anuncia — o recall de "correção monetária" no PAB); a precedência de type é a do TYPE_BOOST
TITULO_SLOTS = 2
_TIPO_RANK = {"Caso de Uso": 3, "História de Usuário": 2, "Regra": 1}
# testes: True indexa na própria chamada; no servidor, sempre numa thread (o 1º uso percorre o bundle pela API)
SINCRONO = False
_jobs: dict[str, threading.Thread] = {}
_falhas: dict[str, str] = {}
# documentos que o provider recusou (erro de entrada, ex.: contexto estourado), por bundle: path -> (hash, erro).
# Não voltam ao lote enquanto o arquivo não mudar (ou até o forcar); em memória: reiniciar o MCP tenta de novo
_rejeitados: dict[str, dict[str, tuple[str, str]]] = {}
_lock = threading.Lock()
SNIPPET_MAX = 800
CHUNK_MAX = 1500
CHUNK_VER = 2  # formato do chunk: v2 quebra por linha o parágrafo > CHUNK_MAX (v1 ia inteiro); mudou: índice refazido
RESERVED = {"index.md", "log.md"}  # reservados pelo OKF em qualquer pasta, não documentos
SKIP = {"sync.md"}  # relatório do elm_sync na raiz
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$", re.M)
_LEADING_ID = re.compile(r"(\d+)(?:[ _:-].*)?")
_LINK_ID = re.compile(r"\s*(\d+)\s*:")  # item de `links`: 'id: título'
_MD = MarkdownIt()
SCHEMA = """
create table if not exists docs(path text primary key, hash text, id text, title text, type text, folder text,
                                description text);
create table if not exists chunks(cid integer primary key autoincrement, path text, section text, text text, vec blob);
create index if not exists chunks_path on chunks(path);
create virtual table if not exists chunks_fts using fts5(text, tokenize='unicode61 remove_diacritics 2');
create table if not exists meta(k text primary key, v text);
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


_SEP = re.compile(r"^\|[\s:|-]+\|$")  # separador de tabela markdown ('| --- | --- |')


def _partes_linha(para: str) -> list[str]:
    """Parágrafo > CHUNK_MAX em partes por linha; tabela markdown repete header + separador no começo de cada parte."""
    linhas = para.splitlines()
    header = None
    if len(linhas) >= 2 and linhas[0].lstrip().startswith("|") and _SEP.match(linhas[1].strip()):
        header = f"{linhas[0]}\n{linhas[1]}"
    parts, cur, tam = [], [], 0
    # linha maior que CHUNK_MAX (tabela larga, XML, base64) vai em fatias: nenhum chunk passa do teto e o
    # input nunca estoura o contexto do modelo. ponytail: sem bump do CHUNK_VER — os chunks já gravados
    # cabem no modelo; o documento com linha gigante pega as fatias quando mudar (ou no forcar)
    for linha in (f for ln in linhas for f in ([ln[i:i + CHUNK_MAX] for i in range(0, len(ln), CHUNK_MAX)] or [ln])):
        if cur and tam + len(linha) + 1 > CHUNK_MAX:
            parts.append("\n".join(cur))
            cur, tam = [], 0
        cur.append(linha)
        tam += len(linha) + 1
    if cur:
        parts.append("\n".join(cur))
    if header:
        parts = [parts[0]] + [f"{header}\n{p}" for p in parts[1:]]
    return parts


def _split(text: str) -> list[str]:
    """Pedaços de até CHUNK_MAX: junta parágrafos; parágrafo maior que CHUNK_MAX quebra por linha."""
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        for ped in _partes_linha(para) if len(para) > CHUNK_MAX else [para]:
            if cur and len(cur) + len(ped) > CHUNK_MAX:
                parts.append(cur)
                cur = ""
            cur = f"{cur}\n\n{ped}" if cur else ped
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
    base = " ".join(filter(None, [doc["type"], doc["id"]]))  # sem type/id, o "—" não fica solto
    prefix = f"{base} — {doc['title']}" if base else doc["title"]
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


def embed(texts: list[str]) -> np.ndarray:
    """(n, dim) float32 com norma 1: produto escalar = cosseno (via API, veja embedder)."""
    return embedder.embed(texts)


def rerank(texto: str, hits: list[dict]) -> tuple[list[dict] | None, str | None]:
    """Reordena os chunks pela score do rerank (substitui a do RRF); (None, None) sem rerank configurado e
    (None, detalhe) se a API falhar — a busca segue na ordem RRF. Cada chunk vai truncado a RERANK_MAX
    (o contexto do modelo é de 8192 tokens)."""
    if len(hits) < 2:
        return None, None
    try:
        scores = embedder.rerank(texto, [h["text"][:RERANK_MAX] for h in hits])
    except Exception as exc:  # o rerank é opcional: falha nele degrada para o RRF, não derruba a busca
        return None, f"{type(exc).__name__}: {exc}"[:200]
    if scores is None:
        return None, None
    for h, s in zip(hits, scores):
        h["score"] = s
    return sorted(hits, key=lambda h: h["score"], reverse=True), None


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


def _mudou(meta: dict) -> bool:
    """O índice renasce: sem proveniência (meta sem o embedder — índice de versão anterior), vetores de
    outro modelo (dimensão muda) ou chunks do formato anterior."""
    return meta.get("embedder") != embedder.assinatura() or \
        meta.get("chunking") not in (None, str(CHUNK_VER))


def _indexar(root: str, docs: dict, forcar: bool = False) -> dict:
    """Remove do índice os apagados e (re)indexa os novos ou alterados (sha256). Meta sem proveniência,
    assinatura do embedder (troca de modelo) ou versão de chunking diferente da meta limpa o índice
    inteiro, que renasce do bundle + API. Sem provider configurado, erro claro — antes de apagar nada."""
    sig = embedder.assinatura()  # antes de abrir o índice: sem provider, o índice antigo continua inteiro
    with connect(root) as con:
        rej = _rejeitados.setdefault(root, {})
        if _mudou(dict(con.execute("select k, v from meta"))):
            rej.clear()  # outro modelo/formato: o que foi recusado ganha nova chance
            con.execute("delete from chunks_fts")
            con.execute("delete from chunks")
            con.execute("delete from docs")
            con.commit()
        for k, v in (("embedder", sig), ("chunking", str(CHUNK_VER))):
            con.execute("insert into meta(k, v) values (?, ?) on conflict(k) do update set v = excluded.v", (k, v))
        if forcar:
            con.execute("update docs set hash = ''")  # continua buscável enquanto reindexa
        indexed = dict(con.execute("select path, hash from docs"))
        removed = [p for p in indexed if p not in docs]
        for p in removed:
            _delete(con, p)
        con.commit()
        pending = [p for p, d in docs.items() if indexed.get(p) != d["hash"] and rej.get(p, ("",))[0] != d["hash"]]
        done = recusados = 0
        for p in pending:
            try:
                _write(con, docs[p])
            except Erro as exc:
                if exc.codigo != "entrada":  # credencial, config, indisponível: valem para todos, o lote para
                    raise
                rej[p] = (docs[p]["hash"], exc.detalhe)  # o provider recusou ESTE documento: registra e segue
                recusados += 1
                continue
            rej.pop(p, None)
            con.commit()  # por documento: reinício do MCP no meio do lote não perde o progresso
            done += 1
    return {"documentos": len(docs), "indexados": done, "removidos": len(removed), "recusados": recusados,
            "restantes": len(pending) - done - recusados}


def _palavras(texto: str) -> list[str]:
    """Palavras minúsculas sem diacríticos (título e query comparados no mesmo formato)."""
    s = "".join(c for c in unicodedata.normalize("NFD", texto) if not unicodedata.combining(c)).lower()
    return re.findall(r"\w+", s)


def _casam(a: str, b: str) -> bool:
    """Igualdade com tolerância de prefixo nos dois lados ("correcao" casa "correcoes"); o prefixo só
    conta a partir de 3 letras."""
    return a == b or (len(a) >= 3 and b.startswith(a)) or (len(b) >= 3 and a.startswith(b))


def _piso_titulo(con: sqlite3.Connection, query: str, hits: list[dict], rows: list,
                 scores: np.ndarray | None, where: str, args: list) -> None:
    """4º estágio (pós-rerank e type-boost): o documento cujo título contém todos os termos do query —
    ou cujo id é o query inteiro — ocupa um dos TITULO_SLOTS do top, mesmo com os chunks fora do pool do
    RRF. Score = o máximo do pool + acréscimo por slot (o slot 0 é o maior; o top fica estável); sem
    chunk do documento no pool, entra o melhor chunk dele por score densa. A where do _search vale aqui
    também: os filtros `pasta`/`tipo` cortam os candidatos."""
    if not TITULO_SLOTS or scores is None:
        return
    qnorm = " ".join(_palavras(query))
    termos = [t for t in _palavras(query) if len(t) >= 3 or (t.isdigit() and len(t) >= 2)]
    if not termos:
        return
    por_path: dict[str, list[dict]] = {}
    for h in hits:
        por_path.setdefault(h["path"], []).append(h)
    cands = []
    for path, doc_id, title, tipo, folder in con.execute(
            f"select d.path, d.id, d.title, d.type, d.folder from docs d where {where}", args):
        if qnorm.isdigit() and doc_id == query.strip():
            frase, cov = 1, 1.0  # busca pelo id do documento: direto ao top
        else:
            palavras = list(dict.fromkeys(_palavras(title or "")))
            if not palavras or not all(any(_casam(t, p) for p in palavras) for t in termos):
                continue
            frase = 1 if qnorm in " ".join(palavras) else 0
            cov = sum(1 for p in palavras if any(_casam(t, p) for t in termos)) / len(palavras)
        cands.append({"path": path, "id": doc_id, "title": title, "type": tipo, "folder": folder,
                      "frase": frase, "cov": cov, "tipo": _TIPO_RANK.get(tipo or "", 0),
                      "melhor": max((h["score"] for h in por_path.get(path, ())), default=0.0)})
    if not cands:
        return
    cands.sort(key=lambda c: (-c["frase"], -c["cov"], -c["tipo"], -c["melhor"], c["path"]))
    escolhidos = cands[:TITULO_SLOTS]
    n, base = len(escolhidos), max((h["score"] for h in hits), default=0.0)
    pos = {cid: i for i, (cid, _) in enumerate(rows)}
    for i, c in enumerate(escolhidos):
        score = base + (n - i) * 2e-4  # absoluto: slots vizinhos seguem distintos arredondados a 4 casas
        em_pool = por_path.get(c["path"])
        if em_pool:
            max(em_pool, key=lambda h: h["score"])["score"] = score
        else:
            do_doc = con.execute("select cid, section, text from chunks where path = ?", (c["path"],)).fetchall()
            if not do_doc:
                continue
            cid, section, texto = max(do_doc, key=lambda t: scores[pos[t[0]]])
            hits.append({"path": c["path"], "section": section, "text": texto, "id": c["id"],
                         "title": c["title"], "type": c["type"], "folder": c["folder"], "score": score})
    hits.sort(key=lambda h: h["score"], reverse=True)


def _search(con: sqlite3.Connection, query: str, wide: int, folder: str | None, type_: str | None) \
        -> tuple[list[dict], str | None]:
    """Até `wide` chunks, do mais relevante: [{path, section, text, id, title, type, folder, score}], com o
    erro do rerank (None se ele reordenou ou não está configurado); o score sai ajustado pelo type-boost
    e pelo piso de título (4º estágio). Índice de outro embedder: erro claro (cossinos de espaço
    diferente são inválidos — o reindex resolve)."""
    where, args = "1 = 1", []
    for col, value in (("folder", folder), ("type", type_)):
        if value is not None:
            where += f" and d.{col} = ?"
            args.append(value)
    # guarda de espaço: assinatura do índice (meta) diferente da atual -> erro antes de gastar a API
    # da query; meta sem proveniência deixa passar (a dimensão incompatível vira o mesmo erro abaixo)
    if (dita := dict(con.execute("select k, v from meta")).get("embedder")) and \
            (atual := embedder.assinatura()) != dita:
        raise Erro("config", f"índice do bundle feito com outro embedder (índice: {dita}; atual: {atual}): "
                             "rode elm_indexar_local", "provider_embed_model")
    # shortcut: vetores lidos a cada consulta; cachear a matriz se o bundle passar de ~50k chunks
    rows = con.execute(f"select c.cid, c.vec from chunks c join docs d on d.path = c.path where {where}",
                       args).fetchall()
    dense = []
    scores = None
    if rows:
        try:
            matriz = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
            scores = matriz @ embed([query])[0]
        except ValueError:
            raise Erro("config", "índice do bundle incompatível com o embedder atual (dimensões de vetor "
                                 "diferentes): rode elm_indexar_local", "provider_embed_model")
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
    hits = [dict(zip(("path", "section", "text", "id", "title", "type", "folder"), found[cid][1:]),
                 score=rrf[cid]) for cid in best]
    # 2º estágio: o rerank (cross-encoder, por API) reordena o top do RRF
    reordenados, erro_rerank = rerank(query, hits)
    if reordenados is not None:
        hits = reordenados
    # 3º estágio: type-boost — o pool inteiro multiplica pelo fator do type antes do slicing
    for h in hits:
        h["score"] *= TYPE_BOOST.get(h["type"] or "", 1.0)
    hits.sort(key=lambda h: h["score"], reverse=True)
    # 4º estágio: piso de título — o query inteiro no título (ou id == query) garante slot no top
    _piso_titulo(con, query, hits, rows, scores, where, args)
    return hits, erro_rerank


# --- operações (as tools elm_*_local)

def _bundle(cfg: dict) -> tuple[str, dict]:
    """(raiz, documentos) do bundle; sem nada baixado: nao_encontrado com a saída (baixar ou ir ao ELM remoto)."""
    root = sync._raiz(cfg)
    docs = scan(root) if os.path.isdir(root) else {}
    if not docs:
        raise Erro("nao_encontrado", f"nada baixado em {cfg['rm']['download']}: baixe com elm_sync (direcao baixar) "
                   "ou pesquise no ELM remoto com elm_buscar_req/elm_ler_req", "bundle")
    return root, docs


def _progresso(root: str, docs: dict) -> dict:
    with connect(root) as con:
        meta = dict(con.execute("select k, v from meta"))
        indexed = dict(con.execute("select path, hash from docs"))
    mudou = _mudou(meta)  # vetores de outro modelo ou chunks do formato antigo: tudo renasce
    rej = {p: e for p, (h, e) in _rejeitados.get(root, {}).items() if p in docs and docs[p]["hash"] == h}
    restantes = sum(p not in docs for p in indexed) + \
        (len(docs) if mudou else sum(indexed.get(p) != d["hash"] and p not in rej for p, d in docs.items()))
    job = _jobs.get(root)
    return {"documentos": len(docs), "restantes": restantes, "indexando": bool(job and job.is_alive()),
            **({"falhas": [{"path": p, "erro": e} for p, e in sorted(rej.items())]} if rej and not mudou else {}),
            **({"erro_indice": _falhas[root]} if root in _falhas else {})}


def _trabalho(root: str) -> None:
    try:
        _indexar(root, scan(root))
        _falhas.pop(root, None)
    except Exception as exc:  # na thread: a próxima chamada mostra o erro e dispara de novo
        _falhas[root] = f"{type(exc).__name__}: {exc}"[:200]


def disparar(root: str, docs: dict, forcar: bool = False) -> dict:
    """Indexa em segundo plano e devolve o progresso na hora: nenhuma chamada espera o modelo nem o lote."""
    if forcar:
        _rejeitados.pop(root, None)  # o forcar tenta de novo também o que o provider recusou
        with connect(root) as con:
            con.execute("update docs set hash = ''")  # continua buscável enquanto reindexa
    elif not _progresso(root, docs)["restantes"]:
        return _progresso(root, docs)  # nada mudou (ou só recusados): sem thread
    _falhas.pop(root, None)  # o erro da rodada anterior não aparece junto com a rodada nova
    if SINCRONO:
        _trabalho(root)
    else:
        with _lock:
            job = _jobs.get(root)
            if not (job and job.is_alive()):
                _jobs[root] = threading.Thread(target=_trabalho, args=(root,), daemon=True, name=f"indice {root}")
                _jobs[root].start()
    return _progresso(root, docs)


def aguardar(root: str) -> None:
    """Espera a indexação em curso (testes)."""
    if job := _jobs.get(root):
        job.join()


def indexar(cfg: dict, forcar: bool = False) -> dict:
    return disparar(*_bundle(cfg), forcar)


def buscar(cfg: dict, texto: str, limite: int = 8, pasta: str | None = None, tipo: str | None = None) -> dict:
    if not texto.strip():
        raise Erro("entrada", "texto vazio", "texto")
    limite = max(1, limite)
    root, docs = _bundle(cfg)
    idx = _progresso(root, docs)
    if idx["restantes"]:  # dispara e busca no que já está no índice
        idx = disparar(root, docs)
    wide = max(limite * 5, RERANK_WIDE) if embedder.tem_rerank() else limite * 5
    with connect(root) as con:
        hits, erro_rerank = _search(con, texto, wide, pasta, tipo)
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
            **({"erro_rerank": erro_rerank} if erro_rerank else {}),
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
