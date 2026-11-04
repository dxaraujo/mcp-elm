"""Visualização do bundle local: <bundle>/explorar.html — o bundle num grafo 3D simples.

Grafo no molde do exemplo large-graph do 3d-force-graph (nó = documento, cor automática por type, rótulo e
clique; o "big bang" de entrada é a simulação padrão partindo da origem). O combobox do header alterna o
conjunto de arestas: "links" (os links entre artefatos, lidos do .md no disco com a mesma lógica do
elm_ler_req_local) ou "semântico" (k vizinhos mais próximos pelo cosseno dos vetores do índice — média
normalizada dos chunks por documento). Lista à esquerda (busca e tipo), documento à direita (Markdown,
chunks e links). Documentos e links saem do disco; do índice (somente leitura, nada de reindexação aqui) saem
só os vetores e os chunks — com o índice incompleto, o modo semântico e os chunks ficam só com os indexados. As
bibliotecas (3d-force-graph, marked, DOMPurify) vêm do CDN jsDelivr, com versão fixa.
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

K_SEM, MIN_SEM = 3, 0.5  # arestas semânticas: k vizinhos por documento, cosseno mínimo


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
    """Média normalizada dos vetores dos chunks, por documento."""
    por_doc: dict[str, list[np.ndarray]] = {}
    for path, vec in con.execute("select path, vec from chunks order by path, cid"):
        por_doc.setdefault(path, []).append(np.frombuffer(vec, dtype=np.float32))
    out = {}
    for path, vs in por_doc.items():
        m = np.stack(vs).mean(axis=0)
        norma = np.linalg.norm(m)
        out[path] = m / norma if norma else m
    return out


def vizinhos(paths: list[str], X: np.ndarray, k: int = K_SEM, minimo: float = MIN_SEM) -> list[dict]:
    """Arestas semânticas: para cada documento, os k mais próximos por cosseno (linhas de X normalizadas),
    só os com cosseno ≥ minimo; par não direcionado, sem duplicar, em ordem de (de, para)."""
    n = len(paths)
    if n < 2:
        return []
    S = X @ X.T
    np.fill_diagonal(S, -np.inf)
    k = min(k, n - 1)
    top = np.argpartition(-S, k - 1, axis=1)[:, :k]
    pares: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in map(int, top[i]):
            if S[i, j] >= minimo:
                pares[(min(i, j), max(i, j))] = float(S[i, j])
    return [{"de": paths[a], "para": paths[b], "cos": round(v, 3)} for (a, b), v in sorted(pares.items())]


def gerar(cfg: dict) -> dict:
    """Gera <bundle>/explorar.html. → {path, documentos, chunks, links, semanticos, gerado_em}"""
    root, disco = local._bundle(cfg)
    con = _indice(root)  # do índice saem só os vetores (arestas semânticas) e os chunks (painel do documento)
    try:
        vetores = _vetores(con)
        chunks: dict[str, list[list[str]]] = {}
        for p, secao, texto in con.execute("select path, section, text from chunks order by path, cid"):
            chunks.setdefault(p, []).append([secao, texto.split("\n", 1)[-1]])  # sem o prefixo de contexto
    finally:
        con.close()
    n_chunks = sum(map(len, chunks.values()))

    # documentos e links saem do disco: o modo "links" não depende do índice estar completo
    paths = sorted(disco)
    ids = local.by_id(disco)
    arestas: dict[tuple[str, str], list[str]] = {}
    for p in paths:
        for lk in local.links(disco[p], disco, ids):
            arestas.setdefault((p, lk["path"]), []).append(lk["rel"])
    links = [{"de": a, "para": b, "rel": " · ".join(r)} for (a, b), r in sorted(arestas.items())]

    if len({v.shape[0] for v in vetores.values()}) > 1:  # índice com dois embedders: sem como comparar
        raise Erro("precondicao", "índice com vetores de dimensões diferentes: rode "
                    "elm_indexar_local (forcar)", "indice")
    dim = next(iter(vetores.values()), np.zeros(0)).shape[0]
    X = np.stack([vetores.get(p, np.zeros(dim)) for p in paths])  # não indexado: linha zero, nunca vizinho
    sem = vizinhos(paths, X)

    dados = {"bundle": os.path.basename(root), "chunks": n_chunks, "links": links, "sem": sem,
             "docs": [{"path": p, "id": d["id"], "titulo": d["title"] or "", "tipo": d["type"] or "(sem tipo)",
                       "descricao": d["description"], "corpo": d["body"], "chunks": chunks.get(p, [])}
                      for p, d in ((p, disco[p]) for p in paths)]}
    js = json.dumps(dados, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    agora = datetime.now(timezone.utc).astimezone()
    out = Path(root) / "explorar.html"
    out.write_text(PAGINA.replace("__DATA__", js).replace("__BUILD__", agora.strftime("%d/%m %H:%M")),
                   encoding="utf-8")
    return {"path": str(out), "documentos": len(paths), "chunks": n_chunks, "links": len(links),
            "semanticos": len(sem), "gerado_em": agora.isoformat(timespec="seconds")}


PAGINA = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Explorar bundle</title>
<script src="https://cdn.jsdelivr.net/npm/3d-force-graph@1.80.1/dist/3d-force-graph.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@18.1.1/lib/marked.umd.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.4.17/dist/purify.min.js"></script>
<style>
:root { --bg:#fafaf9; --panel:#fff; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --accent:#2563eb; --chip:#f5f5f4; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0c0a09; --panel:#1c1917; --fg:#f5f5f4; --muted:#a8a29e; --line:#292524; --accent:#60a5fa; --chip:#292524; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.5 system-ui, sans-serif; }
header { height:48px; padding:0 16px; border-bottom:1px solid var(--line); display:flex; gap:16px; align-items:center; }
header h1 { font-size:16px; margin:0; }
#stats { color:var(--muted); font-size:13px; }
#modo { width:auto; margin-left:auto; }
main { display:grid; grid-template-columns:290px 1fr 420px; height:calc(100vh - 48px); }
main.fechado { grid-template-columns:290px 1fr; }
main.fechado #detail { display:none; }
aside, #detail { overflow:auto; background:var(--panel); }
aside { border-right:1px solid var(--line); overflow-x:hidden; }
#detail { border-left:1px solid var(--line); padding:16px 20px; }
#filtros { padding:10px 12px; display:grid; gap:8px; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--panel); }
input, select { width:100%; padding:6px 8px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); font:inherit; }
#conta { color:var(--muted); font-size:12px; }
#list { list-style:none; margin:0; padding:0; }
#list li { padding:8px 12px; border-bottom:1px solid var(--line); cursor:pointer; }
#list li:hover, #list li.on { background:var(--chip); }
#list .t { font-weight:600; }
.m { color:var(--muted); font-size:12px; }
#graph { position:relative; overflow:hidden; background:#000011; min-width:0; }
#graph .aviso { position:absolute; inset:0; display:grid; place-items:center; color:#94a3b8; }
#graph .legenda { position:absolute; z-index:1; left:10px; top:8px; color:#cbd5e1; font-size:12px; pointer-events:none; }
.legenda span { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }
#graph .build { position:absolute; z-index:1; right:10px; bottom:8px; color:#64748b; font-size:12px; pointer-events:none; }
.topo { display:flex; gap:8px; align-items:flex-start; justify-content:space-between; }
.topo h2 { font-size:18px; margin:0 0 6px; }
.topo button { border:1px solid var(--line); background:var(--bg); color:var(--fg); border-radius:6px; width:28px; height:28px; flex:none; cursor:pointer; }
.chip { background:var(--chip); border-radius:999px; padding:2px 8px; font-size:12px; margin-right:6px; }
#detail a { color:var(--accent); }
#detail h3 { font-size:12px; text-transform:uppercase; color:var(--muted); margin:24px 0 6px; border-top:1px solid var(--line); padding-top:12px; }
.desc { color:var(--muted); font-style:italic; }
.link { display:block; text-decoration:none; }
.md { overflow-wrap:anywhere; }
.md table { border-collapse:collapse; display:block; overflow:auto; }
.md th, .md td { border:1px solid var(--line); padding:4px 8px; text-align:left; vertical-align:top; }
.md pre, .md code { background:var(--chip); border-radius:4px; }
.md pre { padding:10px; overflow:auto; }
.md img { max-width:100%; }
pre.raw { white-space:pre-wrap; font-size:13px; margin:4px 0 8px; }
#detail summary { cursor:pointer; font-size:13px; padding:2px 0; }
@media (max-width: 900px) {
  main, main.fechado { grid-template-columns:1fr; height:auto; }
  aside { max-height:40vh; }
  #graph { height:60vh; }
}
</style>
</head>
<body>
<header>
  <h1 id="nome"></h1>
  <span id="stats"></span>
  <select id="modo" aria-label="Arestas do grafo">
    <option value="links">Links dos artefatos</option>
    <option value="sem">Semântico (embeddings)</option>
  </select>
</header>
<main id="layout" class="fechado">
  <aside>
    <div id="filtros">
      <input id="q" type="search" placeholder="Filtrar por título, id, descrição…" aria-label="Filtro">
      <select id="tipo" aria-label="Tipo de artefato"></select>
      <div id="conta"></div>
    </div>
    <ul id="list"></ul>
  </aside>
  <section id="graph" aria-label="Grafo do bundle"><div class="build">build __BUILD__</div></section>
  <section id="detail"></section>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const docs = D.docs;
const byPath = Object.fromEntries(docs.map(d => [d.path, d]));
const byId = Object.fromEntries(docs.filter(d => d.id).map(d => [d.id, d]));
const saida = {}, entrada = {};
for (const l of D.links) { (saida[l.de] ||= []).push(l); (entrada[l.para] ||= []).push(l); }
let selecionado = null;

const $ = id => document.getElementById(id);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const rotulo = d => (d.id ? d.id + " — " : "") + d.titulo;
const pasta = d => d.path.slice(0, d.path.lastIndexOf("/"));
const abrir = p => { location.hash = "#doc=" + encodeURIComponent(p); };

// ---------- header + grafo (exemplo large-graph do 3d-force-graph; nós sem posição → big bang da origem)
$("nome").textContent = D.bundle;
const arestas = () => $("modo").value === "sem" ? D.sem : D.links;
// cor e tamanho por tipo (volume; o raio cresce com a raiz cúbica): UC = HF > REG > ET > EL > MSG > GLO > resto.
// Cor pela quantidade: os tipos numerosos (REG, ET — centenas) em tons suaves para não dominar a cena;
// os poucos (UC, HF, MSG, GLO, resto) em cores vivas para saltarem no meio deles
const TIPOS = [[/^Caso de Uso$/, 40, "#fbbf24"], [/^História de Usuário$/, 40, "#f43f5e"], [/^Regra/, 20, "#3f7d5c"],
  [/^Especificação Técnica/, 12, "#41618f"], [/Leiaute|Layout/i, 7, "#a78bfa"],  // ponytail: EL casado por nome, ajuste ao ver o type real
  [/^Mensagem/, 4, "#22d3ee"], [/Glossário/, 2, "#f472b6"]];
const estilo = t => TIPOS.find(([re]) => re.test(t)) || [, 1, "#e2e8f0"];
const ordemTipo = t => { const i = TIPOS.findIndex(([re]) => re.test(t)); return i < 0 ? TIPOS.length : i; };
const dadosGrafo = () => ({
  nodes: docs.map(d => ({ id: d.path, tipo: d.tipo, nome: rotulo(d), val: estilo(d.tipo)[1], color: estilo(d.tipo)[2] })),
  links: arestas().map(l => ({ source: l.de, target: l.para })),
});
const stats = () => { $("stats").textContent = `${docs.length} documentos · ${D.chunks} chunks · ${arestas().length} links`; };
stats();
const box = $("graph");
let grafo = null;
if (window.ForceGraph3D) {
  grafo = new ForceGraph3D(box)
    .graphData(dadosGrafo())
    .nodeLabel(n => `${n.tipo}: ${n.nome}`)
    .linkOpacity(0.18)  // grafo muito interligado: linhas discretas, os nós mandam
    .onNodeClick(n => abrir(n.id));
  new ResizeObserver(() => grafo.width(box.clientWidth).height(box.clientHeight)).observe(box);
} else box.append(el("div", "3d-force-graph indisponível (sem acesso ao CDN ou sem WebGL).", "aviso"));
$("modo").onchange = () => { stats(); if (grafo) grafo.graphData(dadosGrafo()); repintar(false); };
// o grafo segue as barras laterais: o filtro da esquerda apaga o que ficou de fora; o documento aberto
// (lista, nó ou link do painel) esconde o resto e deixa só ele, os vizinhos e as arestas dele.
// enquadrar: a câmera ajusta ao que ficou ativo (sem posição ainda — antes do 1º tick — não mexe)
const ponta = x => typeof x === "object" ? x.id : x;  // o 3d-force-graph troca source/target (path) pelos nós
function repintar(enquadrar) {
  if (!grafo) return;
  const foco = selecionado && new Set([selecionado, ...arestas().flatMap(l =>
    l.de === selecionado ? [l.para] : l.para === selecionado ? [l.de] : [])]);
  const ativo = id => foco ? foco.has(id) : filtro(byPath[id]);
  grafo.nodeVisibility(n => !foco || foco.has(n.id))
    .nodeColor(n => ativo(n.id) ? n.color : "#1e293b")
    .linkVisibility(l => foco ? ponta(l.source) === selecionado || ponta(l.target) === selecionado
                              : ativo(ponta(l.source)) && ativo(ponta(l.target)))
    .linkOpacity(foco ? 0.6 : 0.18);
  if (enquadrar) enquadrarNos(ativo);
}
// câmera na esfera que envolve os nós ativos, mantendo a direção de olhar. Não usa o zoomToFit: o bbox dele
// sai dos objetos 3D, e o nó que volta a ser visível (fechar o documento) só ganha posição no próximo render —
// o fechar enquadrava só os vizinhos do documento que acabou de sair
function enquadrarNos(ativo) {
  const ns = grafo.graphData().nodes.filter(n => ativo(n.id) && n.x !== undefined);  // antes do 1º tick: sem posição
  if (!ns.length) return;
  const c = ["x", "y", "z"].map(k => ns.reduce((s, n) => s + n[k], 0) / ns.length);
  const r = Math.max(30, ...ns.map(n => Math.hypot(n.x - c[0], n.y - c[1], n.z - c[2])));
  const p = grafo.cameraPosition(), d = Math.hypot(p.x - c[0], p.y - c[1], p.z - c[2]) || 1;
  const k = 1.15 * r / Math.sin(grafo.camera().fov * Math.PI / 360) / d;
  grafo.cameraPosition({ x: c[0] + (p.x - c[0]) * k, y: c[1] + (p.y - c[1]) * k, z: c[2] + (p.z - c[2]) * k },
                       { x: c[0], y: c[1], z: c[2] }, 1000);
}

// ---------- barra esquerda: busca + tipo + lista
const contTipo = {};
for (const d of docs) contTipo[d.tipo] = (contTipo[d.tipo] || 0) + 1;
$("tipo").append(new Option("Todos", ""));
const legenda = el("div", null, "legenda");  // legenda das cores sobre o grafo (só os tipos presentes)
for (const t of Object.keys(contTipo).sort((a, b) => ordemTipo(a) - ordemTipo(b) || contTipo[b] - contTipo[a] || a.localeCompare(b, "pt-BR"))) {
  $("tipo").append(new Option(`${t} (${contTipo[t]})`, t));
  const i = el("div", `${t} (${contTipo[t]})`); i.prepend(Object.assign(el("span"), { style: `background:${estilo(t)[2]}` })); legenda.append(i); }
box.append(legenda);
const filtro = d => {
  const q = $("q").value.trim().toLowerCase(), tipo = $("tipo").value;
  return (!tipo || d.tipo === tipo) && (!q || [d.id, d.titulo, d.descricao, d.path].some(x => x && x.toLowerCase().includes(q)));
};
const ordenados = [...docs].sort((a, b) => (a.id || a.path).localeCompare(b.id || b.path, "pt-BR", { numeric: true }));
function renderLista() {
  const vis = ordenados.filter(filtro);
  $("conta").textContent = vis.length === docs.length ? `${docs.length} documentos` : `${vis.length} de ${docs.length}`;
  $("list").replaceChildren(...vis.map(d => {
    const li = el("li");
    li.append(el("div", rotulo(d), "t"), el("div", `${d.tipo} · ${pasta(d)}`, "m"));
    li.onclick = () => abrir(d.path);
    if (d.path === selecionado) li.className = "on";
    return li;
  }));
}
$("q").oninput = $("tipo").onchange = () => { renderLista(); repintar(!selecionado); };  // filtro novo: enquadra o que sobrou

// ---------- barra direita: documento (Markdown com links internos → #doc=)
function resolver(base, alvo) {  // link relativo do .md → path do bundle (como o local.resolve)
  alvo = decodeURIComponent(alvo).split("#")[0].trim();
  if (!alvo || /^[a-z][a-z0-9+.-]*:/i.test(alvo)) return null;
  const partes = alvo.startsWith("/") ? alvo.slice(1).split("/") : base.split("/").slice(0, -1).concat(alvo.split("/"));
  const st = [];
  for (const s of partes) {
    if (s === "..") { if (!st.length) return null; st.pop(); }
    else if (s && s !== ".") st.push(s);
  }
  const p = st.join("/");
  if (byPath[p]) return p;
  const m = /^(\d+)(?:[ _:-].*)?$/.exec(st[st.length - 1]?.replace(/\.md$/, "") || "");  // arquivo por id
  return m && byId[m[1]] ? byId[m[1]].path : null;
}
function corpo(d) {
  if (!(window.marked && window.DOMPurify)) return el("pre", d.corpo, "raw");
  let md = d.corpo.replace(/\[(\d{3,})[ .\-–—]([^\]]+)\](?!\()/g,  // citação do ELM "[id TÍTULO]" → link interno
    (s, id, t) => byId[id] ? `[${t}](#doc=${encodeURIComponent(byId[id].path)})` : s);
  md = md.replace(/\]\(([^)\s]+)\)/g, (s, href) => { const p = resolver(d.path, href); return p ? `](#doc=${encodeURIComponent(p)})` : s; });
  const div = el("div", null, "md");
  div.innerHTML = DOMPurify.sanitize(marked.parse(md));
  const base = new URL(d.path.split("/").map(encodeURIComponent).join("/"), location.href);
  for (const a of div.querySelectorAll("a[href]:not([href^='#'])")) { a.href = new URL(a.getAttribute("href"), base).href; a.target = "_blank"; a.rel = "noopener"; }
  for (const img of div.querySelectorAll("img[src]")) img.src = new URL(img.getAttribute("src"), base).href;
  return div;
}
function listaLinks(titulo, itens, chave) {
  const out = [el("h3", `${titulo} (${itens.length})`)];
  for (const l of itens) {
    const d = byPath[l[chave]], a = el("a", d ? rotulo(d) : l[chave], "link");
    a.prepend(el("span", l.rel + " · ", "m"));
    if (d) a.href = "#doc=" + encodeURIComponent(d.path);
    out.push(a);
  }
  return out;
}
function listaChunks(d) {  // o que o índice guardou do documento: cada chunk recolhível (seção como título)
  const out = [el("h3", `Chunks (${d.chunks.length})`)];
  if (!d.chunks.length) out.push(el("div", "não indexado", "m"));
  for (const [i, [secao, texto]] of d.chunks.entries()) {
    const item = el("details");
    item.append(el("summary", `${i + 1}. ${secao || "(início)"}`), el("pre", texto, "raw"));
    out.push(item);
  }
  return out;
}
function mostrar(path) {
  selecionado = byPath[path] ? path : null;
  $("layout").classList.toggle("fechado", !selecionado);
  renderLista();
  repintar(true);
  if (!selecionado) return;
  const d = byPath[path], fechar = el("button", "×");
  fechar.setAttribute("aria-label", "Fechar");
  // limpar o hash dispara o hashchange → mostrar(null), o mesmo caminho da abertura. Sem a History API:
  // em file:// alguns navegadores (Firefox) lançam SecurityError e o painel ficava preso no artefato
  fechar.onclick = () => { location.hash = ""; };
  const topo = el("div", null, "topo"), meta = el("div");
  topo.append(el("h2", rotulo(d)), fechar);
  const md = el("a", "abrir .md"); md.href = d.path.split("/").map(encodeURIComponent).join("/"); md.target = "_blank";
  meta.append(el("span", d.tipo, "chip"), el("span", pasta(d), "chip"), md);
  $("detail").replaceChildren(topo, meta, ...(d.descricao ? [el("p", d.descricao, "desc")] : []), corpo(d), ...listaChunks(d),
    ...listaLinks("Links de saída", saida[path] || [], "para"), ...listaLinks("Links de entrada", entrada[path] || [], "de"));
  $("detail").scrollTop = 0;
}
const doHash = () => mostrar(location.hash.startsWith("#doc=") ? decodeURIComponent(location.hash.slice(5)) : null);
window.addEventListener("hashchange", doHash);
doHash();
</script>
</body>
</html>
"""
