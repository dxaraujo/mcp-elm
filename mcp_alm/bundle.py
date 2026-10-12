"""Bundle OKF da documentação do RM (alm-sync): o `sync.md` (fila e situação de cada artefato),
os `index.md` (raiz e um por diretório) e o `log.md` (o que cada sync mudou).
Só formato e disco; as tools que os usam estão em rm.py."""
from __future__ import annotations

import glob
import hashlib
import os
import posixpath
import re
from datetime import date, datetime, timezone

from .infra.document import document, read_document, utc_datetime

SYNC, INDEX, LOG = "sync.md", "index.md", "log.md"
HEADER = "| Artefato | Pasta | Última atualização ALM | Hash | Status |"
# linhas a baixar e a subir para o ALM
QUEUE = ("novo", "desatualizado", "erro")
UPLOAD = ("atualizado", "normalizado")
_CELL = re.compile(r"(?<!\\)\|")
_LINKED = re.compile(r"\[(\d+) — (.*)\]\(</(.*)>\)")
_PLAIN = re.compile(r"(\d+) — (.*)")


def _esc(text: str) -> str:
    return re.sub(r"([|\[\]])", r"\\\1", text)


def _unesc(text: str) -> str:
    return re.sub(r"\\([|\[\]])", r"\1", text)


def _label(row: dict) -> str:
    label = f"{row['id']} — {_esc(row['title'])}"
    return f"[{label}](</{row['path']}>)" if row.get("path") else label


def read_sync(dest: str) -> dict[str, dict]:
    """Linhas do `sync.md` por id: {id, title, folder, path (None se não baixado), alm, hash, status}.
    `alm` = dcterms:modified do ALM no último download/upload; `hash` = sha256 do arquivo gravado."""
    try:
        with open(os.path.join(dest, SYNC), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return {}
    rows = {}
    for line in lines:
        cells = [c.strip() for c in _CELL.split(line.strip())[1:-1]]
        if len(cells) != 5:
            continue
        m = _LINKED.fullmatch(cells[0]) or _PLAIN.fullmatch(cells[0])
        if not m:  # cabeçalho e separador
            continue
        rows[m.group(1)] = {"id": m.group(1), "title": _unesc(m.group(2)),
                            "path": m.group(3) if m.re is _LINKED else None, "folder": _unesc(cells[1]),
                            "alm": cells[2], "hash": cells[3], "status": cells[4]}
    return rows


def _order(rows: dict[str, dict]) -> list[dict]:
    return sorted(rows.values(), key=lambda r: (r["folder"], int(r["id"])))


def write_sync(dest: str, rows: dict[str, dict], by: str) -> None:
    head = {"type": "Relatório de Sincronismo", "title": "Sincronismo ALM → OKF",
            "description": "Situação de cada artefato do DOORS Next baixado neste bundle. 'normalizado': o md"
                           " não foi editado; é o ALM que foge da regra do bundle (artefato do bundle = embed, o"
                           " resto = link), invisível no md, e o upload corrige.",
            "generated": {"by": by, "at": utc_datetime(datetime.now(timezone.utc))}}
    table = [HEADER, "|---|---|---|---|---|"] + [
        f"| {_label(r)} | {_esc(r['folder'])} | {r['alm']} | {r['hash']} | {r['status']} |" for r in _order(rows)]
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, SYNC), "w", encoding="utf-8") as f:
        f.write(document(head, "\n".join(table)))


def file_hash(path: str) -> str:
    """sha256 do arquivo; '' se não existir."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return ""


def _head(path: str) -> dict:
    """Frontmatter de um arquivo baixado; {} se ilegível."""
    try:
        with open(path, encoding="utf-8") as f:
            return read_document(f.read())[0] or {}
    except (OSError, ValueError):
        return {}


def write_index(dest: str, rows: dict[str, dict]) -> None:
    """OKF §8 (como o build_index do okf-kit): um index.md por diretório com os artefatos dele agrupados por `type`
    e a seção Subdiretórios; só o da raiz tem frontmatter e lista o `sync.md`. Apaga index.md de diretório sem
    artefato (pasta esvaziada ou removida)."""
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
            head = _head(os.path.join(dest, r["path"]))
            label = f"{r['id']} — {_esc(r['title'])}"
            line = f"* [{label}](<{posixpath.basename(r['path'])}>)"
            groups.setdefault(str(head.get("type") or "Outros"), []).append(
                line + (f" - {head['description']}" if head.get("description") else ""))
        if d == "":
            groups.setdefault("Relatório de Sincronismo", []).append(
                "* [Sincronismo ALM → OKF](sync.md) - Situação de cada artefato do DOORS Next baixado neste bundle.")
        out += [f"# {typ}\n\n" + "\n".join(lines) + "\n" for typ, lines in groups.items()]
        subs = sorted(s for s in dirs if s and posixpath.dirname(s) == d)
        if subs:
            count = lambda s: sum(len(v) for k, v in files.items() if k == s or k.startswith(s + "/"))
            out.append("# Subdiretórios\n\n" + "\n".join(
                f"* [{_esc(posixpath.basename(s))}](<{posixpath.basename(s)}/index.md>) - {count(s)} artefato(s)"
                for s in subs) + "\n")
        os.makedirs(os.path.join(dest, d), exist_ok=True)
        with open(os.path.join(dest, d, INDEX), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
    for old in glob.glob(os.path.join(glob.escape(dest), "**", INDEX), recursive=True):
        if os.path.relpath(os.path.dirname(old), dest).replace(os.sep, "/") not in dirs | {"."}:
            os.remove(old)


def log_line(kind: str, rows: list[dict]) -> str | None:
    """Item do log.md: '* **<kind>**: <artefatos>' (link para o arquivo se ele existe); None sem artefatos."""
    return f"* **{kind}**: " + ", ".join(_label(r) for r in rows) if rows else None


def append_log(dest: str, lines: list[str | None]) -> None:
    """Acrescenta os itens no topo da seção '## <hoje>' do log.md (OKF: datas da mais nova para a mais antiga)."""
    lines = [line for line in lines if line]
    if not lines:
        return
    path = os.path.join(dest, LOG)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = "# Directory Update Log\n"
    today = f"## {date.today().isoformat()}"
    head, _, rest = text.partition("\n## ")
    rest = "## " + rest if rest else ""
    new = "\n".join(lines)
    rest = (f"{today}\n{new}{rest[len(today):]}" if rest.startswith(today + "\n")
            else f"{today}\n{new}\n\n{rest}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{head.rstrip()}\n\n{rest.rstrip()}\n")
