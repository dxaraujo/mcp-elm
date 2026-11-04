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


# ---------- galaxias_docs ----------

def test_galaxias_docs_direta_saida_e_entrada():
    # aresta de saída do centro (UC→req) e de entrada (rn→UC) entram por qualquer rel
    arestas = {("uc/2010.md", "req/10.md"): ["Vincular"], ("rn/2001.md", "uc/2010.md"): ["Citar"]}
    nomes, grupos, centros = visualizar.galaxias_docs(
        ["uc/2010.md", "req/10.md", "rn/2001.md"],
        {"uc/2010.md": "UC"}, {"uc/2010.md": "Caso de Uso"}, arestas, {})
    assert nomes == ["UC"]
    assert grupos == [["uc/2010.md", "req/10.md", "rn/2001.md"]]
    assert centros == ["uc/2010.md"]


def test_galaxias_docs_semantica_artefato_a_artefato():
    # d = 1-cos(0,5) ≈ 0,122 ≤ 0,3 → membro; d = 1-cos(1,0) ≈ 0,46 → "Tudo"
    c, perto, longe, sem = "uc/2010.md", "el/1.md", "el/2.md", "el/3.md"
    x = _unit([1, 0, 0, 0],
              [np.cos(0.5), np.sin(0.5), 0, 0],
              [np.cos(1.0), np.sin(1.0), 0, 0],
              [0, 0, 1, 0])
    vetores = {c: x[0], perto: x[1], longe: x[2]}  # sem: sem vetor (não entra no dicionário)
    nomes, grupos, centros = visualizar.galaxias_docs(
        [c, perto, longe, sem], {c: "UC"}, {c: "Caso de Uso"}, {}, vetores)
    assert nomes == ["UC", "Tudo"]
    assert grupos == [[c, perto], [longe, sem]]
    assert centros == [c, None]


def test_galaxias_docs_direta_manda_sobre_semantica():
    # doc é semântico do centro B (d≈0,02) mas tem aresta para o centro A: a direta leva
    a, b, d = "a-uc.md", "b-uc.md", "doc.md"
    x = _unit([1, 0, 0, 0],
              [0, 1, 0, 0],
              [np.sin(0.2), np.cos(0.2), 0, 0])
    vetores = {a: x[0], b: x[1], d: x[2]}
    nomes, grupos, centros = visualizar.galaxias_docs(
        [a, b, d], {a: "A", b: "B"}, {a: "Caso de Uso", b: "Caso de Uso"},
        {(d, a): ["Vincular"]}, vetores)
    assert nomes == ["A", "B"]
    assert grupos == [[a, d], [b]]
    assert centros == [a, b]


def test_galaxias_docs_dois_centros_o_menor_path_puxa_o_membro():
    a, b, d = "a-uc.md", "b-uc.md", "doc.md"
    arestas = {(d, a): ["x"], (d, b): ["y"]}
    nomes, grupos, centros = visualizar.galaxias_docs(
        [a, b, d], {a: "A", b: "B"}, {a: "Caso de Uso", b: "Caso de Uso"}, arestas, {})
    # passadas nos centros por menor path: A (menor) pega o doc primeiro
    assert nomes == ["A", "B"]
    assert grupos == [[a, d], [b]]


def test_galaxias_docs_membro_que_tambem_e_centro_fica_na_propria():
    uc, hf = "uc/1.md", "hf/1.md"
    nomes, grupos, centros = visualizar.galaxias_docs(
        [uc, hf], {uc: "UC", hf: "HF"}, {uc: "Caso de Uso", hf: "História de Usuário"},
        {(uc, hf): ["Vincular"]}, {})
    assert nomes == ["UC", "HF"]
    assert grupos == [[uc], [hf]]
    assert centros == [uc, hf]


def test_galaxias_docs_sem_uc_tudo_recebe_tudo():
    x = _unit([1, 0], [0, 1])
    nomes, grupos, centros = visualizar.galaxias_docs(
        ["r/1.md", "r/2.md"], {}, {"r/1.md": "Regra", "r/2.md": "Regra"}, {},
        {"r/1.md": x[0], "r/2.md": x[1]})
    assert nomes == ["Tudo"]
    assert grupos == [["r/1.md", "r/2.md"]]
    assert centros == [None]


def test_galaxias_docs_deterministico():
    c, d1, d2, resto = "uc/1.md", "a/1.md", "b/1.md", "z/1.md"
    x = _unit([1, 0, 0, 0],
              [np.cos(0.2), np.sin(0.2), 0, 0],
              [np.cos(0.4), np.sin(0.4), 0, 0],
              [0, 0, 1, 0])
    kw = dict(paths=[c, d1, d2, resto], titulos={c: "UC"}, tipos={c: "Caso de Uso"},
              arestas={}, vetores={p: x[i] for i, p in enumerate([c, d1, d2, resto])})
    assert visualizar.galaxias_docs(**kw) == visualizar.galaxias_docs(**kw)


