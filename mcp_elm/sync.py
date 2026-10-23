"""Bundle OKF do DOORS Next (elm_sync) em <projeto>/<rm.download>: as duas vias.

`sync.md` = fila e situação de cada artefato; `index.md` por diretório; `log.md` = o que cada sync mudou.
Estados: novo, sincronizado, desatualizado (o ELM mudou), atualizado (o md mudou), normalizado (o ELM foge da regra
embed/link do bundle; o upload corrige), conflito (os dois mudaram), erro: ...
"""
from __future__ import annotations

import glob
import hashlib
import os
import posixpath
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from . import oslc, rm
from .contrato import Erro, erro
from .documento import artifact_ref, document, link_targets, read_document, utc_datetime
from .http import get_session

SYNC, INDEX, LOG = "sync.md", "index.md", "log.md"
HEADER = "| Artefato | Pasta | Última atualização ELM | Hash | Status |"
QUEUE, UPLOAD = ("novo", "desatualizado", "erro"), ("atualizado", "normalizado")
LIST_PASSES = 3  # releituras se a listagem ordenada não alcançar o oslc:totalCount (índice instável)
WORKERS = 8  # o tempo é latência do servidor, não CPU
_CELL = re.compile(r"(?<!\\)\|")
_LINKED = re.compile(r"\[(\d+) — (.*)\]\(</(.*)>\)")
_PLAIN = re.compile(r"(\d+) — (.*)")


# --- formato e disco

def _esc(text: str) -> str:
    return re.sub(r"([|\[\]])", r"\\\1", text)


def _label(row: dict) -> str:
    label = f"{row['id']} — {_esc(row['title'])}"
    return f"[{label}](</{row['path']}>)" if row.get("path") else label


def read_sync(root: str) -> dict[str, dict]:
    """Linhas do sync.md por id: {id, title, folder, path, elm (modified no último download), hash, status}."""
    try:
        with open(os.path.join(root, SYNC), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return {}
    rows = {}
    for line in lines:
        cells = [c.strip() for c in _CELL.split(line.strip())[1:-1]]
        if len(cells) == 5 and (m := _LINKED.fullmatch(cells[0]) or _PLAIN.fullmatch(cells[0])):
            unesc = lambda t: re.sub(r"\\([|\[\]])", r"\1", t)  # noqa: E731
            rows[m.group(1)] = {"id": m.group(1), "title": unesc(m.group(2)),
                                "path": m.group(3) if m.re is _LINKED else None, "folder": unesc(cells[1]),
                                "elm": cells[2], "hash": cells[3], "status": cells[4]}
    return rows


def _order(rows: dict[str, dict]) -> list[dict]:
    return sorted(rows.values(), key=lambda r: (r["folder"], int(r["id"])))


def write_sync(root: str, rows: dict[str, dict]) -> None:
    head = {"type": "Relatório de Sincronismo", "title": "Sincronismo ELM → OKF",
            "description": "Situação de cada artefato do DOORS Next baixado neste bundle. 'normalizado': o md"
                           " não foi editado; é o ELM que foge da regra do bundle (artefato do bundle = embed, o"
                           " resto = link), invisível no md, e o upload corrige.",
            "generated": {"by": rm.gerador(), "at": utc_datetime(datetime.now(timezone.utc))}}
    table = [HEADER, "|---|---|---|---|---|"] + [
        f"| {_label(r)} | {_esc(r['folder'])} | {r['elm']} | {r['hash']} | {r['status']} |" for r in _order(rows)]
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, SYNC), "w", encoding="utf-8") as f:
        f.write(document(head, "\n".join(table)))


