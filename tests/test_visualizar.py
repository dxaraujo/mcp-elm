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
    assert 'id="arr-esq"' in html and 'id="arr-dir"' in html
    # seletor de galáxia no header, à esquerda do combobox de modo; o painel flutuante saiu
    assert 'Todas as galáxias' in html and html.index('id="gal"') < html.index('id="modo"')
    assert 'id="gals"' not in html and "arrastarCaixa" not in html
    # largura limitada: sem max-width o <select> dimensiona para a opção mais larga e estica o header
    assert "max-width:min(300px, 32vw)" in html
    # carimbo de build na hint do 3D (o valor muda a cada geração: só o formato é asertado)
    assert re.search(r"build \d{2}/\d{2} \d{2}:\d{2}", html)

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


def test_explorar_header_sels_do_lado(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    html = open(d["path"], encoding="utf-8").read()
    cab = html[html.index("<header>"):html.index("</header>")]
    # os dois <select> do header ficam envoltos num .sels (flex): antes, o CSS inválido em '#'
    # derrubava a regra do #modo, que herdava width:100% e ocupava a linha inteira do header
    assert cab.index('class="sels"') < cab.index('id="gal"') < cab.index('id="modo"')
    assert cab.index("</select>", cab.index('id="modo"')) < cab.index("</span>", cab.index('id="modo"'))
    estilo = html[html.index("<style>"):html.index("</style>")]
    # o margin-left:auto foi para o .sels; #modo não estica (flex:none) e não divide linha
    assert "header .sels { display:flex; gap:10px; align-items:center; margin-left:auto; }" in estilo
    assert "#gal { width:auto; max-width:min(300px, 32vw); }" in estilo
    assert "#modo { width:auto; flex:none; }" in estilo
    # anotação com '#' não é comentário de CSS (sintaxe inválida derruba a regra seguinte): só /* */
    assert "# sem max-width" not in estilo
    assert "/* sem max-width, o select estica para a opção mais larga */" in estilo


def test_explorar_folga_entre_galaxias(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    html = open(d["path"], encoding="utf-8").read()
    # escala dos centros: distância entre centros ≥ 2,5·R_MAX → folga entre as bordas das duas maiores
    # ≈ metade do raio da maior (antes: 10u fixos, e no PAB as maiores quase se tocavam)
    assert "R_GAL = Math.max(120 + 26 * Math.sqrt(KGAL), (2 * R_MAX + 0.5 * R_MAX) / f);" in html
    assert "2·R_MAX + 10" not in html


def test_explorar_opacidade_da_web_de_links(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    html = open(d["path"], encoding="utf-8").read()
    # a web de "cita" atravessa a cena toda: perceptível, mas sem dominar (cores e largura intactas)
    assert ".linkOpacity(0.22)" in html
    assert ".linkOpacity(0.5)" not in html


def test_explorar_sol_maior_e_claro(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    html = open(d["path"], encoding="utf-8").read()
    # o sol é maior (raio 6,6u, antes 4,4u) e leva a variante mais clara do ângulo da galáxia
    assert "const valCore = () => Math.pow(6.6 / TAM, 3);" in html
    assert "solGal: new Map" in html
    # o core pinta por solGal; planetas e luas seguem usando corGal (e o cinza fixo)
    assert "G.solGal.get(n.core)" in html
    assert "G.corGal.get(n.core)" not in html


def test_explorar_sol_e_o_maior(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    html = open(d["path"], encoding="utf-8").read()
    # o sol é o maior nó da cena: raio dos planetas com teto 5,5 (UCs de alto grau chegavam a 9,3u,
    # maiores que o sol de 6,6u, e roubavam o centro da galáxia na visão global)
    assert "Math.pow(Math.min(5.5, 2 + 0.55 * Math.sqrt(d.n_links + d.n_chunks)) / TAM, 3)" in html


def test_explorar_raios_core_documento(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    html = open(d["path"], encoding="utf-8").read()
    # starburst: um raio core→documento por documento (estética; a casca esférica já posiciona tudo)
    assert 'target: "core:" + idxDoc(d), raio: true, rel: "raio"' in html
    # cor do raio: terceiro mapa por galáxia, tom entre o sol (76%) e os planetas (58%)
    assert "raioGal: new Map" in html
    assert "l.raio ? G.raioGal.get(c0)" in html
    # o raio não influencia a simulação: distância/força zero por-link (o d3 re-lê o accessor a cada graphData)
    assert "distance(l => l.raio ? 0 : (l.rel === \"sem\" ? 18 : 26)).strength(l => l.raio ? 0 : 0.2)" in html


def test_explorar_petalas_em_volume(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    html = open(d["path"], encoding="utf-8").read()
    # dente-de-leão em volume: raio externo ∝ cbrt(N) e cada pétala com o próprio raio-alvo, em
    # profundidades diferentes (antes: casca fina, todos no mesmo raio — o print mostrava 59,4u exato)
    assert "Math.cbrt(20 ** 3 + (g.docs * 2400 + REL[i] * 1200) / (4 / 3 * Math.PI))" in html
    assert "12 + 3 * Math.sqrt(g.docs - 1)" not in html
    assert "const rAlvo = Math.cbrt(20 ** 3 + (G.RAIO[i] ** 3 - 20 ** 3) * u);" in html
    # o raio cresce também com as relações internas da galáxia (links com as duas pontas nela):
    # antes só o nº de docs mandava e as pétalas grandes colidiam
    assert "const REL = gl.map(() => 0);" in html
    assert "if (a !== undefined && a === b) REL[a]++;" in html
    # direção pela espiral de Fibonacci (uniforme na esfera), não pelo hash por caminho (concentrava)
    assert "Math.acos(1 - 2 * (k + 0.5) / N)" in html
    # a mola é POSICIONAL: persegue direção × raio-alvo do nó. Mola só radial segurava o raio mas
    # deixava os links arrastarem todas as pétalas para a mesma direção (espinha); simulado com a
    # d3-force-3d real: 82% das pétalas num cone de 30° (Rbar 0,84)
    assert "n.vx += (c[0] + n.dx * n.rAlvo - n.x) * 0.35" in html
    assert "const dx = sp * Math.cos(th), dy = sp * Math.sin(th), dz = Math.cos(phi);" in html
    # a rotação gira a direção-alvo junto com a posição (senão a mola posicional trava a rotação)
    assert "epd = ex * ex0 + ey * ey0 + ez * ez0" in html


def test_explorar_sol_no_centro_da_esfera(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    html = open(d["path"], encoding="utf-8").read()
    # o kapsule cria forceCenter(0,0,0) por padrão: ele translada tudo a cada tick e, com a mola em
    # alvos absolutos, sobrava um desvio constante sol↔esfera (~125 u medidos no Chrome real — "peteca")
    assert 'g3d.d3Force("center", null);' in html


def test_explorar_modo_grafo(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    html = open(d["path"], encoding="utf-8").read()
    # terceiro modo: grafo convencional — só documentos + citações (sem sol, casca, raios ou luas);
    # a simulação padrão (link + charge) posiciona e a mola da casca nem é registrada
    assert '<option value="grafo">Grafo (convencional)</option>' in html
    assert '...D.links.map(l => ({ source: l.de, target: l.para, rel: l.rel })),' in html
    assert '...(D.sem || []).map(l => ({ source: l.de, target: l.para, rel: "sem", cos: l.cos })),' in html
    # as linhas ficam no padrão anterior (finas e discretas, 0,22 — como no dente-de-leão); só o guard
    # do digest fica: durante a troca de modo as pontas ainda são paths, não nós
    assert 'if (typeof s !== "object" || typeof t !== "object") return "#94a3b8";' in html
    assert 'g3d.linkOpacity(modo === "grafo" ? 0.55 : 0.22);' not in html
    # o payload leva as arestas semânticas calculadas do índice (k vizinhos por cosseno dos chunks)
    assert '"sem":[' in html
    assert 'g3d.d3Force("galaxia", modo === "grafo" ? null : forcaGalaxia);' in html
    assert 'g3d.d3Force("charge").strength(modo === "grafo" ? -30 : -6);' in html
    # selecionar no grafo não reconstrói o layout (não há luas) — a física não recomeça a cada clique
    assert 'if (modo !== "grafo") setDados3D();' in html


def test_explorar_raios_sem_particulas_e_luas_no_teto(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    html = open(d["path"], encoding="utf-8").read()
    # os fios do dente-de-leão ficam limpos: sem partícula azul animada nos raios (citações mantêm)
    assert 'l => (l.lua || l.raio || l.rel === "sem") ? 0 : 1' in html
    # as luas orbitam na superfície do planeta: mesmo teto de 5,5u (antes 9,3u no maior planeta)
    assert "const r = Math.min(5.5, 2 + 0.55 * Math.sqrt(d.n_links + d.n_chunks));" in html


def test_explorar_cameras_seguem_isolacao(projeto, raiz):
    call("elm_indexar_local", projeto)
    d = call("elm_explorar_local", projeto)
    html = open(d["path"], encoding="utf-8").read()
    # voo por rAF (sem libs): a câmera entra na galáxia ao isolar e volta à visão global ao soltar
    assert "function cameraNaGalaxia(i, ms = 800)" in html
    assert "function cameraGlobal(ms = 800)" in html
    assert "cancelAnimationFrame" in html and "requestAnimationFrame(passo)" in html
    na = html[html.index("function cameraNaGalaxia"):html.index("function cameraGlobal")]
    gl = html[html.index("function cameraGlobal"):html.index("function cameraNoPlaneta")]
    # no load, setIsolado(null) roda antes do init3D: o guard no-op é o que impede o crash
    assert "if (!g3d) return;" in na and "if (!g3d) return;" in gl
    # setIsolado move a câmera nos dois sentidos
    corpo = html[html.index("function setIsolado"):html.index("function repintar")]
    assert "cameraNaGalaxia(isolado)" in corpo and "cameraGlobal()" in corpo
    # sem ping-pong: planeta clicado com galáxia isolada usa centro/raio dela, não a distância global
    # (centroGal: centro fixo no dente-de-leão, centroide dos nós no grafo convencional)
    corpo = html[html.index("function cameraNoPlaneta"):html.index("function init3D")]
    assert "const { c, raio } = centroGal(isolado);" in corpo
    # distância global única: init3D e cameraGlobal usam a mesma função (não divergem no modo sem)
    assert "function distGlobal()" in html and "return 2.2 * Math.max(G.R_GAL + 90, 140);" in html
    assert html.count("z: distGlobal()") == 2
