"""Bundle OKF da documentação do RM (alm-sync): o `sync.md` (fila e situação de cada artefato) e o `index.md`.
Só formato e disco; as tools que os usam estão em rm.py."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone

from .infra.document import document, read_document, utc_datetime

SYNC, INDEX = "sync.md", "index.md"
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


def _description(path: str) -> str | None:
    """`description` do frontmatter de um arquivo baixado."""
    try:
        with open(path, encoding="utf-8") as f:
            return read_document(f.read())[0].get("description") or None
    except (OSError, ValueError):
        return None


def write_index(dest: str, rows: dict[str, dict]) -> None:
    """OKF §8: uma seção por pasta com os artefatos baixados, e a seção Bundle com o `sync.md`."""
    out, folder = ['---\nokf_version: "0.2"\n---'], None
    for r in _order(rows):
        if not r.get("path"):
            continue
        if r["folder"] != folder:
            folder = r["folder"]
            out.append(f"\n# {folder}\n")
        desc = _description(os.path.join(dest, r["path"]))
        out.append(f"* {_label(r)}" + (f" - {desc}" if desc else ""))
    out += ["\n# Bundle\n",
            "* [Sincronismo ALM → OKF](/sync.md) - Situação de cada artefato do DOORS Next baixado neste bundle."]
    with open(os.path.join(dest, INDEX), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
