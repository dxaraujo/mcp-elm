"""DOORS Next: buscar, ler e publicar requisitos na configuração (stream) do pa; documento OKF do bundle."""
from __future__ import annotations

import posixpath
import re
from datetime import datetime, timezone
from html import unescape
from importlib import metadata

from rdflib import Literal as RdfLiteral, URIRef
from rdflib.namespace import DCTERMS, RDF

from . import oslc
from .contrato import Erro
from .documento import document, one_line, resource_url, slug, to_markdown, to_xhtml, utc_datetime
from .http import get_session

COMPONENT, STREAM = "/rm/cm/component/{id}", "/rm/cm/stream/{id}"
FOLDER, TYPE = "/rm/folders/{id}", "/rm/types/{id}"
SEARCH_SELECT = "dcterms:identifier,dcterms:title,dcterms:modified,oslc:instanceShape"
# preenchidos pelo servidor ou com lugar próprio: não entram em `links` do documento
SYSTEM = {"dcterms:title", "dcterms:identifier", "dcterms:description", "jazz_rm:primaryText", "nav:parent",
          "oslc:instanceShape", "dcterms:created", "dcterms:modified", "dcterms:creator", "dcterms:contributor",
          "oslc_config:component", "process:projectArea"}
RESOURCE_TYPES = {"oslc:Resource", "oslc:AnyResource", "oslc:LocalResource", ""}
EMBEDDING = "http://www.ibm.com/xmlns/rdm/types/Embedding"  # derivado dos embeds do texto
_EMBED = re.compile(r"<a\s[^>]*>")


def _url(template: str, identifier: str) -> str:
    return get_session().url(template.format(id=identifier))


def _rm(cfg: dict) -> dict:
    if "rm" not in cfg:
        raise Erro("config", "pa sem a seção rm (DOORS Next)", "rm")
    return cfg["rm"]


def stream(cfg: dict) -> str:
    return _url(STREAM, _rm(cfg)["configuracao"])


def _base(cfg: dict) -> str:
    return oslc.query_base(oslc.provider("rm", _rm(cfg)["id"]), oslc.OSLC_RM.Requirement, stream(cfg))


def gerador() -> str:
    """Ator OKF `generated.by`: 'process:elm-mcp/<versão>' (sem a distribuição instalada, sem versão)."""
    try:
        return f"process:elm-mcp/{metadata.version('mcp-elm')}"
    except metadata.PackageNotFoundError:
        return "process:elm-mcp"


def recurso(cfg: dict, req_id) -> dict:
    """Requisito pelo id numérico, lido na stream do pa."""
    if not str(req_id).isdigit():
        raise Erro("entrada", f"id de requisito inválido: {req_id}", "id")
    found = oslc.query(_base(cfg), where=oslc.where_eq("dcterms:identifier", int(req_id)),
                       select="dcterms:identifier", limit=1, configuration=stream(cfg))
    if not found:
        raise Erro("nao_encontrado", f"requisito {req_id} não encontrado", "id")
    return oslc.get(found[0]["url"], stream(cfg))


def url_req(cfg: dict, req_id) -> str:
    # o link guarda a URI sem ?oslc_config.context (o ELM resolve a versão pela configuração)
    return recurso(cfg, req_id)["url"].split("?")[0]


def _link(r: dict, pred: str) -> dict | None:
    return next(iter(r["links"].get(pred, [])), None)


def _tipo(r: dict, config: str) -> str | None:
    shape = _link(r, "oslc:instanceShape")
    return oslc.shape(shape["url"], config)["title"] if shape else None


def _pasta(url: str, config: str) -> str:
    """Caminho da pasta sem a 'root' ('01-Req/Funcionais')."""
    folder = oslc.cached(url, config)
    if folder["title"] == "root":
        return ""
    parent = _link(folder, "nav:parent")
    return posixpath.join(_pasta(parent["url"], config), folder["title"] or "") if parent else folder["title"] or ""


def caminho(r: dict, config: str) -> str:
    """Arquivo do requisito no bundle: '<pasta>/<id>-<slug do título>.md'."""
    folder = _link(r, "nav:parent")
    name = f"{r['id']}-{slug(one_line(r['title']) or '')}.md"
    return posixpath.join(_pasta(folder["url"], config), name) if folder else name


def buscar(cfg: dict, texto: str, limite: int = 20) -> dict:
    config = stream(cfg)
    # só o texto: no 1.0.43 texto junto com pasta/tipo dava HTTP 400
    found = oslc.query(_base(cfg), search_terms=texto, select=SEARCH_SELECT, limit=limite + 1, configuration=config)
    return {"itens": [{"id": int(r["id"]), "titulo": one_line(r["title"]), "tipo": _tipo(r, config),
                       "modificado": utc_datetime(r["properties"].get("dcterms:modified"))} for r in found[:limite]],
            "truncado": len(found) > limite}


