"""Visualizador local (elm_explorar_local): clustering, embedding dos assets e gerar no bundle fake."""
import json
import re

import numpy as np
import pytest

from conftest import chamar
from mcp_elm import visualizar

from test_local import raiz  # fixture: bundle fake no disco (docs/alm)


def call(tool, projeto, **args):
    r = chamar(tool, projeto=projeto, **args)
    assert r["ok"], r
    return r["dados"]


def _unit(*vets):
    x = np.array(vets, dtype=np.float64)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


# ---------- aglomerar ----------

def test_aglomerar_funde_perto_e_separa_longe():
    # d(a,b)=0.2 < 0.3 (funde); c ortogonal a ambos (d=1) -> galáxia própria
    x = _unit([1, 0, 0, 0],
              [0.8, 0.6, 0, 0],
              [0, 0, 1, 0])
    assert visualizar.aglomerar(x, 0.3) == [0, 0, 1]


def test_aglomerar_distancias_simetricas_com_x_nao_quadrado():
    # o bug antigo usava X.T @ X (eixo errado quando n_docs != n_dims, matriz não quadrada)
    # d(b,c)=0.063 < 0.3 (funde); d({b,c},a)=0.33 > 0.3 (para)
    x = _unit([1, 0, 0, 0],
              [0.8, 0.6, 0, 0],
              [np.cos(1.0), np.sin(1.0), 0, 0])
    assert visualizar.aglomerar(x, 0.3) == [0, 1, 1]
    # com limite maior, o mesmo conjunto todo funde
    assert visualizar.aglomerar(x, 0.46) == [0, 0, 0]


def test_aglomerar_documento_nao_se_funde_com_ele_mesmo():
    # sem o fill_diagonal, o empata no 0 diagonal vinha antes do par duplicado (argmin por linha)
    x = _unit([1, 0],
              [1, 0])
    assert visualizar.aglomerar(x, 0.3) == [0, 0]
    x = _unit([1, 0],
              [0, 1],
              [1, 0])
    assert visualizar.aglomerar(x, 0.3) == [0, 1, 0]


def test_aglomerar_limite_e_casos_extremos():
    x = _unit([1, 0], [0, 1])
    assert visualizar.aglomerar(x, 0.3) == [0, 1]
    assert visualizar.aglomerar(x[:1], 0.3) == [0]
    assert visualizar.aglomerar(np.zeros((0, 2)), 0.3) == []


# ---------- _ler_asset ----------

def test_ler_asset_aplica_guard_de_layout():
    js = visualizar._ler_asset("3d-force-graph.min.js")
    assert js.count(visualizar._FORCA3D_NEW) == 1
    assert visualizar._FORCA3D_OLD not in js


def test_ler_asset_outro_arquivo_intacto():
    esperado = (visualizar.ASSETS / "marked.min.js").read_text(encoding="utf-8")
    assert visualizar._ler_asset("marked.min.js") == esperado


def test_ler_asset_sem_ponto_do_patch_erra(tmp_path, monkeypatch):
    fake = tmp_path / "assets"
    fake.mkdir()
    (fake / "3d-force-graph.min.js").write_text("const x = 1;", encoding="utf-8")
    monkeypatch.setattr(visualizar, "ASSETS", fake)
    with pytest.raises(RuntimeError, match="ponto do patch"):
        visualizar._ler_asset("3d-force-graph.min.js")


# ---------- gerar / tool ----------

def test_explorar_sem_indice_pede_indexar(projeto, raiz):
    r = chamar("elm_explorar_local", projeto=projeto)
    assert r["ok"] is False and r["erro"]["codigo"] == "precondicao" and r["erro"]["campo"] == "indice"
    assert "elm_indexar_local" in r["erro"]["detalhe"]


def test_explorar_gera_html_com_guard_e_dados_coerentes(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    UC, RN, LOGIN = ("03-Casos de Uso/2010-uc-cadastrar-cliente.md",
                     "03-Regras/2001-rn-validar-cpf-do-cliente.md",
                     "01-Requisitos/10-login-do-cliente.md")
    assert d["path"].endswith("docs/alm/explorar.html")
    assert (d["documentos"], d["links"]) == (3, 3)
    assert d["chunks"] >= 3 and 1 <= d["galaxias"] <= 3

    html = open(d["path"], encoding="utf-8").read()
    assert html.count(visualizar._FORCA3D_NEW) == 1  # o guard vai embutido, não o asset cru
    assert visualizar._FORCA3D_OLD not in html

    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    data = json.loads(m.group(1))  # "</" sai escapado (<\/): o JSON é válido
    assert [g["id"] for g in data["galaxias"]] == list(range(len(data["galaxias"])))
    assert sum(g["docs"] for g in data["galaxias"]) == 3
    assert all(doc["galaxia"] < len(data["galaxias"]) for doc in data["docs"])
    assert {(l["de"], l["para"]) for l in data["links"]} == {(UC, LOGIN), (UC, RN), (LOGIN, RN)}
