"""Visualizador local (elm_explorar_local): vizinhos semânticos e gerar no bundle fake."""
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


# ---------- vizinhos ----------

def test_vizinhos_k_mais_proximos_sem_duplicar_e_com_minimo():
    x = _unit([1, 0, 0], [0.9, 0.1, 0], [0, 1, 0], [0, 0, 1])
    sem = visualizar.vizinhos(["a", "b", "c", "d"], x, k=1, minimo=0.5)
    # a<->b é o par mútuo (uma aresta só); c e d não têm vizinho com cosseno ≥ 0,5
    assert [(l["de"], l["para"]) for l in sem] == [("a", "b")]
    assert sem[0]["cos"] == pytest.approx(float(x[0] @ x[1]), abs=1e-3)


def test_vizinhos_linha_zero_nunca_entra_e_casos_extremos():
    x = np.vstack([_unit([1, 0], [1, 0.1]), np.zeros((1, 2))])
    assert {(l["de"], l["para"]) for l in visualizar.vizinhos(["a", "b", "z"], x, k=2)} == {("a", "b")}
    assert visualizar.vizinhos(["a"], _unit([1, 0])) == []
    assert visualizar.vizinhos([], np.zeros((0, 2))) == []


# ---------- gerar / tool ----------

def test_explorar_sem_indice_pede_indexar(projeto, raiz):
    r = chamar("elm_explorar_local", projeto=projeto)
    assert r["ok"] is False and r["erro"]["codigo"] == "precondicao" and r["erro"]["campo"] == "indice"
    assert "elm_indexar_local" in r["erro"]["detalhe"]


def test_explorar_gera_html_simples_com_dois_conjuntos(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    UC, RN, LOGIN = ("03-Casos de Uso/2010-uc-cadastrar-cliente.md",
                     "03-Regras/2001-rn-validar-cpf-do-cliente.md",
                     "01-Requisitos/10-login-do-cliente.md")
    assert d["path"].endswith("docs/alm/explorar.html")
    assert (d["documentos"], d["links"]) == (3, 3) and d["chunks"] >= 3 and "galaxias" not in d

    html = open(d["path"], encoding="utf-8").read()
    # libs do CDN com versão fixa (nada embutido)
    assert re.findall(r'<script src="https://cdn\.jsdelivr\.net/npm/([^"]+)"', html) == [
        "3d-force-graph@1.80.1/dist/3d-force-graph.min.js", "marked@18.1.1/lib/marked.umd.js",
        "dompurify@3.4.17/dist/purify.min.js"]
    assert 'id="list"' in html and 'id="detail"' in html  # barras laterais
    assert re.search(r"build \d{2}/\d{2} \d{2}:\d{2}", html)
    # o grafo é o exemplo large-graph, sem galáxias
    assert "galáx" not in html.lower()
    # cor e tamanho fixos por tipo, legenda sobre o grafo, linhas discretas
    # o grafo segue as barras: filtro apaga o resto e enquadra; documento aberto foca nele e nos vizinhos
    assert "function repintar(enquadrar)" in html and "if (enquadrar) enquadrarNos(ativo);" in html
    assert "nodeVisibility(n => !foco || foco.has(n.id))" in html
    # fechar pelo hash (o replaceState lança SecurityError em file:// no Firefox e travava o painel)
    assert 'fechar.onclick = () => { location.hash = ""; };' in html and "replaceState" not in html
    assert "color: estilo(d.tipo)[2]" in html and ".linkOpacity(0.18)" in html and '"legenda"' in html
    cab = html[html.index("<header>"):html.index("</header>")]
    assert cab.count("<select") == 1
    assert '<option value="links">' in cab and '<option value="sem">' in cab

    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    data = json.loads(m.group(1))  # "</" sai escapado (<\/): o JSON é válido
    assert {(l["de"], l["para"]) for l in data["links"]} == {(UC, LOGIN), (UC, RN), (LOGIN, RN)}
    assert len(data["sem"]) == d["semanticos"]
    # chunks do índice por documento (painel direito), sem o prefixo de contexto do embedding
    assert sum(len(doc["chunks"]) for doc in data["docs"]) == d["chunks"]
    assert all(not t.startswith(doc["tipo"]) for doc in data["docs"] for _, t in doc["chunks"])
    assert "...listaChunks(d)" in html
    paths = {doc["path"] for doc in data["docs"]}
    assert all(l["de"] in paths and l["para"] in paths and l["de"] < l["para"] for l in data["sem"])