def documento(cfg: dict, req_id, bundle_ids: set[str] | None = None, artefatos: dict | None = None) -> dict:
    """Requisito em partes: {recurso, tipo, pasta, corpo, links, normalizado, caminho}.

    `bundle_ids` (bundle da sdd-sync): artefato do bundle vira embed com alvo = arquivo relativo; o resto, link
    para a URL. Sem ele, todo artefato citado vira [id título](URL). `normalizado`: algum link mudou de forma (link
    da UI web, embed fora do bundle, hyperlink para artefato do bundle) e o arquivo difere do texto no ELM."""
    config, bundle = stream(cfg), bundle_ids is not None
    r = recurso(cfg, req_id)
    artefatos = {} if artefatos is None else artefatos  # um GET por artefato citado, reaproveitado no download
    own_dir = posixpath.dirname(caminho(r, config)) if bundle else ""
    normalizado = False

    def artefato(url: str) -> dict | None:
        url = get_session().url(url.split("?")[0])
        if url not in artefatos:
            try:
                artefatos[url] = oslc.get(url, config)
            except RuntimeError:  # ilegível (sem permissão, removido) não derruba a leitura
                artefatos[url] = None
        return artefatos[url]

    html = oslc.markup(r["url"], "jazz_rm:primaryText", config) or ""
    embeds = embeds_de(html)

    def ref(href: str):
        nonlocal normalizado
        url = resource_url(href)
        found = artefato(url) if "/rm/resources/" in url else None
        if found is None:
            return None
        normalizado |= url != href
        no_bundle = bundle and found["id"] in bundle_ids
        if bundle:
            normalizado |= no_bundle != (href in embeds)  # artefato do bundle = embed; o resto = link
        alvo = posixpath.relpath(caminho(found, config), own_dir or ".") if no_bundle else found["url"].split("?")[0]
        return f"{found['id']} {one_line(found['title'])}", alvo

    def rotulo(link: dict) -> str:
        url = link["url"]
        if "/rm/resources/" in url:
            found = artefato(url)
            return f"{found['id']}: {one_line(found['title'])}" if found else url.split("?")[0]
        if "/jts/users/" in url:
            return url.rsplit("/", 1)[-1]
        return one_line(link.get("title")) or oslc.title(url, config) or url

    links = {}
    shape = _link(r, "oslc:instanceShape")
    for prop in oslc.shape(shape["url"], config)["properties"] if shape else []:
        key = prop["predicate"]
        if (prop["title"] and key not in SYSTEM and key != EMBEDDING and key in r["links"]
                and not prop["allowed_values"] and prop["value_type"] in RESOURCE_TYPES):
            links[prop["title"]] = [rotulo(link) for link in r["links"][key]]
    folder = _link(r, "nav:parent")
    return {"recurso": r, "tipo": _tipo(r, config), "corpo": to_markdown(html, ref), "links": links,
            "pasta": _pasta(folder["url"], config) if folder else None, "caminho": caminho(r, config),
            "normalizado": normalizado}


def embeds_de(html: str) -> list[str]:
    """hrefs dos artefatos embutidos no XHTML do DOORS Next."""
    return [unescape(m.group(1)) for tag in _EMBED.findall(html) if 'class="embedded"' in tag
            for m in [re.search(r'href="([^"]+)"', tag)] if m]


def okf(doc: dict) -> str:
    """Arquivo do bundle (Google OKF v0.2): cabeçalho OKF + provenance + extensões RM, e o corpo."""
    r = doc["recurso"]
    props, url = r["properties"], r["url"].split("?")[0]
    login = lambda pred: next((f"human:{link['url'].rsplit('/', 1)[-1]}" for link in r["links"].get(pred, [])), None)  # noqa: E731
    # verified/status/stale_after omitidos: o DOORS Next não tem sinal real deles; nunca inventados
    source = {"id": "doors-next", "resource": url, "author": login("dcterms:creator"),
              "last_modified": utc_datetime(props.get("dcterms:modified")), "last_modified_by": login("dcterms:contributor")}
    head = {"type": doc["tipo"], "title": one_line(r["title"]),
            "description": one_line(props.get("dcterms:description")) or None, "resource": url,
            "tags": [doc["pasta"]] if doc["pasta"] else None, "sources": [{k: v for k, v in source.items() if v}],
            "generated": {"by": gerador(), "at": utc_datetime(datetime.now(timezone.utc))},
            "id": int(r["id"]), "created": utc_datetime(props.get("dcterms:created")), "links": doc["links"] or None}
    return document(head, doc["corpo"])


def ler(cfg: dict, req_id) -> str:
    doc = documento(cfg, req_id)
    r = doc["recurso"]
    head = {"id": int(r["id"]), "titulo": one_line(r["title"]), "tipo": doc["tipo"], "pasta": doc["pasta"],
            "modificado": utc_datetime(r["properties"].get("dcterms:modified")), "url": r["url"].split("?")[0],
            "links": doc["links"] or None}
    return document(head, doc["corpo"])


def xhtml(cfg: dict, texto_md: str, embutir) -> RdfLiteral:
    """Markdown -> jazz_rm:primaryText. `[x](id)` cita o requisito; vira embed se ele está em `embutir`."""
    body = texto_md if texto_md.lstrip().startswith("<") else to_xhtml(
        texto_md, lambda ref: url_req(cfg, ref) if ref.isdigit() else get_session().url(ref), embutir)
    return RdfLiteral(f'<div xmlns="{oslc.XHTML}">{body}</div>', datatype=RDF.XMLLiteral)


