from mcp_alm import bundle


def test_sync_round_trip_escapes_title_and_keeps_unlinked_rows(tmp_path):
    rows = {"10": {"id": "10", "title": "[EXCLUIR] A | B", "folder": "03 Casos", "path": "03 Casos/10-excluir-a-b.md",
                   "alm": "2025-01-01T00:00:00Z", "hash": "ab12", "status": "sincronizado"},
            "2": {"id": "2", "title": "Novo", "folder": "01-Req", "path": None, "alm": "2025-01-01T00:00:00Z",
                  "hash": "", "status": "novo"}}
    bundle.write_sync(str(tmp_path), rows, "process:test")
    text = (tmp_path / "sync.md").read_text(encoding="utf-8")
    assert "type: Relatório de Sincronismo" in text
    assert "| [10 — \\[EXCLUIR\\] A \\| B](</03 Casos/10-excluir-a-b.md>) | 03 Casos |" in text
    assert text.index("| 2 — Novo |") < text.index("| [10 —")  # ordenado por pasta
    assert bundle.read_sync(str(tmp_path)) == rows


def test_index_lists_downloaded_with_description(tmp_path):
    (tmp_path / "03 Casos").mkdir()
    (tmp_path / "03 Casos" / "10-a.md").write_text('---\ntype: UC\ndescription: "Faz: A"\n---\ncorpo\n')
    rows = {"10": {"id": "10", "title": "A", "folder": "03 Casos", "path": "03 Casos/10-a.md", "alm": "", "hash": "",
                   "status": "sincronizado"},
            "2": {"id": "2", "title": "B", "folder": "01", "path": None, "alm": "", "hash": "", "status": "novo"}}
    bundle.write_index(str(tmp_path), rows)
    text = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert text.startswith('---\nokf_version: "0.2"\n---')
    assert "# 03 Casos\n\n* [10 — A](</03 Casos/10-a.md>) - Faz: A" in text and "2 — B" not in text
    assert "* [Sincronismo ALM → OKF](/sync.md)" in text
