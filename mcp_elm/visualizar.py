"""Visualização do bundle local: <bundle>/explorar.html — o bundle em galáxias, num HTML autocontido.

Galáxia = cluster semântico (agregativo, linkage média, distância de cosseno entre a média normalizada dos
vetores dos chunks do documento; funde enquanto a distância for menor que LIMITE); planeta = documento
(tamanho proporcional a links + chunks); lua = chunk (orbita o planeta selecionado). Os documentos e vetores
saem do índice local em modo somente leitura (nada de reindexação aqui) e os links saem do disco com a mesma
lógica do elm_ler_req_local. As bibliotecas (3d-force-graph/three.js na vista 3D, Cytoscape.js na vista 2D,
marked + DOMPurify no Markdown) ficam em assets/ e vão embutidas no HTML: abre no navegador sem internet
(intranet). Sem extensão 3D para o Cytoscape (o Cytoscape.js é 2D), a 3D é do 3d-force-graph — a mesma
família three.js do visualizador anterior.
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

LIMITE = 0.3  # distância de cosseno: funde clusters enquanto a distância de linkage média for menor que ela
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


def _nome(X: np.ndarray, membros: list[int], titulos: list[str]) -> str:
    """Título do documento mais próximo do centroide (normalizado) do cluster."""
    c = X[membros].mean(axis=0)
    if not np.linalg.norm(c):
        return titulos[membros[0]]
    return titulos[int(np.argmax(X[membros] @ (c / np.linalg.norm(c))))]


def _pagina(dados: dict) -> str:
    js = json.dumps(dados, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = PAGINA
    for nome, arquivo in (("CYTOSCAPE", "cytoscape.min.js"), ("FORCA3D", "3d-force-graph.min.js"),
                          ("MARKED", "marked.min.js"), ("PURIFY", "purify.min.js")):
        html = html.replace(f"__{nome}__", _ler_asset(arquivo))
    return html.replace("__DATA__", js)


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

    # galáxias: cluster semântico dos vetores; sem vetores -> "Tudo"; só alguns sem -> "Sem índice"
    com_vetor = [p for p in paths if p in vetores]
    sem_vetor = [p for p in paths if p not in vetores]
    titulo = {l[0]: (l[2] or "") for l in linhas}
    if not com_vetor:
        grupos, nomes = [paths], ["Tudo"]
    else:
        if len({v.shape[0] for v in vetores.values()}) > 1:  # índice com dois embedders: sem como fundir
            raise Erro("precondicao", "índice com vetores de dimensões diferentes: rode "
                        "elm_indexar_local (forcar)", "indice")
        X = np.stack([vetores[p] for p in com_vetor]).astype(np.float64)
        clusters = aglomerar(X, LIMITE)
        por: dict[int, list[int]] = {}
        for idx, c in enumerate(clusters):
            por.setdefault(c, []).append(idx)
        grupos = [[com_vetor[i] for i in mem] for mem in por.values()]
        nomes = [_nome(X, mem, [titulo[com_vetor[i]] for i in mem]) for mem in por.values()]
        if sem_vetor:
            grupos.append(sem_vetor)
            nomes.append("Sem índice")
    # maior galáxia primeiro (empate: o path menor), numerando de 0
    ordenado = sorted(zip(nomes, grupos), key=lambda gn: (-len(gn[1]), min(gn[1])))
    nomes, grupos = [n for n, _ in ordenado], [m for _, m in ordenado]
    gal_de: dict[str, int] = {}
    for gid, membros in enumerate(grupos):
        for p in membros:
            gal_de[p] = gid

    docs_json = []
    for p, doc_id, doc_titulo, tipo, pasta, desc in linhas:
        d = docs.get(p)  # arquivo apagado depois do índice: sem corpo e sem links
        docs_json.append({"path": p, "id": doc_id, "titulo": doc_titulo or "", "tipo": tipo, "pasta": pasta,
                          "subpasta": p.rsplit("/", 1)[0] if "/" in p else None, "descricao": desc,
                          "n_chunks": n_chunks.get(p, 0), "n_links": grau.get(p, 0), "galaxia": gal_de[p],
                          "corpo": d["body"] if d else ""})
    dados = {"bundle": os.path.basename(root),
             "docs": docs_json,
             "links": links,
             "chunks": [{"path": p, "secao": s, "texto": t} for p, s, t in chunk_rows],
             "galaxias": [{"id": gid, "nome": nomes[gid], "docs": len(m)} for gid, m in enumerate(grupos)]}
    out = Path(root) / "explorar.html"
    out.write_text(_pagina(dados), encoding="utf-8")
    return {"path": str(out), "documentos": len(docs_json), "chunks": len(chunk_rows),
            "galaxias": len(grupos), "links": len(links),
            "gerado_em": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}


PAGINA = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Galáxias do bundle</title>
<script>__CYTOSCAPE__</script>
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
.toolbar { margin-left:auto; display:flex; gap:10px; align-items:center; }
.toolbar label { font-size:13px; color:var(--muted); }
.toolbar select { padding:5px 8px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); font:inherit; }
.vtog { border:1px solid var(--line); background:var(--bg); color:var(--fg); border-radius:6px; padding:5px 12px; cursor:pointer; font:inherit; }
.vtog.on { background:var(--accent); border-color:var(--accent); color:#fff; }
main { display:grid; grid-template-columns:290px 1fr minmax(380px, 40%); height:calc(100vh - 47px); }
aside, section { overflow:auto; border-right:1px solid var(--line); background:var(--panel); }
#filters { padding:10px 12px; display:grid; gap:8px; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--panel); z-index:1; }
input, select { width:100%; padding:6px 8px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); font:inherit; }
#conta { color:var(--muted); font-size:12px; }
#list { list-style:none; margin:0; padding:0; }
#list li { padding:8px 12px; border-bottom:1px solid var(--line); cursor:pointer; }
#list li:hover, #list li.on { background:var(--chip); }
#list .t { font-weight:600; }
#list .m { color:var(--muted); font-size:12px; }
#graph { position:relative; overflow:hidden; background:#070b16; border-right:1px solid var(--line); }
#g3d, #g2d { position:absolute; inset:0; }
#g2d { background:var(--bg); }
#graph .off { position:absolute; inset:0; display:grid; place-items:center; color:var(--muted); padding:16px; text-align:center; }
#graph .hint { position:absolute; right:10px; bottom:8px; color:#94a3b8; font-size:12px; pointer-events:none; }
#legenda { position:absolute; left:10px; bottom:10px; max-width:320px; max-height:52%; overflow:auto; background:var(--panel);
  border:1px solid var(--line); border-radius:8px; padding:8px 10px; z-index:2; box-shadow:0 2px 10px rgba(0,0,0,.25); }
#legenda h4 { margin:0 0 6px; font-size:12px; color:var(--muted); text-transform:lowercase; }
#legenda .legitem { display:flex; gap:8px; align-items:center; padding:2px 4px; border-radius:5px; cursor:pointer; font-size:12px; }
#legenda .legitem:hover { background:var(--chip); }
#legenda .legitem.on { background:var(--chip); outline:1px solid var(--accent); }
#legenda .sw { width:11px; height:11px; border-radius:3px; flex:none; }
#tip { position:absolute; display:none; z-index:3; pointer-events:none; background:var(--panel); color:var(--fg);
  border:1px solid var(--line); border-radius:6px; padding:4px 8px; font-size:12px; max-width:340px; box-shadow:0 2px 8px rgba(0,0,0,.3); }
#detail { padding:16px 20px; border-right:0; }
#detail h2.title { font-size:18px; margin:0 0 4px; }
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
  main { grid-template-columns:1fr; height:auto; }
  aside, section { border-right:0; border-bottom:1px solid var(--line); max-height:60vh; }
  #graph { height:60vh; }
  #detail { max-height:none; }
}
</style>
</head>
<body>
<header>
  <h1 id="name"></h1>
  <span class="stat" id="stats"></span>
  <div class="toolbar">
    <button class="vtog on" data-m="3d" id="vtog3d">3D</button>
    <button class="vtog" data-m="2d" id="vtog2d">2D</button>
    <label>cor por <select id="corpo" aria-label="Colorir por">
      <option value="tipo" selected>tipo</option><option value="pasta">pasta</option><option value="galáxia">galáxia</option>
    </select></label>
  </div>
</header>
<main>
  <aside>
    <div id="filters">
      <input id="q" type="search" placeholder="Filtrar por título, id, descrição…" aria-label="Filtro">
      <div id="conta"></div>
    </div>
    <ul id="list"></ul>
  </aside>
  <section id="graph" aria-label="Galáxias do bundle">
    <div id="g3d"></div>
    <div id="g2d" style="display:none"></div>
    <div id="legenda"></div>
    <div id="tip"></div>
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
const gal = Object.fromEntries(D.galaxias.map(g => [g.id, g]));
const KGAL = D.galaxias.length;
// centros das galáxias: grade de Fibonacci na esfera (determinístico, sem solapar)
const R_GAL = KGAL <= 1 ? 0 : 120 + 26 * Math.sqrt(KGAL);
const CENTRO = D.galaxias.map((g, i) => {
  if (KGAL === 1) return [0, 0, 0];
  const phi = Math.acos(1 - 2 * (i + 0.5) / KGAL), th = Math.PI * (1 + Math.sqrt(5)) * (i + 0.5);
  return [R_GAL * Math.sin(phi) * Math.cos(th), R_GAL * Math.sin(phi) * Math.sin(th), R_GAL * Math.cos(phi)];
});
const out = {}, inc = {};
for (const l of D.links) { (out[l.de] ||= []).push(l); (inc[l.para] ||= []).push(l); }
const deDoc = {};
for (const c of D.chunks) (deDoc[c.path] ||= []).push(c);

// ---------- estado
let corCampo = "tipo", corMapa = new Map(), corGal = new Map(), isolado = null, busca = "";
let selecionado = null, modo = window.ForceGraph3D ? "3d" : "2d";
let g3d = null, cy = null, N3D = [], L3D = [];
const TAU = Math.PI * 2, TAM = 2.2, LUA_MAX = 40;  // TAM = nodeRelSize da vista 3D; LUA_MAX = luas por planeta
const t0 = performance.now();

// ---------- util
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const esc = s => { const e = document.createElement("span"); e.textContent = s ?? ""; return e.innerHTML; };
const rotulo = d => (d.id ? d.id + " — " : "") + d.titulo;
const docHash = p => "#doc=" + encodeURIComponent(p);
const valor = d => corCampo === "tipo" ? (d.tipo || "(sem tipo)") : corCampo === "pasta" ? (d.subpasta || d.pasta || "(raiz)") : "gal:" + d.galaxia;
const rotuloValor = v => v.startsWith("gal:") ? gal[+v.slice(4)].nome : v;
// semente determinística por caminho (posição inicial dos planetas, em [-1, 1])
const semente = (s, k) => { let h = 2166136261 ^ s.length; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619); h = Math.imul(h ^ (h >>> (5 * k + 3)), 16777619); return ((h >>> 0) % 1000) / 500 - 1; };
const matchBusca = d => !busca || [d.id, d.titulo, d.descricao, d.pasta, d.path, gal[d.galaxia].nome].some(x => x && String(x).toLowerCase().includes(busca));
const matchBuscaGal = i => !busca || String(gal[i].nome).toLowerCase().includes(busca);
function dimDoc(d) {
  if (busca && !matchBusca(d)) return true;
  if (!isolado) return false;
  const v = isolado.campo === "tipo" ? (d.tipo || "(sem tipo)") : isolado.campo === "pasta" ? (d.subpasta || d.pasta || "(raiz)") : "gal:" + d.galaxia;
  return v !== isolado.valor;
}
const dimCore = i => (busca && !matchBuscaGal(i)) || (!!isolado && !(isolado.campo === "galáxia" && isolado.valor === "gal:" + i));
const corDoc = d => corMapa.get(valor(d));
const corDoCore = i => corMapa.get("gal:" + i) || corGal.get(i);

// ---------- cor / legenda
function repaleta() {
  const vals = [...new Set(docs.map(valor))].sort((a, b) => a.localeCompare(b, "pt-BR"));
  corMapa = new Map(vals.map((v, i) => [v, `hsl(${Math.round(i * 137.508) % 360}, 62%, 58%)`]));
  // as galáxias têm cor própria sempre (o nó core aparece em qualquer "cor por")
  corGal = new Map(D.galaxias.map((g, i) => [i, `hsl(${(Math.round(i * 137.508) + 180) % 360}, 70%, 55%)`]));
  repintar();
  legenda();
}
function legenda() {
  const box = $("legenda");
  box.replaceChildren(el("h4", `cor por ${corCampo}` + (isolado ? ` · isola: ${rotuloValor(isolado.valor)}` : " · clique isola")));
  const cont = {};
  for (const d of docs) { const v = valor(d); cont[v] = (cont[v] || 0) + 1; }
  for (const v of Object.keys(cont).sort((a, b) => cont[b] - cont[a] || a.localeCompare(b, "pt-BR"))) {
    const item = el("div", null, "legitem" + (isolado && isolado.valor === v ? " on" : ""));
    const sw = el("span", null, "sw"); sw.style.background = corMapa.get(v);
    item.append(sw, el("span", `${rotuloValor(v)} (${cont[v]})`));
    item.onclick = () => { isolado = (isolado && isolado.valor === v) ? null : { campo: corCampo, valor: v }; repintar(); };
    box.append(item);
  }
}
function dimNodo(n) {
  const d = n.data();
  if (d.core !== undefined) return dimCore(d.core);
  if (d.lua !== undefined) return dimDoc(byPath[d.path]);
  return dimDoc(byPath[n.id()]);
}
function corNodo2(n) {
  const d = n.data();
  if (d.core !== undefined) return dimNodo(n) ? "#9ca3af" : corDoCore(d.core);
  if (d.lua !== undefined) return "#94a3b8";
  return dimNodo(n) ? "#d6d3d1" : corDoc(byPath[n.id()]);
}
function repintar() {
  if (g3d) g3d.nodeColor(nodeColor3).linkColor(linkColor3);
  if (cy) {
    cy.nodes().forEach(n => n.style("background-color", corNodo2(n)));
    cy.edges().forEach(e => e.style("opacity", dimNodo(e.source()) || dimNodo(e.target()) ? 0.06 : (e.data().lua ? 0.5 : 0.45)));
  }
  legenda();
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
  const c = base ? [base.x, base.y, base.z] : CENTRO[d.galaxia];
  return Array.from({ length: n }, (_, i) => {
    const a0 = TAU * i / n, R = r + 2.2 + (i % 3) * 0.7;
    return { id: "lua|" + d.path + "|" + i, lua: i, val: valLua(), a0, R,
             x: c[0] + R * Math.cos(a0), y: c[1], z: c[2] + R * Math.sin(a0) };
  });
}
function buildNodes3D() {
  const nodes = docs.map(d => {
    const c = CENTRO[d.galaxia];
    return { id: d.path, g: d.galaxia, val: valDoc(d),
             x: c[0] + 14 * semente(d.path, 0), y: c[1] + 14 * semente(d.path, 1), z: c[2] + 14 * semente(d.path, 2) };
  });
  nodes.push(...D.galaxias.map((g, i) => ({ id: "core:" + i, core: i, val: valCore(), x: CENTRO[i][0], y: CENTRO[i][1], z: CENTRO[i][2] })));
  nodes.push(...luas3D());
  return nodes;
}
function buildLinks3D() {
  const links = D.links.map(l => ({ source: l.de, target: l.para, rel: l.rel }));
  if (selecionado) for (const m of luas3D()) links.push({ source: selecionado, target: m.id, lua: true });
  return links;
}
const nodeColor3 = n => n.core !== undefined ? (dimCore(n.core) ? "#0d1424" : corDoCore(n.core))
  : n.lua !== undefined ? "#94a3b8" : (dimDoc(byPath[n.id]) ? "#0d1424" : corDoc(byPath[n.id]));
const linkColor3 = l => {
  const s = l.source, t = l.target;
  const dim = (s.core !== undefined ? dimCore(s.core) : dimDoc(byPath[s.id])) ||
              (t.core !== undefined ? dimCore(t.core) : (t.lua !== undefined ? dimDoc(byPath[s.id]) : dimDoc(byPath[t.id])));
  if (dim) return "#0d1424";
  return l.lua ? "#94a3b8" : (l.rel.includes("cita") ? "#475569" : "#3b82f6");
};
function labelDoc(d) {
  return `<b>${esc(rotulo(d))}</b><br>${esc([d.tipo, d.pasta, gal[d.galaxia].nome,
    d.n_chunks + " chunk" + (d.n_chunks === 1 ? "" : "s"), d.n_links + " link" + (d.n_links === 1 ? "" : "s")].filter(Boolean).join(" · "))}`;
}
const forcaGalaxia = alpha => {
  const k = 0.06 * alpha;
  for (const n of N3D) {
    const c = CENTRO[n.core !== undefined ? n.core : n.g];
    if (n.core !== undefined) { n.x = c[0]; n.y = c[1]; n.z = c[2]; n.vx = n.vy = n.vz = 0; }
    else { n.vx += (c[0] - n.x) * k; n.vy += (c[1] - n.y) * k; n.vz += (c[2] - n.z) * k; }
  }
};
function setDados3D() {
  g3d.graphData({ nodes: buildNodes3D(), links: buildLinks3D() });
  const g = g3d.graphData();  // os nós internos (com as posições): é neles que a física trabalha
  N3D = g.nodes; L3D = g.links;
  g3d.d3ReheatSimulation();
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
function cameraNoPlaneta(n, ms) {
  const r = Math.hypot(n.x, n.y, n.z);
  const cam = r > 1e-6 ? (() => { const f = Math.max(60, r * 2.4) / r; return { x: n.x * f, y: n.y * f, z: n.z * f }; })()
                      : { x: 0, y: 0, z: 60 };
  g3d.cameraPosition(cam, n, ms);
}
function init3D() {
  const box = $("g3d");
  if (!window.ForceGraph3D) { box.append(el("div", "3d-force-graph indisponível: use a vista 2D.", "off")); return; }
  g3d = ForceGraph3D()(box)
    .width(box.clientWidth).height(box.clientHeight)
    .backgroundColor("#070b16")
    .nodeRelSize(TAM).nodeResolution(10)
    .nodeColor(nodeColor3)
    .nodeLabel(n => n.core !== undefined
      ? `<b>Galáxia: ${esc(gal[n.core].nome)}</b><br>${gal[n.core].docs} documento${gal[n.core].docs === 1 ? "" : "s"} · clique isola`
      : n.lua !== undefined ? "chunk do planeta — clique abre o documento"
      : labelDoc(byPath[n.id]))
    .linkColor(linkColor3)
    .linkOpacity(0.5)
    .linkWidth(0.4)
    .linkDirectionalArrowLength(0.8).linkDirectionalArrowRelPos(1)
    .linkDirectionalParticles(l => l.lua ? 0 : 1)
    .linkDirectionalParticleWidth(0.8).linkDirectionalParticleSpeed(0.15)
    .linkDirectionalParticleColor(l => l.lua ? "#94a3b8" : (l.rel.includes("cita") ? "#64748b" : "#60a5fa"))
    .cooldownTime(Infinity)  // o motor não para: as luas seguem orbitando (1.73.4 não tem d3AlphaTarget)
    .onNodeClick(n => {
      if (n.core !== undefined) { isolado = { campo: "galáxia", valor: "gal:" + n.core }; repintar(); }
      else if (n.lua !== undefined) { const bar = n.id.lastIndexOf("|"); selecionarLua(n.id.slice(4, bar), +n.id.slice(bar + 1)); }
      else selecionar(selecionado === n.id ? null : n.id);
    })
    .onNodeHover(n => box.style.cursor = n ? "pointer" : "default")
    .onBackgroundClick(() => { isolado = null; selecionar(null); });
  setDados3D();
  g3d.d3Force("charge").strength(-12);
  g3d.d3Force("link").distance(26).strength(0.35);
  g3d.warmupTicks(50);  // reaquecimento das seleções (a entrada anima na tela)
  g3d.onEngineTick(orbitarLuas);
  g3d.cameraPosition({ x: 0, y: 0, z: 2.2 * Math.max(R_GAL + 90, 140) });
  new ResizeObserver(() => g3d && g3d.width(box.clientWidth).height(box.clientHeight)).observe(box);
  box.append(el("div", "arraste para girar · roda para zoom · clique no planeta abre o documento", "hint"));
}

// ---------- vista 2D (Cytoscape.js)
function pos2D() {
  const R = KGAL === 1 ? 0 : 360 + 34 * Math.sqrt(KGAL), pos = {}, porGal = {};
  for (const d of docs) (porGal[d.galaxia] ||= []).push(d);
  D.galaxias.forEach((g, i) => {
    const ang = TAU * i / KGAL - Math.PI / 2, cx = R * Math.cos(ang), cyy = R * Math.sin(ang);
    pos["core:" + i] = [cx, cyy];
    (porGal[i] || []).sort((a, b) => (a.id || a.path).localeCompare(b.id || b.path, "pt-BR", { numeric: true }))
      .forEach((d, j) => {
        const r = 24 * Math.sqrt(j + 0.6), t2 = j * 2.39996323 + ang;  // espiral de Fermat
        pos[d.path] = [cx + r * Math.cos(t2), cyy + r * Math.sin(t2)];
      });
  });
  return pos;
}
function init2D() {
  const box = $("g2d");
  if (!window.cytoscape) { box.append(el("div", "Cytoscape indisponível: use a vista 3D.", "off")); return; }
  const pos = pos2D();
  const nodes = docs.map(d => ({ data: { id: d.path, tam: 10 + 4 * Math.sqrt(d.n_links + d.n_chunks) },
    position: { x: pos[d.path][0], y: pos[d.path][1] } }))
    .concat(D.galaxias.map((g, i) => ({ data: { id: "core:" + i, core: i, tam: 26 }, position: { x: pos["core:" + i][0], y: pos["core:" + i][1] } })));
  const edges = D.links.map((l, i) => ({ data: { id: "e" + i, source: l.de, target: l.para, rel: l.rel } }));
  cy = window.cytoscape({ container: box, elements: nodes.concat(edges),
    style: [
      { selector: "node", style: { "background-color": "#888", width: "data(tam)", height: "data(tam)",
        "border-width": 1, "border-color": "rgba(120,120,120,.4)" } },
      { selector: "node[?core]", style: { "border-width": 2 } },
      { selector: "node[?lua]", style: { "background-color": "#94a3b8", "border-width": 0 } },
      { selector: "edge", style: { width: 1, opacity: 0.45, "line-color": "#64748b",
        "target-arrow-color": "#64748b", "target-arrow-shape": "triangle", "arrow-scale": 0.6 } },
      { selector: "edge[?lua]", style: { width: 0.7, "target-arrow-shape": "none" } },
    ],
    wheelSensitivity: 0.3 });
  repintar();
  cy.on("tap", "node", e => {
    const n = e.target, dd = n.data();
    if (dd.lua !== undefined) selecionarLua(dd.path, dd.lua);
    else if (dd.core !== undefined) { isolado = { campo: "galáxia", valor: "gal:" + dd.core }; repintar(); }
    else selecionar(selecionado === n.id() ? null : n.id());
  });
  cy.on("tap", e => { if (e.target === cy) { isolado = null; selecionar(null); } });
  const tip = $("tip");
  cy.on("mouseover", "node", e => {
    const n = e.target, dd = n.data();
    tip.textContent = dd.core !== undefined ? `Galáxia: ${gal[dd.core].nome} (${gal[dd.core].docs})`
      : dd.lua !== undefined ? `chunk ${dd.lua + 1} de ${byPath[dd.path].n_chunks} — clique mostra o texto`
      : rotulo(byPath[n.id()]) + (byPath[n.id()].tipo ? " · " + byPath[n.id()].tipo : "");
    tip.style.display = "block";
  });
  cy.on("mousemove", e => { tip.style.left = (e.containerX + 14) + "px"; tip.style.top = (e.containerY + 14) + "px"; });
  cy.on("mouseout", "node", () => { tip.style.display = "none"; });
  new ResizeObserver(() => cy && cy.resize()).observe(box);
}
function atualizarLuas2D() {
  if (!cy) return;
  cy.nodes().filter(n => n.data().lua !== undefined).remove();
  cy.edges().filter(e => e.data().lua !== undefined).remove();
  if (!selecionado) return;
  const d = byPath[selecionado], p = cy.$id(selecionado).position();
  const n = Math.min(d.n_chunks, LUA_MAX), R = 26 + 6 * Math.sqrt(d.n_links + d.n_chunks);
  const els = [];
  for (let i = 0; i < n; i++) {
    const a = TAU * i / n;
    els.push({ data: { id: "lua|" + d.path + "|" + i, lua: i, path: d.path, tam: 6 },
      position: { x: p.x + R * Math.cos(a), y: p.y + R * Math.sin(a) } });
    els.push({ data: { id: "elua|" + d.path + "|" + i, source: d.path, target: "lua|" + d.path + "|" + i, lua: true } });
  }
  cy.add(els);
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
  if (cy) atualizarLuas2D();
  renderLista();
}
function setModo(m) {
  if (m === modo) return;
  modo = m;
  $("g3d").style.display = m === "3d" ? "" : "none";
  $("g2d").style.display = m === "2d" ? "" : "none";
  if (m === "2d") { if (!cy) init2D(); else cy.resize(); }
  if (g3d) g3d.pauseAnimation(m !== "3d");
  document.querySelectorAll(".vtog").forEach(b => b.classList.toggle("on", b.dataset.m === m));
}

// ---------- lista
function renderLista() {
  const ul = $("list"); ul.replaceChildren();
  const vis = docs.filter(matchBusca)
    .sort((a, b) => a.galaxia - b.galaxia || (a.id || a.path).localeCompare(b.id || b.path, "pt-BR", { numeric: true }));
  $("conta").textContent = busca ? `${vis.length} de ${docs.length}` : `${docs.length} documentos`;
  for (const d of vis) {
    const li = el("li");
    li.append(el("div", rotulo(d), "t"), el("div", [d.tipo, d.subpasta || d.pasta, gal[d.galaxia].nome].filter(Boolean).join(" · "), "m"));
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
  if (!path || !byPath[path]) { box.replaceChildren(el("p", "Selecione um documento na lista, no grafo ou numa lua.", "m")); return; }
  const d = byPath[path];
  box.replaceChildren(el("h2", rotulo(d), "title"));
  const meta = el("div", null, "meta");
  for (const x of [d.tipo, d.subpasta || d.pasta, gal[d.galaxia].nome]) if (x) meta.append(el("span", x, "chip"));
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

// ---------- start
document.getElementById("name").textContent = D.bundle + " — galáxias do bundle";
$("stats").textContent = `${docs.length} documentos · ${D.chunks.length} chunks · ${D.links.length} links · ${KGAL} galáxias`;
$("vtog3d").onclick = () => setModo("3d");
$("vtog2d").onclick = () => setModo("2d");
$("corpo").onchange = e => { corCampo = e.target.value; repaleta(); };
$("q").addEventListener("input", e => { busca = e.target.value.trim().toLowerCase(); renderLista(); repintar(); });
repaleta();
renderLista();
init3D();
if (modo === "2d") {  // sem ForceGraph3D (sem WebGL): a vista 2D assume
  $("g3d").style.display = "none";
  $("g2d").style.display = "";
  init2D();
}
window.addEventListener("hashchange", fromHash);
fromHash();
</script>
</body>
</html>
"""
