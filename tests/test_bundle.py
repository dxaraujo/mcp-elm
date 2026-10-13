from mcp_elm import bundle


def test_sync_round_trip_escapes_title_and_keeps_unlinked_rows(tmp_path):
    rows = {"10": {"id": "10", "title": "[EXCLUIR] A | B", "folder": "03 Casos", "path": "03 Casos/10-excluir-a-b.md",
                   "elm": "2025-01-01T00:00:00Z", "hash": "ab12", "status": "sincronizado"},
            "2": {"id": "2", "title": "Novo", "folder": "01-Req", "path": None, "elm": "2025-01-01T00:00:00Z",
                  "hash": "", "status": "novo"}}
    bundle.write_sync(str(tmp_path), rows, "process:test")
    text = (tmp_path / "sync.md").read_text(encoding="utf-8")
    assert "type: Relatório de Sincronismo" in text
    assert "| [10 — \\[EXCLUIR\\] A \\| B](</03 Casos/10-excluir-a-b.md>) | 03 Casos |" in text
    assert text.index("| 2 — Novo |") < text.index("| [10 —")  # ordenado por pasta
    assert bundle.read_sync(str(tmp_path)) == rows


def test_index_per_directory_like_okf_kit(tmp_path):
    (tmp_path / "03 Casos" / "Sub").mkdir(parents=True)
    (tmp_path / "03 Casos" / "10-a.md").write_text('---\ntype: UC\ndescription: "Faz: A"\n---\ncorpo\n')
    (tmp_path / "03 Casos" / "Sub" / "11-b.md").write_text("---\ntype: UC\n---\ncorpo\n")
    (tmp_path / "Velha").mkdir()
    (tmp_path / "Velha" / "index.md").write_text("# UC\n")
    row = lambda rid, title, path: {"id": rid, "title": title, "folder": "f", "path": path, "elm": "", "hash": "",
                                    "status": "sincronizado"}
    rows = {"10": row("10", "A", "03 Casos/10-a.md"), "11": row("11", "B", "03 Casos/Sub/11-b.md"),
            "2": {**row("2", "C", None), "status": "novo"}}
    bundle.write_index(str(tmp_path), rows)
    root = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert root.startswith('---\nokf_version: "0.2"\n---\n')
    assert "* [Sincronismo ELM → OKF](sync.md)" in root
    assert "# Subdiretórios\n\n* [03 Casos](<03 Casos/index.md>) - 2 artefato(s)" in root
    casos = (tmp_path / "03 Casos" / "index.md").read_text(encoding="utf-8")
    assert casos.startswith("# UC\n\n* [10 — A](<10-a.md>) - Faz: A\n")
    assert "* [Sub](<Sub/index.md>) - 1 artefato(s)" in casos and "---" not in casos
    assert "* [11 — B](<11-b.md>)\n" in (tmp_path / "03 Casos" / "Sub" / "index.md").read_text(encoding="utf-8")
    assert not (tmp_path / "Velha" / "index.md").exists()


def test_log_newest_day_first_and_same_day_appends_on_top(tmp_path, monkeypatch):
    (tmp_path / "log.md").write_text("# Directory Update Log\n\n## 2020-01-01\n* **Creation**: x\n")
    r = {"id": "10", "title": "A", "path": "P/10-a.md"}
    bundle.append_log(str(tmp_path), [bundle.log_line("Creation", [r]), bundle.log_line("Update", [])])
    bundle.append_log(str(tmp_path), [bundle.log_line("Deletion", [{**r, "path": None}])])
    today = bundle.date.today().isoformat()
    assert (tmp_path / "log.md").read_text(encoding="utf-8") == (
        f"# Directory Update Log\n\n## {today}\n* **Deletion**: 10 — A\n* **Creation**: [10 — A](</P/10-a.md>)\n\n"
        "## 2020-01-01\n* **Creation**: x\n")
