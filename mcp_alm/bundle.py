"""Bundle OKF da documentação do RM (alm-sync): o `sync.md` (fila e situação de cada artefato) e o `index.md`.
Só formato e disco; as tools que os usam estão em rm.py."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from .infra.document import document, utc_datetime

SYNC, INDEX = "sync.md", "index.md"
HEADER = "| Artefato | Pasta | Última atualização ALM | Generated OKF | Status |"
# linhas que ainda precisam ser baixadas
QUEUE = ("novo", "pendente", "erro")
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
    """Linhas do `sync.md` por id: {id, title, folder, path (None se não baixado), alm, okf, status}."""
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
                            "alm": cells[2], "okf": cells[3], "status": cells[4]}
    return rows


def _order(rows: dict[str, dict]) -> list[dict]:
    return sorted(rows.values(), key=lambda r: (r["folder"], int(r["id"])))


def write_sync(dest: str, rows: dict[str, dict], by: str) -> None:
    head = {"type": "Relatório de Sincronismo", "title": "Sincronismo ALM → OKF",
            "description": "Situação de cada artefato do DOORS Next baixado neste bundle.",
            "generated": {"by": by, "at": utc_datetime(datetime.now(timezone.utc))}}
    table = [HEADER, "|---|---|---|---|---|"] + [
        f"| {_label(r)} | {_esc(r['folder'])} | {r['alm']} | {r['okf']} | {r['status']} |" for r in _order(rows)]
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, SYNC), "w", encoding="utf-8") as f:
        f.write(document(head, "\n".join(table)))


def _description(path: str) -> str | None:
    """`description` do frontmatter de um arquivo baixado (só o cabeçalho é lido)."""
    try:
        with open(path, encoding="utf-8") as f:
            if f.readline().strip() != "---":
                return None
            for line in f:
                if line.strip() == "---":
                    return None
                if line.startswith("description: "):
                    value = line[len("description: "):].strip()
                    return json.loads(value) if value.startswith('"') else value
    except (OSError, ValueError):
        return None
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