def atualizar(cfg: dict, req_id, titulo: str, texto_md: str, embutir=None, modificado_em: str | None = None) -> dict:
    config = stream(cfg)
    r = recurso(cfg, req_id)
    no_elm = utc_datetime(r["properties"].get("dcterms:modified"))
    if modificado_em and no_elm and no_elm > utc_datetime(modificado_em):
        raise Erro("conflito", f"ELM modificado em {no_elm}, depois de {utc_datetime(modificado_em)}", "modificado_em")
    if embutir is None:  # sem a lista, mantém como embed o que já é embed no ELM
        embutir = embeds_de(oslc.markup(r["url"], "jazz_rm:primaryText", config) or "")
    oslc.update(r["url"], {"dcterms:title": titulo, "jazz_rm:primaryText": xhtml(cfg, texto_md, embutir)},
                configuration=config)
    return {"id": int(r["id"]), "url": r["url"].split("?")[0], "acao": "atualizado"}


def publicar(cfg: dict, sigla: str, titulo: str, texto_md: str, req_id=None, modificado_em: str | None = None,
             embutir: list[str] | None = None) -> dict:
    if req_id:
        return atualizar(cfg, req_id, titulo, texto_md, embutir, modificado_em)
    rm, config = _rm(cfg), stream(cfg)
    art = rm["artefatos"].get(sigla)
    if art is None:
        raise Erro("config", f"sigla '{sigla}' não está em rm.artefatos ({', '.join(rm['artefatos'])})",
                   f"rm.artefatos.{sigla}")
    tipo = _url(TYPE, art["tipo-id"])
    factories = oslc.creation_factories(oslc.provider("rm", rm["id"]), oslc.OSLC_RM.Requirement, config)
    factory = next((f["creation"] for f in factories if tipo in f["resource_shapes"]), factories[0]["creation"])
    g = oslc.new_resource(oslc.OSLC_RM.Requirement, {
        DCTERMS.title: RdfLiteral(titulo), DCTERMS.description: RdfLiteral(""),
        oslc.OSLC.instanceShape: URIRef(tipo), oslc.JAZZ_RM.primaryText: xhtml(cfg, texto_md, embutir or ()),
        oslc.RM_NAV.parent: URIRef(_url(FOLDER, art["pasta-id"]))})
    novo = oslc.get(oslc.create(factory, g, config), config)
    return {"id": int(novo["id"]), "url": novo["url"].split("?")[0], "acao": "criado"}


# --- descoberta para a sdd-setup

def _pastas(component_url: str, config: str) -> dict[str, str]:
    """{caminho sem 'root': FR_...} de todas as pastas do componente."""
    g, subject, _ = oslc.fetch(component_url)
    pa = next((str(o) for p, o in g.predicate_objects(subject) if str(p).endswith("projectArea")), None)
    if pa is None:
        raise Erro("nao_encontrado", f"componente {component_url} não informa a project area")
    base = oslc.query_base(oslc.provider("rm", oslc.item_id(pa)), oslc.RM_NAV.folder, config)
    out, pending = {}, [(base, "")]
    while pending:  # shortcut: 1 query por pasta (nav:subfolders); consulta única se ficar lento
        url, path = pending.pop(0)
        for folder in oslc.query(url, select=None, configuration=config, limit=oslc.MAX_LIMIT):
            name = "" if folder["title"] == "root" and not path else posixpath.join(path, folder["title"] or "")
            if name:
                out[name] = oslc.item_id(folder["url"])
            pending += [(sub["url"], name) for sub in folder["links"].get("nav:subfolders", [])]
    return out


def descobrir(trecho: str) -> dict:
    pas = [{"nome": p["title"], "id": p["project_area_uuid"], "url": p["url"]} for p in oslc.providers("rm")
           if trecho.casefold() in p["title"].casefold()]
    if len(pas) != 1:
        return {"pas": [{k: p[k] for k in ("nome", "id")} for p in pas]}
    pa = pas[0]
    componentes = oslc.services(pa["url"])["components"]
    if not componentes:
        raise Erro("nao_encontrado", f"PA {pa['nome']} sem componente no DOORS Next")
    config = next((c["url"] for c in oslc.configurations(componentes[0]) if "oslc_config:Stream" in c["types"]), None)
    if config is None:
        raise Erro("nao_encontrado", f"componente de {pa['nome']} sem stream")
    shapes = {s for f in oslc.creation_factories(pa["url"], oslc.OSLC_RM.Requirement, config) for s in f["resource_shapes"]}
    return {"pa": {"nome": pa["nome"], "id": pa["id"]}, "componente": oslc.item_id(componentes[0]),
            "stream": oslc.item_id(config), "pastas": _pastas(componentes[0], config),
            "tipos": {oslc.shape(s, config)["title"]: oslc.item_id(s) for s in sorted(shapes)}}
