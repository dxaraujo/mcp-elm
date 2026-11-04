"""Visualização do bundle local: <bundle>/explorar.html — o bundle em galáxias, num HTML autocontido.

Galáxia = um Caso de Uso (UC) ou História de Usuário (HF) no centro, reunindo os documentos com RELAÇÃO
DIRETA (link de entrada ou saída, qualquer rel) ou RELAÇÃO SEMÂNTICA (distância de cosseno ≤ LIMITE entre o
vetor do documento — média normalizada dos vetores dos chunks, artefato-a-artefato, nunca chunk a chunk — e o
do centro); a relação direta manda e cada documento entra em no máximo uma galáxia (centro de menor path); o
resto vai para a galáxia catch-all "Tudo" (bundle sem UC/HF: uma única "Tudo" com tudo). Planeta = documento
(tamanho proporcional a links + chunks); lua = chunk (orbita o planeta selecionado). No 3D, cada galáxia é
uma esfera: o centro fixo e os demais numa casca de raio ∝ sqrt dos membros (área da esfera ∝ nº de
membros), girando devagar em torno do core (eixo e período determinísticos por galáxia). O header tem o
seletor de galáxia (isolar uma — a câmera voa até ela; "Todas as galáxias" solta e a câmera volta à visão
global — o clique no core 3D sincroniza) e o combobox
do modo das galáxias: UC/HF (acima, padrão) ou semântico (aglomerativo, linkage média, cosseno ≤ LIMITE:
uma galáxia por cluster, centro = o documento com a maior média de cosseno com os demais membros). Os
documentos e
vetores saem do índice local em modo somente leitura (nada de reindexação aqui) e os links saem do disco com
a mesma lógica do elm_ler_req_local. As bibliotecas (3d-force-graph/three.js no grafo 3D — a 3D é só do
3d-force-graph, sem fallback 2D —, marked + DOMPurify no Markdown) ficam em assets/ e vão embutidas no HTML:
abre no navegador sem internet (intranet).
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import local
from .contrato import Erro

LIMITE = 0.3  # distância de cosseno: relação semântica entre documento e centro da galáxia
CENTROS = ("Caso de Uso", "História de Usuário")  # types que viram centro de galáxia
ASSETS = Path(__file__).parent / "assets"

# 3d-force-graph 1.73.4 tem um crash: d3ReheatSimulation() liga engineRunning antes do primeiro digest
# (debounce de 1 ms) atribuir state.layout, e o frame requestAnimationFrame seguinte faz tickFrame ->
# state.layout.tick() em undefined (a animação morre e a vista 3D fica em branco). Backport do fix oficial
# upstream (vasturiano/three-forcegraph@f89a2000ee, "Fix #45. Prevent layoutTick from running before
# layout is assigned", no 3d-force-graph 1.80.0), aplicado no embutir:
#   if (state.engineRunning) { layoutTick(); }  ->  if (state.engineRunning && state.layout) { layoutTick(); }
_FORCA3D_OLD = 'return t.engineRunning&&function(){'
_FORCA3D_NEW = 'return t.engineRunning&&t.layout&&function(){'


def _ler_asset(arquivo: str) -> str:
    texto = (ASSETS / arquivo).read_text(encoding="utf-8")
    if arquivo == "3d-force-graph.min.js":
        if texto.count(_FORCA3D_OLD) != 1:
            raise RuntimeError(f"asset {arquivo} sem o ponto do patch do guard de layout (a versão mudou?)")
        texto = texto.replace(_FORCA3D_OLD, _FORCA3D_NEW)
    return texto


def _indice(root: str) -> sqlite3.Connection:
    """Conexão em modo somente leitura no índice; sem índice (ou vazio): precondicao apontando o elm_indexar_local."""
    path = local.db_file(root)
    if not path.is_file():
        raise Erro("precondicao", f"sem índice em {path.parent.name}/{path.name}: rode elm_indexar_local antes "
                    "de gerar a visualização", "indice")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    if not con.execute("select count(*) from docs").fetchone()[0]:
        con.close()
        raise Erro("precondicao", "índice vazio (nada indexado): rode elm_indexar_local antes de gerar a "
                    "visualização", "indice")
    return con


def _vetores(con: sqlite3.Connection) -> dict[str, np.ndarray]:
    """Média normalizada dos vetores dos chunks, por documento (o vetor do planeta)."""
    por_doc: dict[str, list[np.ndarray]] = {}
    for path, vec in con.execute("select path, vec from chunks order by path, cid"):
        por_doc.setdefault(path, []).append(np.frombuffer(vec, dtype=np.float32))
    out = {}
    for path, vs in por_doc.items():
        m = np.stack(vs).mean(axis=0)
        norma = np.linalg.norm(m)
        out[path] = m / norma if norma else m
    return out


def aglomerar(X: np.ndarray, limite: float) -> list[int]:
    """Agregativo, linkage média, distância de cosseno; determinístico (empate no menor índice, ordem de
    aparição). Uma galáxia (0..k-1) por linha de X; para quando a menor distância excede `limite`."""
    n = X.shape[0]
    if n <= 1:
        return list(range(n))
    S = X @ X.T
    D = np.clip(1.0 - (S + S.T) / 2.0, 0.0, 2.0)  # distância de cosseno, simétrica exata
    tam = np.ones(n)
    ativo = np.ones(n, bool)
    pai = list(range(n))

    def achar(i: int) -> int:
        while pai[i] != i:
            pai[i] = pai[pai[i]]  # compressão de caminho
            i = pai[i]
        return i

    for _ in range(n - 1):
        m = np.where(ativo[:, None] & ativo[None, :], D, np.inf)
        np.fill_diagonal(m, np.inf)  # o documento não se funde com ele mesmo
        i, j = np.unravel_index(m.argmin(), (n, n))
        if m[i, j] > limite:
            break
        i, j = sorted((int(i), int(j)))  # o menor índice segue como representante
        ls = np.where(ativo)[0]
        ls = ls[ls != i]  # (a distância de j, agora inativo, não conta mais)
        D[i, ls] = D[ls, i] = (tam[i] * D[i, ls] + tam[j] * D[j, ls]) / (tam[i] + tam[j])
        tam[i] += tam[j]
        ativo[j] = False
        pai[j] = i
    nomes: dict[int, int] = {}
    out = []
    for k in range(n):
        r = achar(k)
        if r not in nomes:
            nomes[r] = len(nomes)
        out.append(nomes[r])
    return out


def galaxias_docs(paths: list[str], titulos: dict[str, str], tipos: dict[str, str | None],
                  arestas: dict[tuple[str, str], list[str]], vetores: dict[str, np.ndarray],
                  limite: float = LIMITE) -> tuple[list[str], list[list[str]], list[str | None]]:
    """Atribui cada documento a UMA galáxia (função pura, determinística).

    Centro = documento cujo type é UC ou HF, na ordem estável de `paths` (menor path primeiro); o nome da
    galáxia é o título do centro. Membro = documento (que não seja centro) com RELAÇÃO DIRETA com o centro
    (aresta de saída OU de entrada, qualquer rel) OU RELAÇÃO SEMÂNTICA (cosseno ≤ limite entre o vetor do
    documento e o do centro — artefato-a-artefato, nunca chunk a chunk). A direta manda: as duas passadas
    (direta e depois semântica) rodam nos centros por menor path. O resto vai para a galáxia catch-all
    "Tudo", omitida se vazia; sem nenhum UC/HF, "Tudo" recebe tudo. → (nomes, grupos, centros).
    """
    centros = [p for p in paths if tipos.get(p) in CENTROS]
    if not centros:
        return ["Tudo"], [list(paths)], [None]
    grupos: dict[str, list[str]] = {c: [c] for c in centros}
    atribuido = set(centros)
    for c in centros:  # passada 1: relação direta (manda sobre a semântica)
        for p in paths:
            if p not in atribuido and ((c, p) in arestas or (p, c) in arestas):
                grupos[c].append(p)
                atribuido.add(p)
    for c in centros:  # passada 2: relação semântica, só o que ficou fora
        vc = vetores.get(c)
        if vc is None:
            continue
        for p in paths:
            if p in atribuido:
                continue
            vp = vetores.get(p)
            if vp is not None and float(np.clip(1.0 - np.dot(vc, vp), 0.0, 2.0)) <= limite:
                grupos[c].append(p)
                atribuido.add(p)
    nomes = [titulos.get(c) or c for c in centros]
    grupos_l = [grupos[c] for c in centros]
    centros_l = list(centros)
    resto = [p for p in paths if p not in atribuido]
    if resto:
        nomes.append("Tudo")
        grupos_l.append(resto)
        centros_l.append(None)
    return nomes, grupos_l, centros_l


def galaxias_semanticas(paths: list[str], titulos: dict[str, str], X: np.ndarray) -> tuple[list[str], list[list[str]], list[str]]:
    """Semântico (aglomerativo, linkage média, cosseno ≤ LIMITE): uma galáxia por cluster; centro = o
    documento com a MAIOR MÉDIA de cosseno com os demais membros (singleton: ele mesmo; empate: o menor
    path) e nome = o título do centro. Sem "Tudo" (toda linha entra em um cluster; sem vetor: linha zero,
    distância 1 de tudo → singleton). Mesma forma de `galaxias_docs` → (nomes, grupos, centros)."""
    n = len(paths)
    if n == 0:
        return [], [], []
    rot = aglomerar(X, LIMITE)
    grupos: list[list[int]] = [[] for _ in range(max(rot) + 1)]
    for i, r in enumerate(rot):
        grupos[r].append(i)
    S = X @ X.T
    nomes, grupos_l, centros_l = [], [], []
    for g in grupos:
        if len(g) == 1:
            c = g[0]
        else:
            s = S[np.ix_(g, g)]
            media = (s.sum(axis=1) - np.diag(s)) / (len(g) - 1)  # média do cosseno com os demais
            c = g[int(np.argmax(media))]  # empate: o primeiro (g em ordem de path) → menor path
        centros_l.append(paths[c])
        nomes.append(titulos.get(paths[c]) or paths[c])
        grupos_l.append([paths[i] for i in g])
    return nomes, grupos_l, centros_l


def _pagina(dados: dict, build: str) -> str:
    js = json.dumps(dados, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = PAGINA
    for nome, arquivo in (("FORCA3D", "3d-force-graph.min.js"),
                          ("MARKED", "marked.min.js"), ("PURIFY", "purify.min.js")):
        html = html.replace(f"__{nome}__", _ler_asset(arquivo))
    return html.replace("__DATA__", js).replace("__BUILD__", build)  # build = carimbo na hint do 3D


def gerar(cfg: dict) -> dict:
    """Gera <bundle>/explorar.html. → {path, documentos, chunks, galaxias, links, gerado_em}"""
    root, disco = local._bundle(cfg)
    con = _indice(root)
    try:
        linhas = con.execute("select path, id, title, type, folder, description from docs order by path").fetchall()
        n_chunks = dict(con.execute("select path, count(*) from chunks group by path").fetchall())
        vetores = _vetores(con)
        chunk_rows = con.execute("select path, section, text from chunks order by path, cid").fetchall()
    finally:
        con.close()

    paths = [l[0] for l in linhas]
    indexados = set(paths)
    docs = {p: d for p, d in disco.items() if p in indexados}
    ids = local.by_id(docs)
    # links do disco (mesma lógica do local.links), só entre documentos indexados
    arestas: dict[tuple[str, str], list[str]] = {}
    for p in paths:
        d = docs.get(p)
        if d:
            for lk in local.links(d, docs, ids):
                arestas.setdefault((p, lk["path"]), []).append(lk["rel"])
    links = [{"de": a, "para": b, "rel": " · ".join(r)} for (a, b), r in sorted(arestas.items())]
    grau: dict[str, int] = {}
    for l in links:
        grau[l["de"]] = grau.get(l["de"], 0) + 1
        grau[l["para"]] = grau.get(l["para"], 0) + 1

    if len({v.shape[0] for v in vetores.values()}) > 1:  # índice com dois embedders: sem como comparar
        raise Erro("precondicao", "índice com vetores de dimensões diferentes: rode "
                    "elm_indexar_local (forcar)", "indice")
    # galáxias: uma por UC/HF (membros por relação direta ou semântica) + "Tudo" para o resto
    tipos = {l[0]: l[3] for l in linhas}
    titulo = {l[0]: (l[2] or "") for l in linhas}
    nomes, grupos, centros = galaxias_docs(paths, titulo, tipos, arestas, vetores)
    # maior galáxia primeiro (empate: o path menor), numerando de 0
    ordenado = sorted(zip(nomes, grupos, centros), key=lambda t: (-len(t[1]), min(t[1])))
    nomes, grupos, centros = [n for n, _, _ in ordenado], [m for _, m, _ in ordenado], [c for _, _, c in ordenado]
    gal_de: dict[str, int] = {}
    for gid, membros in enumerate(grupos):
        for p in membros:
            gal_de[p] = gid
    # modo semântico: uma galáxia por cluster (aglomerativo, linkage média); sem vetor: linha zero
    dim = next(iter(vetores.values()), np.zeros(0)).shape[0]
    X = np.stack([vetores.get(p, np.zeros(dim)) for p in paths]) if paths else np.zeros((0, dim))
    nomes_s, grupos_s, centros_s = galaxias_semanticas(paths, titulo, X)
    ordenado_s = sorted(zip(nomes_s, grupos_s, centros_s), key=lambda t: (-len(t[1]), min(t[1])))
    nomes_s, grupos_s, centros_s = (list(a) for a in zip(*ordenado_s))
    gsem_de: dict[str, int] = {}
    for k, membros in enumerate(grupos_s):
        for p in membros:
            gsem_de[p] = k

    docs_json = []
    for p, doc_id, doc_titulo, tipo, pasta, desc in linhas:
        d = docs.get(p)  # arquivo apagado depois do índice: sem corpo e sem links
        docs_json.append({"path": p, "id": doc_id, "titulo": doc_titulo or "", "tipo": tipo, "pasta": pasta,
                          "subpasta": p.rsplit("/", 1)[0] if "/" in p else None, "descricao": desc,
                          "n_chunks": n_chunks.get(p, 0), "n_links": grau.get(p, 0), "galaxia": gal_de[p],
                          "gsem": gsem_de[p], "corpo": d["body"] if d else ""})
    dados = {"bundle": os.path.basename(root),
             "docs": docs_json,
             "links": links,
             "chunks": [{"path": p, "secao": s, "texto": t} for p, s, t in chunk_rows],
             "galaxias": [{"id": gid, "nome": nomes[gid], "docs": len(m), "centro": centros[gid]}
                           for gid, m in enumerate(grupos)],
             "semantica": [{"id": k, "nome": nomes_s[k], "docs": len(m), "centro": centros_s[k]}
                            for k, m in enumerate(grupos_s)]}
    out = Path(root) / "explorar.html"
    agora = datetime.now(timezone.utc).astimezone()
    out.write_text(_pagina(dados, agora.strftime("%d/%m %H:%M")), encoding="utf-8")  # carimbo da geração na hint
    return {"path": str(out), "documentos": len(docs_json), "chunks": len(chunk_rows),
            "galaxias": len(grupos), "links": len(links), "gerado_em": agora.isoformat(timespec="seconds")}


PAGINA = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Galáxias do bundle</title>
<script>__FORCA3D__</script>
<script>__MARKED__</script>
<script>__PURIFY__</script>
<style>
:root { --bg:#fafaf9; --panel:#fff; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --accent:#2563eb; --chip:#f5f5f4; --code:#f5f5f4; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0c0a09; --panel:#1c1917; --fg:#f5f5f4; --muted:#a8a29e; --line:#292524; --accent:#60a5fa; --chip:#292524; --code:#0c0a09; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.5 system-ui, sans-serif; }
header { padding:10px 16px; border-bottom:1px solid var(--line); display:flex; flex-wrap:wrap; gap:8px 20px; align-items:center; }
header h1 { font-size:16px; margin:0; }
header .stat { color:var(--muted); font-size:13px; }
main { display:grid; grid-template-columns:var(--w-esq,290px) 1fr var(--w-dir,420px); height:calc(100vh - 47px); }
main.sem-detalhe { grid-template-columns:var(--w-esq,290px) 1fr 0; }
main.sem-detalhe #detail, main.sem-detalhe #arr-dir { display:none; }
main.sem-detalhe #graph { border-right:0; }
aside, section { overflow:auto; border-right:1px solid var(--line); background:var(--panel); }
#filters { padding:10px 12px; display:grid; gap:8px; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--panel); z-index:1; }
input, select { width:100%; padding:6px 8px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); font:inherit; }
header .sels { display:flex; gap:10px; align-items:center; margin-left:auto; }
#gal { width:auto; max-width:min(300px, 32vw); }  /* sem max-width, o select estica para a opção mais larga */
#modo { width:auto; flex:none; }
#conta { color:var(--muted); font-size:12px; }
#list { list-style:none; margin:0; padding:0; }
#list li { padding:8px 12px; border-bottom:1px solid var(--line); cursor:pointer; }
#list li:hover, #list li.on { background:var(--chip); }
#list .t { font-weight:600; }
#list .m { color:var(--muted); font-size:12px; }
#graph { position:relative; overflow:hidden; background:#070b16; border-right:1px solid var(--line); }
#g3d { position:absolute; inset:0; }
.arr { position:absolute; top:0; bottom:0; width:7px; cursor:col-resize; z-index:4; touch-action:none; }
.arr.esq { left:0; } .arr.dir { right:0; }
.arr.on { background:rgba(96,165,250,.35); }
#graph .off { position:absolute; inset:0; display:grid; place-items:center; color:var(--muted); padding:16px; text-align:center; }
#graph .hint { position:absolute; right:10px; bottom:8px; color:#94a3b8; font-size:12px; pointer-events:none; }
#detail { padding:16px 20px; border-right:0; }
#detail .topo { display:flex; gap:8px; align-items:flex-start; justify-content:space-between; }
#detail h2.title { font-size:18px; margin:0 0 4px; }
.fechar { border:1px solid var(--line); background:var(--bg); color:var(--fg); border-radius:6px; width:28px; height:28px;
  flex:none; cursor:pointer; font:16px/1 system-ui; }
.fechar:hover { background:var(--chip); }
#detail h3.sec { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:24px 0 6px; border-top:1px solid var(--line); padding-top:12px; }
#detail .m { color:var(--muted); }
#detail a { color:var(--accent); }
.meta { display:flex; flex-wrap:wrap; gap:4px 8px; align-items:center; margin-bottom:8px; }
.chip { background:var(--chip); border-radius:999px; padding:2px 8px; font-size:12px; }
.desc { color:var(--muted); font-style:italic; margin:8px 0; }
.link { display:block; padding:2px 0; text-decoration:none; }
.rel { color:var(--muted); font-size:12px; }
.chunk { display:block; width:100%; text-align:left; padding:3px 6px; margin:1px 0; border:0; border-radius:5px; background:var(--chip); color:var(--fg); font:inherit; font-size:13px; cursor:pointer; }
.chunk.on { outline:1px solid var(--accent); }
pre.chunktxt { margin:4px 0 8px; }
.md { overflow-wrap:anywhere; }
.md h1, .md h2, .md h3, .md h4 { line-height:1.25; margin:20px 0 8px; }
.md h1 { font-size:20px; } .md h2 { font-size:17px; } .md h3 { font-size:15px; } .md h4 { font-size:14px; }
.md table { border-collapse:collapse; display:block; overflow:auto; margin:8px 0; }
.md th, .md td { border:1px solid var(--line); padding:4px 8px; text-align:left; vertical-align:top; }
.md th { background:var(--chip); }
.md code { background:var(--code); padding:1px 4px; border-radius:4px; font-size:13px; }
.md pre { background:var(--code); padding:10px; border-radius:6px; overflow:auto; }
.md pre code { padding:0; }
.md blockquote { margin:8px 0; padding:0 12px; border-left:3px solid var(--line); color:var(--muted); }
.md img { max-width:100%; }
pre.raw { white-space:pre-wrap; font:13px/1.5 ui-monospace, monospace; }
@media (max-width: 900px) {
  main, main.sem-detalhe { grid-template-columns:1fr; height:auto; }
  aside, section { border-right:0; border-bottom:1px solid var(--line); max-height:60vh; }
  #graph { height:60vh; }
  #detail { max-height:none; }
  .arr { display:none; }
}
</style>
</head>
<body>
<header>
  <h1 id="name"></h1>
  <span class="stat" id="stats"></span>
  <span class="sels">
    <select id="gal" aria-label="Galáxia isolada"></select>
    <select id="modo" aria-label="Modo de galáxias">
      <option value="uc">Galáxias UC/HF</option>
      <option value="sem">Galáxias semânticas</option>
    </select>
  </span>
</header>
<main id="layout" class="sem-detalhe">
  <aside>
    <div id="filters">
      <input id="q" type="search" placeholder="Filtrar por título, id, descrição…" aria-label="Filtro">
      <select id="tipo" aria-label="Tipo de artefato"></select>
      <div id="conta"></div>
    </div>
    <ul id="list"></ul>
  </aside>
  <section id="graph" aria-label="Galáxias do bundle">
    <div id="g3d"></div>
    <div id="arr-esq" class="arr esq" title="Arraste para mudar a largura da lista" aria-hidden="true"></div>
    <div id="arr-dir" class="arr dir" title="Arraste para mudar a largura do painel" aria-hidden="true"></div>
  </section>
  <section id="detail"><p class="m">Selecione um documento na lista, no grafo ou numa lua.</p></section>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
// ---------- dados do bundle
const D = JSON.parse(document.getElementById("data").textContent);
const docs = D.docs;
const byPath = Object.fromEntries(docs.map(d => [d.path, d]));
const byId = Object.fromEntries(docs.filter(d => d.id).map(d => [d.id, d]));
// ---------- modo de galáxias: "uc" (UC/HF, padrão) ou "sem" (semântico); combobox no header, persiste
let modo = "uc";
try { const m0 = localStorage.getItem("explorar-modo"); if (m0 === "uc" || m0 === "sem") modo = m0; } catch {}
const idxDoc = d => (modo === "sem" ? d.gsem : d.galaxia);
// semente determinística (direção inicial dos planetas na casca e eixo/velocidade da rotação; em [-1, 1])
const semente = (s, k) => { let h = 2166136261 ^ s.length; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619); h = Math.imul(h ^ (h >>> (5 * k + 3)), 16777619); return ((h >>> 0) % 1000) / 500 - 1; };
let G = null;
function montarGalo() {
  const gl = (modo === "sem" ? D.semantica : D.galaxias);
  const KGAL = gl.length;
  // raio da casca ∝ sqrt dos membros: área da esfera (4πR²) ∝ nº de membros → cada nó recebe a mesma
  // área de superfície (na PAB a maior galáxia fica ~4× o raio do singleton, ~16× a área)
  const RAIO = gl.map(g => 12 + 3 * Math.sqrt(g.docs - 1));
  const R_MAX = KGAL ? Math.max(...RAIO) : 0;
  // rotação lenta da casca em torno do core: eixo unitário e período (20–60 s) derivados do índice da
  // galáxia (determinístico, estável entre gerações); a direção alterna entre galáxias vizinhas
  const ROT = gl.map((g, i) => {
    let ex = semente("rot" + i, 11), ey = semente("rot" + i, 12), ez = semente("rot" + i, 13);
    const m = Math.hypot(ex, ey, ez) || 1, periodo = 20 + 40 * (semente("rot" + i, 14) + 1) / 2;
    return { i, ex: ex / m, ey: ey / m, ez: ez / m, om: (i % 2 ? -1 : 1) * 2 * Math.PI / periodo };
  });
  // grade de Fibonacci dos centros: o termo base é o piso; se as maiores esferas não couberem entre
  // vizinhos, escala R_GAL por f (menor distância entre pares no layout de Fibonacci de raio 1,
  // medida abaixo) para distância entre centros ≥ 2,5·R_MAX → folga entre as bordas das duas maiores
  // galáxias ≈ metade do raio da maior (antes: 10u fixos) → nenhuma galáxia sobrepõe a vizinha
  let R_GAL = 0, CENTRO = [];
  if (KGAL === 1) CENTRO = [[0, 0, 0]];
  else if (KGAL > 1) {
    const u = gl.map((g, i) => {
      const phi = Math.acos(1 - 2 * (i + 0.5) / KGAL), th = Math.PI * (1 + Math.sqrt(5)) * (i + 0.5);
      return [Math.sin(phi) * Math.cos(th), Math.sin(phi) * Math.sin(th), Math.cos(phi)];
    });
    let f = Infinity;
    for (let i = 0; i < KGAL; i++) for (let j = i + 1; j < KGAL; j++) {
      const dx = u[i][0] - u[j][0], dy = u[i][1] - u[j][1], dz = u[i][2] - u[j][2];
      f = Math.min(f, Math.hypot(dx, dy, dz));
    }
    R_GAL = Math.max(120 + 26 * Math.sqrt(KGAL), (2 * R_MAX + 0.5 * R_MAX) / f);
    CENTRO = u.map(p => [p[0] * R_GAL, p[1] * R_GAL, p[2] * R_GAL]);
  }
  G = {
    list: gl,
    gal: Object.fromEntries(gl.map(g => [g.id, g])),
    KGAL, R_GAL,
    CENTRO,
    RAIO,
    ROT,
    porGal: gl.map(() => []),  // nós membros por galáxia, preenchido em setDados3D (a rotação usa)
    // cor fixa por galáxia (ângulo dourado); a catch-all "Tudo" (sem centro UC/HF) fica discreta
    corGal: new Map(gl.map((g, i) => [i, g.centro == null ? "hsl(215, 14%, 52%)" : `hsl(${Math.round(i * 137.508) % 360}, 62%, 58%)`]))
  };
}
montarGalo();
const out = {}, inc = {};
for (const l of D.links) { (out[l.de] ||= []).push(l); (inc[l.para] ||= []).push(l); }
const deDoc = {};
for (const c of D.chunks) (deDoc[c.path] ||= []).push(c);

// ---------- estado
let isolado = null, busca = "", tipoF = "", selecionado = null;  // isolado = id da galáxia (ou null)
let g3d = null, N3D = [], L3D = [];
const TAU = Math.PI * 2, TAM = 2.2, LUA_MAX = 40;  // TAM = nodeRelSize; LUA_MAX = luas por planeta
const t0 = performance.now();

// ---------- util
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const esc = s => { const e = document.createElement("span"); e.textContent = s ?? ""; return e.innerHTML; };
const rotulo = d => (d.id ? d.id + " — " : "") + d.titulo;
const docHash = p => "#doc=" + encodeURIComponent(p);
const matchBusca = d => !busca || [d.id, d.titulo, d.descricao, d.pasta, d.path, G.gal[idxDoc(d)].nome].some(x => x && String(x).toLowerCase().includes(busca));
const matchGal = i => !busca || String(G.gal[i].nome).toLowerCase().includes(busca);
const matchTipo = d => !tipoF || (d.tipo || "(sem tipo)") === tipoF;
const dimDoc = d => (!matchTipo(d) || !matchBusca(d)) || (isolado !== null && idxDoc(d) !== isolado);
const dimCore = i => !matchGal(i) || (isolado !== null && isolado !== i);

// ---------- isolamento por galáxia: seletor no header ⇄ clique no core 3D (estado único em `isolado`)
function setIsolado(i) {  // i = id da galáxia (ou null = "Todas as galáxias"); reconstrói as opções do modo ativo
  isolado = i;
  const sel = $("gal"), todas = el("option", "Todas as galáxias");
  todas.value = ""; sel.replaceChildren(todas);
  for (const g of G.list) { const o = el("option", `${g.nome} (${g.docs} doc${g.docs === 1 ? "" : "s"})`); o.value = g.id; sel.append(o); }
  sel.value = isolado === null ? "" : isolado;
  if (isolado !== null) cameraNaGalaxia(isolado); else cameraGlobal();  // a câmera acompanha o isolamento (no-op se g3d ainda não existe)
  repintar();
}
function repintar() {
  if (g3d) { g3d.nodeColor(nodeColor3); g3d.linkColor(linkColor3); }
}

// ---------- vista 3D (3d-force-graph, three.js)
const valDoc = d => Math.pow((2 + 0.55 * Math.sqrt(d.n_links + d.n_chunks)) / TAM, 3);  // raio = TAM * cbrt(val)
const valCore = () => Math.pow(4.4 / TAM, 3);
const valLua = () => Math.pow(0.6 / TAM, 3);
function luas3D() {
  if (!selecionado) return [];
  const d = byPath[selecionado], n = Math.min(d.n_chunks, LUA_MAX);
  const r = 2 + 0.55 * Math.sqrt(d.n_links + d.n_chunks);
  const base = N3D.find(x => x.id === selecionado);
  const c = base ? [base.x, base.y, base.z] : G.CENTRO[idxDoc(d)];
  return Array.from({ length: n }, (_, i) => {
    const a0 = TAU * i / n, R = r + 2.2 + (i % 3) * 0.7;
    return { id: "lua|" + d.path + "|" + i, lua: i, val: valLua(), a0, R,
             x: c[0] + R * Math.cos(a0), y: c[1], z: c[2] + R * Math.sin(a0) };
  });
}
function buildNodes3D() {
  const nodes = docs.map(d => {
    const i = idxDoc(d);
    // começa na casca da galáxia (direção determinística por caminho)
    const vx = semente(d.path, 3), vy = semente(d.path, 4), vz = semente(d.path, 5);
    const r = Math.hypot(vx, vy, vz) || 1;
    return { id: d.path, g: i, val: valDoc(d),
             x: G.CENTRO[i][0] + vx / r * G.RAIO[i], y: G.CENTRO[i][1] + vy / r * G.RAIO[i], z: G.CENTRO[i][2] + vz / r * G.RAIO[i] };
  });
  nodes.push(...G.list.map((g, i) => ({ id: "core:" + i, core: i, val: valCore(), x: G.CENTRO[i][0], y: G.CENTRO[i][1], z: G.CENTRO[i][2] })));
  nodes.push(...luas3D());
  return nodes;
}
function buildLinks3D() {
  const links = D.links.map(l => ({ source: l.de, target: l.para, rel: l.rel }));
  if (selecionado) for (const m of luas3D()) links.push({ source: selecionado, target: m.id, lua: true });
  return links;
}
const nodeColor3 = n => n.core !== undefined ? (dimCore(n.core) ? "#0d1424" : G.corGal.get(n.core))
  : n.lua !== undefined ? "#94a3b8" : (dimDoc(byPath[n.id]) ? "#0d1424" : G.corGal.get(idxDoc(byPath[n.id])));
const linkColor3 = l => {
  const s = l.source, t = l.target;
  const dim = (s.core !== undefined ? dimCore(s.core) : dimDoc(byPath[s.id])) ||
              (t.core !== undefined ? dimCore(t.core) : (t.lua !== undefined ? dimDoc(byPath[s.id]) : dimDoc(byPath[t.id])));
  if (dim) return "#0d1424";
  return l.lua ? "#94a3b8" : (l.rel.includes("cita") ? "#475569" : "#3b82f6");
};
function labelDoc(d) {
  return `<b>${esc(rotulo(d))}</b><br>${esc([d.tipo, d.pasta, G.gal[idxDoc(d)].nome,
    d.n_chunks + " chunk" + (d.n_chunks === 1 ? "" : "s"), d.n_links + " link" + (d.n_links === 1 ? "" : "s")].filter(Boolean).join(" · "))}`;
}
const forcaGalaxia = () => {
  for (const n of N3D) {
    const i = n.core !== undefined ? n.core : n.g;
    const c = G.CENTRO[i];
    if (n.core !== undefined) { n.x = c[0]; n.y = c[1]; n.z = c[2]; n.vx = n.vy = n.vz = 0; }
    else if (n.lua === undefined) {
      // esfera limpa: mola radial forte devolve a distância do centro para R_gal a cada tick
      const dx = n.x - c[0], dy = n.y - c[1], dz = n.z - c[2];
      const r = Math.hypot(dx, dy, dz) || 1e-6, f = (G.RAIO[i] - r) / r * 0.5;
      n.vx += dx * f; n.vy += dy * f; n.vz += dz * f;
    }
  }
};
function setDados3D() {
  g3d.graphData({ nodes: buildNodes3D(), links: buildLinks3D() });
  const g = g3d.graphData();  // os nós internos (com as posições): é neles que a física trabalha
  N3D = g.nodes; L3D = g.links;
  G.porGal = G.list.map(() => []);  // recria a lista de nós da rotação (os nós internos trocaram)
  for (const n of N3D) if (n.g !== undefined) G.porGal[n.g].push(n);
  g3d.d3Force("galaxia", forcaGalaxia);
  g3d.d3ReheatSimulation();
}
// rotação lenta da casca de cada galáxia em torno do core: Rodrigues por tick — rotação pura, preserva a
// distância ao centro (a mola da casca não sente; só a orientação muda). Trig uma vez por galáxia/tick.
let tAnt = null;
function rotacionarGalo() {
  const t = performance.now() / 1000, dt = tAnt === null ? 0 : Math.min(t - tAnt, 0.1);
  tAnt = t;
  if (!dt) return;
  for (const r of G.ROT) {
    const ns = G.porGal[r.i];
    if (!ns.length) continue;
    const a = r.om * dt, cos = Math.cos(a), sen = Math.sin(a), ocm = 1 - cos;
    const c = G.CENTRO[r.i], ex = r.ex, ey = r.ey, ez = r.ez;
    for (const n of ns) {
      const x = n.x - c[0], y = n.y - c[1], z = n.z - c[2];
      const ep = ex * x + ey * y + ez * z;  // e·p, com p relativo ao centro
      n.x = c[0] + x * cos + (ey * z - ez * y) * sen + ex * ep * ocm;
      n.y = c[1] + y * cos + (ez * x - ex * z) * sen + ey * ep * ocm;
      n.z = c[2] + z * cos + (ex * y - ey * x) * sen + ez * ep * ocm;
    }
  }
}
function orbitarLuas() {
  const t = (performance.now() - t0) / 1000;
  for (const l of L3D) {
    if (!l.lua) continue;
    const p = l.source, m = l.target;
    if (typeof p !== "object" || typeof m !== "object") continue;  // 1º tick após seleção: link ainda string (digest do kapsule)
    const a = m.a0 + t * 0.5;
    m.x = p.x + m.R * Math.cos(a);
    m.z = p.z + m.R * Math.sin(a);
    m.y = p.y + m.R * 0.35 * Math.sin(a * 1.7 + m.a0);
  }
}
// ---------- câmera: voo por rAF (sem libs, ease in-out); o usuário assume o controle no meio do voo
const distGlobal = () => 2.2 * Math.max(G.R_GAL + 90, 140);  // distância global: init3D e cameraGlobal compartilham (não divergem)
let vooCam = null;
const cancelarVooCam = () => { if (vooCam) { cancelAnimationFrame(vooCam.raf); vooCam = null; } };
function voarCam(para, paraAlvo, ms = 800) {
  if (!g3d) return;
  cancelarVooCam();
  const p = g3d.cameraPosition();  // {x, y, z, lookAt} — posição atual e ponto da linha de olhar
  const ctl = g3d.controls ? g3d.controls() : null;
  const de = { x: p.x, y: p.y, z: p.z };
  const deAlvo = ctl && ctl.target ? { x: ctl.target.x, y: ctl.target.y, z: ctl.target.z }
                                   : { x: p.lookAt.x, y: p.lookAt.y, z: p.lookAt.z };
  const t0 = performance.now();
  const passo = agora => {
    if (!vooCam) return;  // cancelado no meio do voo
    const t = Math.min(1, (agora - t0) / ms);
    const k = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;  // ease in-out
    g3d.cameraPosition({ x: de.x + (para.x - de.x) * k, y: de.y + (para.y - de.y) * k, z: de.z + (para.z - de.z) * k },
                       { x: deAlvo.x + (paraAlvo.x - deAlvo.x) * k, y: deAlvo.y + (paraAlvo.y - deAlvo.y) * k,
                         z: deAlvo.z + (paraAlvo.z - deAlvo.z) * k });
    if (t < 1) vooCam.raf = requestAnimationFrame(passo);
    else vooCam = null;
  };
  vooCam = { raf: requestAnimationFrame(passo) };
}
$("g3d").addEventListener("pointerdown", cancelarVooCam);
$("g3d").addEventListener("wheel", cancelarVooCam, { passive: true });
function cameraNaGalaxia(i, ms = 800) {
  if (!g3d) return;  // setIsolado(null) roda no load, antes do init3D
  const c = G.CENTRO[i];
  const p = g3d.cameraPosition();
  let dx = p.x - c[0], dy = p.y - c[1], dz = p.z - c[2];
  const L = Math.hypot(dx, dy, dz);  // mantém a direção de olhar atual: voa "em frente"
  if (L < 1e-6) { dx = 0; dy = 0; dz = 1; } else { dx /= L; dy /= L; dz /= L; }
  const dist = Math.max(3.5 * G.RAIO[i], 80);
  voarCam({ x: c[0] + dx * dist, y: c[1] + dy * dist, z: c[2] + dz * dist },
          { x: c[0], y: c[1], z: c[2] }, ms);
}
function cameraGlobal(ms = 800) {
  if (!g3d) return;
  voarCam({ x: 0, y: 0, z: distGlobal() }, { x: 0, y: 0, z: 0 }, ms);
}
function cameraNoPlaneta(n, ms) {
  if (isolado !== null) {  // isolado: base no centro/raio da galáxia (sem ping-pong com a distância global)
    const c = G.CENTRO[isolado];
    const dx = n.x - c[0], dy = n.y - c[1], dz = n.z - c[2];
    const r = Math.hypot(dx, dy, dz), dist = Math.max(2.4 * G.RAIO[isolado], 100);
    const cam = r > 1e-6 ? { x: c[0] + dx / r * dist, y: c[1] + dy / r * dist, z: c[2] + dz / r * dist }
                         : { x: c[0], y: c[1], z: c[2] + dist };
    g3d.cameraPosition(cam, n, ms);
    return;
  }
  const r = Math.hypot(n.x, n.y, n.z);
  const cam = r > 1e-6 ? (() => { const f = Math.max(60, r * 2.4) / r; return { x: n.x * f, y: n.y * f, z: n.z * f }; })()
                      : { x: 0, y: 0, z: 60 };
  g3d.cameraPosition(cam, n, ms);
}
function init3D() {
  const box = $("g3d");
  if (!window.ForceGraph3D) { box.append(el("div", "3d-force-graph indisponível (WebGL necessário) — sem visualização.", "off")); return; }
  g3d = ForceGraph3D()(box)
    .width(box.clientWidth).height(box.clientHeight)
    .backgroundColor("#070b16")
    .nodeRelSize(TAM).nodeResolution(10)
    .nodeColor(nodeColor3)
    .nodeLabel(n => n.core !== undefined
      ? `<b>Galáxia: ${esc(G.gal[n.core].nome)}</b><br>${esc(G.gal[n.core].centro ? rotulo(byPath[G.gal[n.core].centro]) : "sem centro (Tudo)")} · ${G.gal[n.core].docs} documento${G.gal[n.core].docs === 1 ? "" : "s"} · clique isola`
      : n.lua !== undefined ? "chunk do planeta — clique abre o documento"
      : labelDoc(byPath[n.id]))
    .linkColor(linkColor3)
    .linkOpacity(0.22)  // a web de "cita" (centenas de linhas longas) visível sem costurar os núcleos numa massa
    .linkWidth(0.4)
    .linkDirectionalArrowLength(0.8).linkDirectionalArrowRelPos(1)
    .linkDirectionalParticles(l => l.lua ? 0 : 1)
    .linkDirectionalParticleWidth(0.8).linkDirectionalParticleSpeed(0.15)
    .linkDirectionalParticleColor(l => l.lua ? "#94a3b8" : (l.rel.includes("cita") ? "#64748b" : "#60a5fa"))
    .cooldownTime(Infinity)  // o motor não para: as luas orbitam e as cascas giram (1.73.4 não tem d3AlphaTarget)
    .onNodeClick(n => {
      if (n.core !== undefined) setIsolado(isolado === n.core ? null : n.core);
      else if (n.lua !== undefined) { const bar = n.id.lastIndexOf("|"); selecionarLua(n.id.slice(4, bar), +n.id.slice(bar + 1)); }
      else selecionar(selecionado === n.id ? null : n.id);
    })
    .onNodeHover(n => box.style.cursor = n ? "pointer" : "default")
    .onBackgroundClick(() => { setIsolado(null); selecionar(null); });
  setDados3D();
  g3d.d3Force("charge").strength(-6);  // fraco: a casca esférica manda, o charge só espalha
  g3d.d3Force("link").distance(26).strength(0.2);
  g3d.warmupTicks(50);  // reaquecimento das seleções (a entrada anima na tela)
  g3d.onEngineTick(() => { rotacionarGalo(); orbitarLuas(); });  // rotação antes das luas: elas seguem o planeta
  g3d.cameraPosition({ x: 0, y: 0, z: distGlobal() });
  new ResizeObserver(() => g3d && g3d.width(box.clientWidth).height(box.clientHeight)).observe(box);
  box.append(el("div", "arraste para girar · roda para zoom · clique no planeta abre o documento · build __BUILD__", "hint"));
}

// ---------- seleção / câmera
function selecionarLua(path, i) {
  selecionar(path);
  const pre = document.querySelector(`#detail pre.chunktxt[data-i="${i}"]`);
  if (pre) { pre.style.display = ""; pre.scrollIntoView({ block: "center" }); }
}
function selecionar(path) {
  selecionado = path && byPath[path] ? path : null;
  showDoc(selecionado);
  if (g3d) {
    setDados3D();
    if (selecionado) { const n = N3D.find(n => n.id === selecionado); if (n) cameraNoPlaneta(n, 700); }
  }
  renderLista();
}

// ---------- lista
function renderLista() {
  const ul = $("list"); ul.replaceChildren();
  const vis = docs.filter(d => matchBusca(d) && matchTipo(d))
    .sort((a, b) => idxDoc(a) - idxDoc(b) || (a.id || a.path).localeCompare(b.id || b.path, "pt-BR", { numeric: true }));
  $("conta").textContent = (busca || tipoF) ? `${vis.length} de ${docs.length}` : `${docs.length} documentos`;
  for (const d of vis) {
    const li = el("li");
    li.append(el("div", rotulo(d), "t"), el("div", [d.tipo, d.subpasta || d.pasta, G.gal[idxDoc(d)].nome].filter(Boolean).join(" · "), "m"));
    li.onclick = () => { if (selecionado !== d.path) { location.hash = docHash(d.path); selecionar(d.path); } };
    if (d.path === selecionado) li.classList.add("on");
    ul.append(li);
  }
}

// ---------- painel do documento
function resolveLink(basePath, target) {
  target = decodeURIComponent(target).split("#")[0].trim();
  if (!target || /^[a-z][a-z0-9+.-]*:/i.test(target)) return null;
  const partes = target.startsWith("/") ? target.slice(1).split("/") : basePath.split("/").slice(0, -1).concat(target.split("/"));
  const st = []; let prof = 0;
  for (const s of partes) {
    if (!s || s === ".") continue;
    if (s === "..") { if (!prof--) return null; }  // fora do bundle (como o local.resolve)
    else { st.push(s); prof++; }
  }
  const p = st.join("/");
  if (byPath[p]) return p;
  const m = /^(\d+)(?:[ _:-].*)?$/.exec(p.split("/").pop().replace(/\.md$/, ""));  // arquivo por id (como o local.resolve)
  return m && byId[m[1]] ? byId[m[1]].path : null;
}
function ajustarMd(d) {
  let md = d.corpo;
  // citação do ELM "[id TÍTULO]" (sem URL) -> link interno; link relativo que resolve -> #doc=
  md = md.replace(/\[(\d{3,})[ .\-–—]([^\]]+)\](?!\()/g, (s, id, titulo) => byId[id] ? `[${titulo}](#doc=${encodeURIComponent(byId[id].path)})` : s);
  md = md.replace(/\]\(([^)\s]+)\)/g, (s, href) => { const p = resolveLink(d.path, href); return p ? `](#doc=${encodeURIComponent(p)})` : s; });
  return md;
}
function corpoMd(d) {
  const div = el("div", null, "md");
  if (!(window.marked && window.DOMPurify)) { div.append(el("pre", d.corpo, "raw")); return div; }
  div.innerHTML = window.DOMPurify.sanitize(window.marked.parse(ajustarMd(d)));
  const base = new URL(d.path.split("/").map(encodeURIComponent).join("/"), location.href);
  for (const a of div.querySelectorAll("a[href]")) {
    const href = a.getAttribute("href");
    if (href.startsWith("#doc=")) continue;  // o hashchange cuida
    a.href = new URL(href, base).href;  // o resto (imagens, anexos, ELM) é relativo ao .md
    if (!href.startsWith("#")) { a.target = "_blank"; a.rel = "noopener"; }
  }
  for (const img of div.querySelectorAll("img[src]")) img.src = new URL(img.getAttribute("src"), base).href;
  return div;
}
function secChunks(box, d) {
  const cs = deDoc[d.path] || [];
  box.append(el("h3", `Chunks (${cs.length})`, "sec"));
  if (!cs.length) { box.append(el("div", "—", "m")); return; }
  for (const [i, c] of cs.entries()) {
    const b = el("button", `${i + 1}. ${c.secao || "(início)"}`, "chunk");
    const pre = el("pre", (c.texto.split("\n").slice(1).join("\n") || c.texto), "raw chunktxt");
    pre.dataset.i = i; pre.style.display = "none";
    b.onclick = () => { pre.style.display = pre.style.display === "none" ? "" : "none"; b.classList.toggle("on"); };
    box.append(b, pre);
  }
}
function linkList(box, titulo, itens, chave) {
  box.append(el("h3", `${titulo} (${itens.length})`, "sec"));
  if (!itens.length) { box.append(el("div", "—", "m")); return; }
  for (const l of itens) {
    const d = byPath[l[chave]], a = el("a", d ? rotulo(d) : l[chave], "link");
    a.prepend(el("span", l.rel + " · ", "rel"));
    if (d) a.href = docHash(d.path);
    box.append(a);
  }
}
function showDoc(path) {
  const box = $("detail");
  if (!path || !byPath[path]) {
    layout.classList.add("sem-detalhe");  // sem artefato selecionado: o grafo ocupa o espaço todo
    box.replaceChildren(el("p", "Selecione um documento na lista, no grafo ou numa lua.", "m"));
    return;
  }
  layout.classList.remove("sem-detalhe");  // o painel só aparece com artefato selecionado
  const d = byPath[path];
  box.replaceChildren();
  const topo = el("div", null, "topo");
  topo.append(el("h2", rotulo(d), "title"));
  const f = el("button", "×"); f.className = "fechar"; f.setAttribute("aria-label", "Fechar painel");
  f.onclick = () => { setIsolado(null); selecionar(null); };  // mesmo efeito do clique no fundo do 3D
  topo.append(f);
  box.append(topo);
  const meta = el("div", null, "meta");
  for (const x of [d.tipo, d.subpasta || d.pasta, G.gal[idxDoc(d)].nome]) if (x) meta.append(el("span", x, "chip"));
  meta.append(el("span", `${d.n_chunks} chunk${d.n_chunks === 1 ? "" : "s"} · ${d.n_links} link${d.n_links === 1 ? "" : "s"}`, "chip"));
  const raw = el("a", "abrir .md"); raw.href = d.path.split("/").map(encodeURIComponent).join("/"); raw.target = "_blank"; raw.rel = "noopener";
  meta.append(raw);
  box.append(meta);
  if (d.descricao) box.append(el("div", d.descricao, "desc"));
  box.append(corpoMd(d));
  secChunks(box, d);
  linkList(box, "Links de saída", out[path] || [], "para");
  linkList(box, "Links de entrada", inc[path] || [], "de");
  box.scrollTop = 0;
}
function fromHash() {
  const p = location.hash.startsWith("#doc=") ? decodeURIComponent(location.hash.slice(5)) : null;
  if (p && byPath[p] && p !== selecionado) selecionar(p);
}

// ---------- larguras das barras laterais (arraste nos handles; persiste em localStorage)
const layout = $("layout");
function larguraAtual(prop) { const v = getComputedStyle(layout).getPropertyValue(prop); return v ? parseFloat(v) : null; }
function arrastarLargura(handle, prop, chave, min, max) {
  handle.addEventListener("pointerdown", e => {
    e.preventDefault();
    const w0 = larguraAtual(prop) || 300, x0 = e.clientX, sinal = prop === "--w-esq" ? 1 : -1;
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("on");
    document.body.style.userSelect = "none";
    const mover = ev => {
      const w = Math.max(min, Math.min(max, w0 + (ev.clientX - x0) * sinal));
      layout.style.setProperty(prop, w + "px");
    };
    const fim = () => {
      handle.classList.remove("on");
      document.body.style.userSelect = "";
      try { localStorage.setItem(chave, Math.round(larguraAtual(prop))); } catch {}
      handle.removeEventListener("pointermove", mover);
      handle.removeEventListener("pointerup", fim);
      handle.removeEventListener("pointercancel", fim);
    };
    handle.addEventListener("pointermove", mover);
    handle.addEventListener("pointerup", fim);
    handle.addEventListener("pointercancel", fim);
  });
}
arrastarLargura($("arr-esq"), "--w-esq", "explorar-w-esq", 160, 480);
arrastarLargura($("arr-dir"), "--w-dir", "explorar-w-dir", 280, 700);
for (const [prop, chave, min, max] of [["--w-esq", "explorar-w-esq", 160, 480], ["--w-dir", "explorar-w-dir", 280, 700]]) {
  let w = null;
  try { w = +localStorage.getItem(chave); } catch {}
  if (w && w >= min && w <= max) layout.style.setProperty(prop, w + "px");
}

// ---------- start
document.getElementById("name").textContent = D.bundle + " — galáxias do bundle";
const textStats = () => `${docs.length} documentos · ${D.chunks.length} chunks · ${D.links.length} links · ${G.KGAL} galáxias`;
// seletor de galáxia no header (isolar/soltar; o clique no core 3D sincroniza pelo mesmo setIsolado)
$("gal").onchange = e => setIsolado(e.target.value === "" ? null : +e.target.value);
// combobox do modo de galáxias (persiste em localStorage; ao trocar: reconstrói as opções e volta a "Todas")
$("modo").value = modo;
$("modo").onchange = e => {
  modo = e.target.value;
  montarGalo();
  setIsolado(null);
  $("stats").textContent = textStats();
  if (g3d) setDados3D();
  renderLista(); repintar();
  if (selecionado) showDoc(selecionado);
  try { localStorage.setItem("explorar-modo", modo); } catch {}
};
setIsolado(null);  // popula o seletor no start
$("stats").textContent = textStats();
// dropdown por tipo (quantidade desc, nome asc)
const contTipo = {};
for (const d of docs) { const t = d.tipo || "(sem tipo)"; contTipo[t] = (contTipo[t] || 0) + 1; }
const selTipo = $("tipo");
const todos = el("option", "Todos"); todos.value = ""; selTipo.append(todos);
for (const t of Object.keys(contTipo).sort((a, b) => contTipo[b] - contTipo[a] || a.localeCompare(b, "pt-BR"))) {
  const o = el("option", `${t} (${contTipo[t]})`); o.value = t; selTipo.append(o);
}
selTipo.onchange = e => { tipoF = e.target.value; renderLista(); repintar(); };
$("q").addEventListener("input", e => { busca = e.target.value.trim().toLowerCase(); renderLista(); repintar(); });
repintar();
renderLista();
init3D();
window.addEventListener("hashchange", fromHash);
fromHash();
</script>
</body>
</html>
"""