def file_hash(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return ""


def _head(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return read_document(f.read())[0] or {}
    except (OSError, ValueError):
        return {}


def write_index(root: str, rows: dict[str, dict]) -> None:
    """OKF §8: um index.md por diretório, artefatos agrupados por `type` + Subdiretórios; só o da raiz tem
    frontmatter e cita o sync.md. Apaga index.md de diretório sem artefato."""
    files: dict[str, list[dict]] = {}
    for r in _order(rows):
        if r.get("path"):
            files.setdefault(posixpath.dirname(r["path"]), []).append(r)
    dirs = {""}
    for d in files:
        while d:
            dirs.add(d)
            d = posixpath.dirname(d)
    for d in sorted(dirs):
        out = ['---\nokf_version: "0.2"\n---\n'] if d == "" else []
        groups: dict[str, list[str]] = {}
        for r in files.get(d, []):
            head = _head(os.path.join(root, r["path"]))
            line = f"* [{r['id']} — {_esc(r['title'])}](<{posixpath.basename(r['path'])}>)"
            groups.setdefault(str(head.get("type") or "Outros"), []).append(
                line + (f" - {head['description']}" if head.get("description") else ""))
        if d == "":
            groups.setdefault("Relatório de Sincronismo", []).append(
                "* [Sincronismo ELM → OKF](sync.md) - Situação de cada artefato do DOORS Next baixado neste bundle.")
        out += [f"# {typ}\n\n" + "\n".join(lines) + "\n" for typ, lines in groups.items()]
        if subs := sorted(s for s in dirs if s and posixpath.dirname(s) == d):
            count = lambda s: sum(len(v) for k, v in files.items() if k == s or k.startswith(s + "/"))  # noqa: E731
            out.append("# Subdiretórios\n\n" + "\n".join(
                f"* [{_esc(posixpath.basename(s))}](<{posixpath.basename(s)}/index.md>) - {count(s)} artefato(s)"
                for s in subs) + "\n")
        os.makedirs(os.path.join(root, d), exist_ok=True)
        with open(os.path.join(root, d, INDEX), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
    for old in glob.glob(os.path.join(glob.escape(root), "**", INDEX), recursive=True):
        if os.path.relpath(os.path.dirname(old), root).replace(os.sep, "/") not in dirs | {"."}:
            os.remove(old)


def append_log(root: str, kind: str, rows: list[dict]) -> None:
    """'* **<kind>**: <artefatos>' no topo da seção '## <hoje>' do log.md (datas da mais nova à mais antiga)."""
    if not rows:
        return
    path = os.path.join(root, LOG)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = "# Directory Update Log\n"
    today, new = f"## {date.today().isoformat()}", f"* **{kind}**: " + ", ".join(_label(r) for r in rows)
    head, _, rest = text.partition("\n## ")
    rest = "## " + rest if rest else ""
    rest = f"{today}\n{new}{rest[len(today):]}" if rest.startswith(today + "\n") else f"{today}\n{new}\n\n{rest}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{head.rstrip()}\n\n{rest.rstrip()}\n")


# --- operações

def _raiz(cfg: dict) -> str:
    download = rm._rm(cfg).get("download")
    if not download:
        raise Erro("config", "pa sem rm.download (pasta do bundle)", "rm.download")
    projeto = os.path.realpath(cfg["_projeto"])
    root = os.path.realpath(os.path.join(projeto, download))
    if not root.startswith(projeto + os.sep):
        raise Erro("config", "rm.download precisa ficar dentro do projeto", "rm.download")
    return root


def _pastas(cfg: dict) -> dict[str, str]:
    return {a["pasta"]: a["pasta-id"] for a in rm._rm(cfg)["artefatos"].values()}


def _listar_pasta(cfg: dict, pasta_id: str) -> tuple[int, list[dict]]:
    """(total do servidor, requisitos diretamente na pasta). Pagina por id (sem ordem, itens pulam de página) e
    repete a listagem unindo os ids até alcançar o total."""
    config = rm.stream(cfg)
    base, where = rm._base(cfg), oslc.where_eq("nav:parent", rm._url(rm.FOLDER, pasta_id))
    total = oslc.count(base, where=where, order_by="+dcterms:identifier", configuration=config)
    merged: dict[str, dict] = {}
    for _ in range(LIST_PASSES):
        for r in oslc.query(base, where=where, select="dcterms:identifier,dcterms:title,dcterms:modified",
                            order_by="+dcterms:identifier", limit=None, configuration=config):
            merged[r["id"]] = {"id": r["id"], "title": " ".join((r["title"] or "").split()),
                               "modified": utc_datetime(r["properties"].get("dcterms:modified"))}
        if len(merged) >= total:
            break
    return total, list(merged.values())


def _remove(root: str, path: str | None) -> None:
    if path and os.path.realpath(os.path.join(root, path)).startswith(root + os.sep):
        try:
            os.remove(os.path.join(root, path))
        except FileNotFoundError:
            pass


def _ints(ids) -> list[int]:
    return [int(i) for i in ids]


def _in_order(rows: dict[str, dict], statuses: tuple[str, ...]) -> list[str]:
    return [r["id"] for r in _order(rows) if r["status"].startswith(statuses)]


def plano(cfg: dict) -> dict:
    """Inventário: lista cada pasta do pa, compara com o sync.md e o regrava."""
    root = _raiz(cfg)
    rows = read_sync(root)
    listed, inconsistentes, moved = {}, [], []
    for nome, pasta_id in _pastas(cfg).items():
        total, items = _listar_pasta(cfg, pasta_id)
        if total != len(items):
            inconsistentes.append(nome)
        listed.update({i["id"]: {**i, "folder": nome} for i in items})
    for rid, item in listed.items():
        row = rows.get(rid)
        if row is None or not row["hash"]:
            rows[rid] = {"id": rid, "title": item["title"], "folder": item["folder"], "path": None, "elm": "",
                         "hash": "", "status": "novo"}
            continue
        local = row["status"] in ("atualizado", "conflito") or bool(row["path"]) and file_hash(
            os.path.join(root, row["path"])) != row["hash"]
        if row["folder"] != item["folder"] and not local:  # mudou de pasta: sai da antiga, entra como novo
            _remove(root, row["path"])
            moved.append({**row, "path": None})
            row.update(path=None, elm="", hash="", status="novo")
        elif row["folder"] != item["folder"]:
            row["status"] = "conflito"
        else:
            elm = (item["modified"] or "") != row["elm"] or row["status"].startswith(("erro", "desatualizado"))
            row["status"] = ("conflito" if local and elm else "atualizado" if local else "desatualizado" if elm
                             else "normalizado" if row["status"] == "normalizado" else "sincronizado")
        row.update(title=item["title"], folder=item["folder"])
    # fora de toda pasta = removido (arquivo apagado), mas só se a listagem foi completa
    removidos = [] if inconsistentes else [r for rid, r in rows.items() if rid not in listed]
    for r in removidos:
        _remove(root, r["path"])
        del rows[r["id"]]
    write_sync(root, rows)
    if removidos or moved or not os.path.exists(os.path.join(root, INDEX)):
        write_index(root, rows)
    append_log(root, "Deletion", [{**r, "path": None} for r in removidos])
    append_log(root, "Move", moved)
    out = {"baixar": _ints(_in_order(rows, QUEUE)), "enviar": _ints(_in_order(rows, UPLOAD)),
           "conflitos": [{"id": int(rid), "titulo": rows[rid]["title"], "caminho": rows[rid]["path"]}
                         for rid in _in_order(rows, ("conflito",))]}
    if removidos:
        out["removidos"] = _ints(r["id"] for r in removidos)
    if inconsistentes:
        out["inconsistentes"] = inconsistentes
    return out


def _escrever(cfg: dict, rid: str, root: str, bundle_ids: set[str], artefatos: dict) -> dict:
    """Grava '<root>/<pasta>/<id>-<slug>.md' e apaga outro '<id>-*.md' (título ou pasta mudou)."""
    doc = rm.documento(cfg, rid, bundle_ids, artefatos)
    target = os.path.realpath(os.path.join(root, doc["caminho"]))
    if not target.startswith(root + os.sep):
        raise Erro("entrada", f"caminho fora do bundle: {doc['caminho']}")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(rm.okf(doc))
    for old in glob.glob(os.path.join(glob.escape(root), "**", f"{rid}-*.md"), recursive=True):
        if os.path.realpath(old) != target:
            os.remove(old)
    r = doc["recurso"]
    return {"title": " ".join((r["title"] or "").split()), "path": doc["caminho"],
            "elm": utc_datetime(r["properties"].get("dcterms:modified")) or "", "hash": file_hash(target),
            "status": "normalizado" if doc["normalizado"] else "sincronizado"}


def _lote(ids: list[str], fn) -> list:
    def safe(rid):
        try:
            return fn(rid)
        except Exception as exc:  # um id com erro não derruba o lote
            if (e := erro(exc)) is None:
                raise
            return e
    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(safe, ids))


def baixar(cfg: dict, ids: list | None = None) -> dict:
    """Sem ids: a fila (novo, desatualizado, erro). Com ids: esses, inclusive conflito (fica a versão do ELM)."""
    root = _raiz(cfg)
    rows = read_sync(root)
    ids = [str(i) for i in ids] if ids else _in_order(rows, QUEUE)
    bundle_ids, artefatos = set(rows) | set(ids), {}
    feitos, erros, criados, atualizados = [], [], [], []
    for rid, result in zip(ids, _lote(ids, lambda rid: _escrever(cfg, rid, root, bundle_ids, artefatos))):
        row = rows.get(rid) or {"id": rid, "title": rid, "folder": "", "path": None, "elm": "", "hash": ""}
        if isinstance(result, Erro):
            row["status"] = f"erro: {' '.join(result.detalhe.replace('|', '/').split())}"
            erros.append({"id": int(rid), "detalhe": result.detalhe})
        else:
            (atualizados if row["hash"] else criados).append(row)
            row.update(result, folder=row["folder"] or posixpath.dirname(result["path"]))
            feitos.append(rid)
        rows[rid] = row
    write_sync(root, rows)
    append_log(root, "Creation", criados)
    append_log(root, "Update", atualizados)
    if not _in_order(rows, QUEUE):
        write_index(root, rows)
    return {"feitos": _ints(feitos), "conflitos": _ints(_in_order(rows, ("conflito",))),
            **({"erros": erros} if erros else {})}


def _embeds(cfg: dict, body: str, bundle_ids: set[str]) -> list[str]:
    """Ids dos artefatos do bundle citados no corpo: na subida viram embed; o resto, link."""
    ids = []
    for target in link_targets(body):
        ref = artifact_ref(target)
        if ref and not ref.isdigit():
            try:
                ref = oslc.get(get_session().url(ref), rm.stream(cfg))["id"]
            except RuntimeError:
                ref = None
        if ref in bundle_ids:
            ids.append(ref)
    return list(dict.fromkeys(ids))


def enviar(cfg: dict, ids: list | None = None) -> dict:
    """Sem ids: as linhas atualizado/normalizado, só se o ELM não mudou desde o download (senão viram conflito).
    Com ids: esses, sem checar (fica a versão do md). Depois rebaixa cada um."""
    root = _raiz(cfg)
    rows = read_sync(root)
    forcar = bool(ids)
    ids = [str(i) for i in ids] if ids else _in_order(rows, UPLOAD)
    if missing := [i for i in ids if i not in rows or not rows[i]["path"]]:
        raise Erro("entrada", f"fora do bundle: {', '.join(missing)}", "ids")
    artefatos = {}

    def push(rid: str):
        row = rows[rid]
        if not forcar:
            no_elm = utc_datetime(rm.recurso(cfg, rid)["properties"].get("dcterms:modified"))
            if no_elm != row["elm"]:
                return "conflito"
        with open(os.path.join(root, row["path"]), encoding="utf-8") as f:
            head, body = read_document(f.read())
        rm.atualizar(cfg, rid, head.get("title") or row["title"], body, _embeds(cfg, body, set(rows)))
        return _escrever(cfg, rid, root, set(rows), artefatos)

    feitos, conflitos, erros = [], [], []
    for rid, result in zip(ids, _lote(ids, push)):
        if result == "conflito":
            rows[rid]["status"] = "conflito"
            conflitos.append(rid)
        elif isinstance(result, Erro):  # a linha fica como está: a edição do md não se perde
            erros.append({"id": int(rid), "detalhe": result.detalhe})
        else:
            rows[rid].update(result)
            feitos.append(rid)
    write_sync(root, rows)
    if feitos:  # o título pode ter mudado
        write_index(root, rows)
    append_log(root, "Upload", [rows[rid] for rid in feitos])
    return {"feitos": _ints(feitos), "conflitos": _ints(conflitos), **({"erros": erros} if erros else {})}


def sync(cfg: dict, direcao: str, ids: list | None = None) -> dict:
    if direcao == "plano":
        return plano(cfg)
    if direcao in ("baixar", "enviar"):
        return (baixar if direcao == "baixar" else enviar)(cfg, ids)
    raise Erro("entrada", f"direcao inválida: {direcao} (plano, baixar, enviar)", "direcao")