# ---------- galaxias_semanticas ----------

def test_galaxias_semanticas_centro_e_o_mais_central():
    # b é o mais central (maior média de cosseno com os demais) — não o primeiro da lista
    x = _unit([1, 0, 0, 0],
              [np.cos(0.3), np.sin(0.3), 0, 0],
              [np.cos(0.6), np.sin(0.6), 0, 0])
    nomes, grupos, centros = visualizar.galaxias_semanticas(
        ["a.md", "b.md", "c.md"], {"b.md": "B"}, x)
    assert grupos == [["a.md", "b.md", "c.md"]]
    assert centros == ["b.md"]
    assert nomes == ["B"]


def test_galaxias_semanticas_singleton_e_empate_menor_path():
    # c ortogonal (d=1): galáxia própria; a e b com vetor idêntico: empate na média → menor path
    x = _unit([1, 0, 0, 0],
              [1, 0, 0, 0],
              [0, 0, 1, 0])
    nomes, grupos, centros = visualizar.galaxias_semanticas(
        ["a.md", "b.md", "c.md"], {"a.md": "A", "c.md": "C"}, x)
    assert grupos == [["a.md", "b.md"], ["c.md"]]
    assert centros == ["a.md", "c.md"]
    assert nomes == ["A", "C"]


def test_galaxias_semanticas_particao_disjunta_deterministica():
    x = _unit([1, 0, 0, 0],
              [np.cos(0.2), np.sin(0.2), 0, 0],
              [0, 1, 0, 0],
              [0, 0, 1, 0])
    kw = dict(paths=["a.md", "b.md", "c.md", "d.md"], titulos={}, X=x)
    r = visualizar.galaxias_semanticas(**kw)
    assert r == visualizar.galaxias_semanticas(**kw)  # determinístico
    _, grupos, _ = r
    flat = [p for g in grupos for p in g]  # partição disjunta que cobre tudo
    assert sorted(flat) == kw["paths"] and len(set(flat)) == len(flat)


def test_galaxias_semanticas_sem_vetor_fica_singleton():
    # linha zero: distância 1 de tudo → galáxia própria; sem título: nome = path
    x = _unit([1, 0, 0], [0.8, 0.6, 0])
    x[1] = 0.0
    nomes, grupos, centros = visualizar.galaxias_semanticas(
        ["a.md", "b.md"], {"a.md": "A"}, x)
    assert grupos == [["a.md"], ["b.md"]]
    assert centros == ["a.md", "b.md"]
    assert nomes == ["A", "b.md"]


def test_galaxias_semanticas_vazio():
    assert visualizar.galaxias_semanticas([], {}, np.zeros((0, 2))) == ([], [], [])


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
    # só o UC é centro; RN e LOGIN têm relação direta com ele → uma galáxia, sem "Tudo"
    assert d["chunks"] >= 3 and d["galaxias"] == 1

    html = open(d["path"], encoding="utf-8").read()
    assert html.count(visualizar._FORCA3D_NEW) == 1  # o guard vai embutido, não o asset cru
    assert visualizar._FORCA3D_OLD not in html
    assert "cytoscape" not in html.lower()  # 2D foi removido em 3.6.5
    assert 'id="arr-esq"' in html and 'id="arr-dir"' in html and 'id="gals"' in html

    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    data = json.loads(m.group(1))  # "</" sai escapado (<\/): o JSON é válido
    assert [g["id"] for g in data["galaxias"]] == list(range(len(data["galaxias"])))
    assert sum(g["docs"] for g in data["galaxias"]) == 3
    assert all(doc["galaxia"] < len(data["galaxias"]) for doc in data["docs"])
    g = data["galaxias"][0]
    assert (g["nome"], g["docs"], g["centro"]) == ("UC - Cadastrar cliente", 3, UC)
    assert {(l["de"], l["para"]) for l in data["links"]} == {(UC, LOGIN), (UC, RN), (LOGIN, RN)}

    # modo semântico: gsem coerente com semantica (nº de clusters depende do embedding, não aserto exato)
    k = len(data["semantica"])
    assert k >= 1 and [g["id"] for g in data["semantica"]] == list(range(k))
    assert all(g["centro"] for g in data["semantica"])  # centro sempre um doc do cluster
    assert sum(g["docs"] for g in data["semantica"]) == len(data["docs"])  # união cobre tudo
    por_k = {}
    for doc in data["docs"]:
        assert 0 <= doc["gsem"] < k
        por_k.setdefault(doc["gsem"], []).append(doc["path"])
    for g in data["semantica"]:
        assert g["centro"] in por_k[g["id"]]  # centro pertence ao próprio cluster
        assert len(por_k[g["id"]]) == g["docs"]
    docs_s = [g["docs"] for g in data["semantica"]]
    assert docs_s == sorted(docs_s, reverse=True)  # maior cluster primeiro
    assert 'id="modo"' in html and "Galáxias semânticas" in html  # combobox de modo no header
