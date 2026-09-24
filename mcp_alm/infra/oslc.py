"""OSLC genérico: vocabulário, RDF <-> recurso, get/query/create/update, descoberta e configurações.

Recurso devolvido pelas tools:
    {"url", "id", "title", "types": [...], "properties": {qname: valor|[valores]},
     "links": {qname: [{"url", "title"?}]}}
"""
from __future__ import annotations

import re
from functools import lru_cache
from html import unescape

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, FOAF, RDF, RDFS, XSD

from .http import RDF_XML, get_session

# --- vocabulário

OSLC = Namespace("http://open-services.net/ns/core#")
OSLC_RM = Namespace("http://open-services.net/ns/rm#")
OSLC_CM = Namespace("http://open-services.net/ns/cm#")
OSLC_CMX = Namespace("http://open-services.net/ns/cm-x#")
OSLC_QM = Namespace("http://open-services.net/ns/qm#")
OSLC_CONFIG = Namespace("http://open-services.net/ns/config#")
RTC_CM = Namespace("http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/")
RTC_EXT = Namespace("http://jazz.net/xmlns/prod/jazz/rtc/ext/1.0/")
CALM = Namespace("http://jazz.net/xmlns/prod/jazz/calm/1.0/")
JFS = Namespace("http://jazz.net/xmlns/prod/jazz/jfs/1.0/")
JP = Namespace("http://jazz.net/xmlns/prod/jazz/process/1.0/")
JP06 = Namespace("http://jazz.net/xmlns/prod/jazz/process/0.6/")
RM_NAV = Namespace("http://jazz.net/ns/rm/navigation#")
JAZZ_RM = Namespace("http://jazz.net/ns/rm#")
RQM = Namespace("http://jazz.net/ns/qm/rqm#")
PROCESS = Namespace("http://jazz.net/ns/process#")
XHTML = "http://www.w3.org/1999/xhtml"

PREFIXES = {
    "rdf": RDF, "rdfs": RDFS, "xsd": XSD, "dcterms": DCTERMS, "foaf": FOAF,
    "oslc": OSLC, "oslc_rm": OSLC_RM, "oslc_cm": OSLC_CM, "oslc_cmx": OSLC_CMX, "oslc_qm": OSLC_QM,
    "oslc_config": OSLC_CONFIG, "rtc_cm": RTC_CM, "rtc_ext": RTC_EXT, "calm": CALM, "jfs": JFS,
    "jp": JP, "nav": RM_NAV, "jazz_rm": JAZZ_RM, "rqm_qm": RQM, "process": PROCESS,
}

APPS = ("rm", "ccm", "qm", "gc")
ROOTSERVICES = "/{app}/rootservices"
# rootservices usa os namespaces OSLC 1.0 (xmlns/...) para apontar os catálogos de domínio
CATALOGS = {
    "rm": Namespace("http://open-services.net/xmlns/rm/1.0/").rmServiceProviders,
    "ccm": Namespace("http://open-services.net/xmlns/cm/1.0/").cmServiceProviders,
    "qm": Namespace("http://open-services.net/xmlns/qm/1.0/").qmServiceProviders,
}
REQUIRED_OCCURS = ("oslc:Exactly-one", "oslc:One-or-many")
DEFAULT_SELECT = "dcterms:identifier,dcterms:title,dcterms:modified,rdf:type"
MAX_LIMIT = 1000

_TAG = re.compile(r"<[^>]+>")
_UUID = re.compile(r"/(_[\w-]+)")


def clark(ns: Namespace | str, local: str = "") -> str:
    """Nome em notação Clark para ElementTree: clark(JP06, 'url') -> '{...process/0.6/}url'."""
    return f"{{{ns}}}{local}"


def qname(uri: str) -> str:
    """Abrevia uma URI usando os prefixos conhecidos (dcterms:title)."""
    for p, ns in PREFIXES.items():
        if uri.startswith(str(ns)):
            return f"{p}:{uri[len(str(ns)):]}"
    return uri


def expand(name: str) -> str:
    """Expande 'dcterms:title' para URI completa. URIs completas passam direto."""
    if "://" in name:
        return name
    prefix, _, local = name.partition(":")
    if prefix not in PREFIXES or not local:
        raise ValueError(f"Prefixo desconhecido em '{name}'. Use um de: {', '.join(PREFIXES)} ou URI completa.")
    return str(PREFIXES[prefix]) + local


def item_id(url: str | None) -> str | None:
    """Último segmento da URL: itemId ('_xxx'), login, id de tipo ou literal de enumeração."""
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] if url else None


def _prefix_header() -> str:
    return ",".join(f"{p}=<{ns}>" for p, ns in PREFIXES.items())


# --- RDF <-> recurso

def parse(content: bytes | str, base: str | None = None) -> Graph:
    g = Graph()
    if content:
        try:
            g.parse(data=content, format="xml", publicID=base)
        except Exception as exc:  # HTML (página de login, URL da UI) ou XML inválido
            raise ValueError(f"Resposta não é RDF/XML{f' de {base}' if base else ''}: {exc}") from exc
    return g


def serialize(g: Graph) -> bytes:
    for p, ns in PREFIXES.items():
        g.bind(p, ns)
    # pretty-xml grava XMLLiteral como rdf:parseType="Literal" (exigido pelo DOORS Next para primaryText)
    return g.serialize(format="pretty-xml", encoding="utf-8")


def text(value) -> str:
    """Literal (inclusive XMLLiteral/XHTML) para texto simples."""
    s = str(value)
    if "<" in s and ">" in s:
        s = re.sub(r"\s+", " ", unescape(_TAG.sub(" ", s))).strip()
    return s


def _link_titles(g: Graph) -> dict[tuple, str]:
    """Títulos de links reificados (rdf:Statement) usados pelo Jazz: (s, p, o) -> título."""
    titles = {}
    for st in g.subjects(RDF.type, RDF.Statement):
        s, p, o = g.value(st, RDF.subject), g.value(st, RDF.predicate), g.value(st, RDF.object)
        t = g.value(st, DCTERMS.title)
        if None not in (s, p, o, t):
            titles[(s, p, o)] = text(t)
    return titles


def _add(bucket: dict, key: str, value) -> None:
    if key in bucket:
        if not isinstance(bucket[key], list):
            bucket[key] = [bucket[key]]
        bucket[key].append(value)
    else:
        bucket[key] = value


def _jsonable(v):
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool, dict)) or v is None:
        return v
    return str(v)  # datas, decimais etc.


def resource(g: Graph, subject: URIRef | BNode, _depth: int = 0) -> dict:
    titles = _link_titles(g)
    out: dict = {
        "url": str(subject) if isinstance(subject, URIRef) else None,
        "id": text(g.value(subject, DCTERMS.identifier) or "") or None,
        "title": text(g.value(subject, DCTERMS.title) or "") or None,
        "types": sorted(qname(str(t)) for t in g.objects(subject, RDF.type)),
        "properties": {},
        "links": {},
    }
    for p, o in g.predicate_objects(subject):
        if p in (RDF.type, DCTERMS.identifier, DCTERMS.title):
            continue
        key = qname(str(p))
        if isinstance(o, Literal):
            is_markup = (o.datatype is not None and "XMLLiteral" in o.datatype) or "<" in str(o)
            _add(out["properties"], key, text(o) if is_markup else o.toPython())
        elif isinstance(o, BNode):
            if _depth < 2:
                _add(out["properties"], key, resource(g, o, _depth + 1))
        else:
            link = {"url": str(o)}
            t = titles.get((subject, p, o)) or g.value(o, DCTERMS.title)
            if t is not None:
                link["title"] = text(t)
            out["links"].setdefault(key, []).append(link)
    out["properties"] = {k: _jsonable(v) for k, v in out["properties"].items()}
    return out


def to_node(value) -> URIRef | Literal:
    """Valor vindo da tool -> nó RDF. URLs viram recursos; o resto vira literal."""
    if isinstance(value, (URIRef, Literal)):
        return value
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return URIRef(value)
    return Literal(value)


def set_properties(g: Graph, subject: URIRef, attributes: dict) -> None:
    """Substitui os valores de cada propriedade (qname ou URI). Lista = vários valores; None remove."""
    for name, value in attributes.items():
        pred = URIRef(expand(name))
        g.remove((subject, pred, None))
        for v in value if isinstance(value, list) else [value]:
            if v is not None:
                g.add((subject, pred, to_node(v)))


def add_properties(g: Graph, subject: URIRef, attributes: dict) -> None:
    """Acrescenta valores sem remover os existentes (usado para links)."""
    for name, value in attributes.items():
        pred = URIRef(expand(name))
        for v in value if isinstance(value, list) else [value]:
            g.add((subject, pred, to_node(v)))


def new_resource(rdf_type: URIRef, properties: dict, attributes: dict | None = None) -> Graph:
    """Grafo de um recurso novo (sujeito <>): rdf:type, `properties` {predicado URIRef: nó | None}
    e `attributes` ({qname: valor}, ver set_properties)."""
    g, s = Graph(), URIRef("")
    g.add((s, RDF.type, rdf_type))
    for pred, node in properties.items():
        if node is not None:
            g.add((s, pred, node))
    set_properties(g, s, attributes or {})
    return g


# --- operações

def literal(value) -> str:
    """Formata um valor para oslc.where: strings entre aspas com escape, URLs entre <>, números crus."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if s.startswith(("http://", "https://")):
        return f"<{s}>"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def where_eq(predicate: str, value) -> str:
    return f"{predicate}={literal(value)}"


def where_in(predicate: str, values) -> str:
    return f"{predicate} in [{','.join(literal(v) for v in values)}]"


def fetch(url: str, configuration: str | None = None) -> tuple[Graph, URIRef, str | None]:
    resp = get_session().request("GET", url, configuration=configuration)
    return parse(resp.content, resp.url), URIRef(url), resp.headers.get("ETag")


def get(url: str, configuration: str | None = None) -> dict:
    g, subject, _ = fetch(url, configuration)
    if (subject, None, None) not in g:  # o servidor pode ter redirecionado para outra URI canônica
        subject = next((s for s in g.subjects() if isinstance(s, URIRef) and s.startswith(url.split("?")[0])),
                       subject)
    return resource(g, subject)


def query(
    query_base: str,
    *,
    where: str | None = None,
    select: str | None = DEFAULT_SELECT,
    search_terms: str | None = None,
    order_by: str | None = None,
    limit: int = 100,
    configuration: str | None = None,
) -> list[dict]:
    """Executa uma query OSLC seguindo a paginação até `limit` resultados (máx. MAX_LIMIT)."""
    limit = max(1, min(limit, MAX_LIMIT))
    params = {"oslc.paging": "true", "oslc.pageSize": str(min(limit, 100)), "oslc.prefix": _prefix_header()}
    for key, value in (("oslc.where", where), ("oslc.select", select), ("oslc.orderBy", order_by)):
        if value:
            params[key] = value
    if search_terms:
        params["oslc.searchTerms"] = literal(search_terms)

    results: list[dict] = []
    url, first = query_base, True
    while url and len(results) < limit:
        resp = get_session().request("GET", url, params=params if first else None, configuration=configuration)
        g = parse(resp.content, resp.url)
        for member in _members(g, query_base):
            results.append(resource(g, member))
            if len(results) >= limit:
                break
        next_page = next(g.objects(None, OSLC.nextPage), None)
        url, first = (str(next_page) if next_page else None), False
    return results


def _members(g: Graph, query_base: str) -> list:
    """Resultados de uma página de query: rdfs:member ou, no ETM (que não usa rdfs:member),
    os recursos tipados do grafo que não são o container nem o oslc:ResponseInfo."""
    members = list(g.objects(None, RDFS.member))
    if members:
        return members
    container = query_base.split("?")[0]
    return [s for s in dict.fromkeys(g.subjects(RDF.type, None))
            if isinstance(s, URIRef) and str(s).split("?")[0] != container
            and (s, RDF.type, OSLC.ResponseInfo) not in g]


def create(factory_url: str, g: Graph, configuration: str | None = None) -> str:
    """POST na creation factory; devolve a URL do recurso criado."""
    resp = get_session().request(
        "POST", factory_url, data=serialize(g), configuration=configuration,
        headers={"Content-Type": RDF_XML}, ok=(200, 201),
    )
    location = resp.headers.get("Location")
    if not location:
        raise RuntimeError(f"Servidor não devolveu Location ao criar em {factory_url}.")
    return location


def update(
    url: str, attributes: dict, *, append: bool = False,
    configuration: str | None = None, params: dict | None = None,
) -> dict:
    """GET + ETag + PUT. `append=True` acrescenta valores (links) sem remover os existentes."""
    g, subject, etag = fetch(url, configuration)
    (add_properties if append else set_properties)(g, subject, attributes)
    headers = {"Content-Type": RDF_XML}
    if etag:
        headers["If-Match"] = etag
    get_session().request("PUT", url, data=serialize(g), headers=headers, params=params, configuration=configuration)
    return get(url, configuration)


# --- descoberta

@lru_cache(maxsize=None)
def _cached(url: str, configuration: str | None = None) -> Graph:
    """fetch com cache: catálogos, providers, shapes e títulos mudam pouco durante a execução."""
    return fetch(url, configuration)[0]


def clear_cache() -> None:
    _cached.cache_clear()


def catalog_url(app: str, kind: str = "oslc") -> str:
    """Catálogo de service providers. kind='oslc' (domínio) ou 'config' (componentes/configurações)."""
    if app not in APPS:
        raise ValueError(f"App inválido '{app}'. Use: {', '.join(APPS)}")
    g = _cached(get_session().url(ROOTSERVICES.format(app=app)))
    if kind == "config":
        url = next((o for p, o in g.predicate_objects()
                    if str(p).endswith("cmServiceProviders") and "config" in str(p)), None)
    else:
        url = next(g.objects(None, CATALOGS[app]), None) if app in CATALOGS else None
    if url is None:
        raise RuntimeError(f"rootservices de /{app} não publica o catálogo '{kind}' "
                           "(gerenciamento de configuração habilitado?).")
    return str(url)


def providers(app: str, kind: str = "oslc") -> list[dict]:
    """Service providers (um por project area): [{title, url, project_area_uuid}]."""
    g = _cached(catalog_url(app, kind))
    out = []
    for sp in g.objects(None, OSLC.serviceProvider):
        details = g.value(sp, OSLC.details)
        found = (_UUID.search(str(details)) if details else None) or _UUID.search(str(sp))
        out.append({"title": text(g.value(sp, DCTERMS.title) or ""), "url": str(sp),
                    "project_area_uuid": found.group(1) if found else None})
    return sorted(out, key=lambda x: x["title"].lower())


def provider(app: str, project_area_uuid: str, kind: str = "oslc") -> str:
    """URL do service provider da project area (pelo UUID)."""
    found = next((p["url"] for p in providers(app, kind) if p["project_area_uuid"] == project_area_uuid), None)
    if found is None:
        raise LookupError(f"Project area {project_area_uuid} não está no catálogo '{kind}' de /{app}.")
    return found


def services(provider_url: str, configuration: str | None = None) -> dict:
    """Capacidades OSLC do provider: query_capabilities, creation_factories e components (URLs dos
    componentes de configuração citados nas capacidades, ex.: DOORS Next)."""
    g = _cached(provider_url, configuration)

    def cap(node, url_pred) -> dict:
        return {
            "title": text(g.value(node, DCTERMS.title) or ""),
            "url": str(g.value(node, url_pred)),
            "resource_types": [str(t) for t in g.objects(node, OSLC.resourceType)],
            "resource_shapes": [str(s) for s in g.objects(node, OSLC.resourceShape)],
        }

    return {
        "query_capabilities": [cap(q, OSLC.queryBase) for q in g.objects(None, OSLC.queryCapability)],
        "creation_factories": [{**cap(f, OSLC.creation), "creation": str(g.value(f, OSLC.creation))}
                               for f in g.objects(None, OSLC.creationFactory)],
        "components": sorted({str(c) for c in g.objects(None, OSLC_CONFIG.component)}),
    }


def query_base(provider_url: str, resource_type, configuration: str | None = None) -> str:
    for q in services(provider_url, configuration)["query_capabilities"]:
        if str(resource_type) in q["resource_types"]:
            return q["url"]
    raise LookupError(f"{provider_url} não tem query capability para {qname(str(resource_type))}.")


def creation_factories(provider_url: str, resource_type, configuration: str | None = None) -> list[dict]:
    found = [f for f in services(provider_url, configuration)["creation_factories"]
             if str(resource_type) in f["resource_types"]]
    if not found:
        raise LookupError(f"{provider_url} não tem creation factory para {qname(str(resource_type))}.")
    return found


def resource_shapes(provider_url: str, resource_type) -> list[str]:
    """Shapes publicados para o tipo em query capabilities e creation factories."""
    svc = services(provider_url)
    return sorted({s for c in svc["query_capabilities"] + svc["creation_factories"]
                   if str(resource_type) in c["resource_types"] for s in c["resource_shapes"]})


def shape(url: str, configuration: str | None = None) -> dict:
    """Resource shape: título e propriedades (nome, predicado, obrigatoriedade, valores permitidos)."""
    g = _cached(url, configuration)
    subject = URIRef(url)
    if (subject, RDF.type, OSLC.ResourceShape) not in g:
        subject = next(g.subjects(RDF.type, OSLC.ResourceShape), subject)
    props = []
    for p in g.objects(subject, OSLC.property):
        allowed_node = g.value(p, OSLC.allowedValues)
        allowed = [str(v) for v in g.objects(p, OSLC.allowedValue)]
        if allowed_node is not None:
            allowed += [str(v) for v in g.objects(allowed_node, OSLC.allowedValue)]
        occurs = qname(str(g.value(p, OSLC.occurs) or ""))
        props.append({
            "name": text(g.value(p, OSLC.name) or ""),
            "title": text(g.value(p, DCTERMS.title) or ""),
            "predicate": qname(str(g.value(p, OSLC.propertyDefinition) or "")),
            "occurs": occurs,
            "required": occurs in REQUIRED_OCCURS,
            "value_type": qname(str(g.value(p, OSLC.valueType) or "")),
            "range": [str(r) for r in g.objects(p, OSLC.range)],
            "read_only": str(g.value(p, OSLC.readOnly) or "").lower() == "true",
            "allowed_values": allowed,
            # EWM publica os valores permitidos num documento separado: use allowed_values(url)
            "allowed_values_url": str(allowed_node) if isinstance(allowed_node, URIRef) and not allowed else None,
            "default": str(g.value(p, OSLC.defaultValue) or "").strip() or None,
        })
    return {
        "url": url,
        "title": text(g.value(subject, DCTERMS.title) or ""),
        "describes": [str(d) for d in g.objects(subject, OSLC.describes)],
        "properties": sorted(props, key=lambda x: x["title"].lower() or x["name"]),
    }


def allowed_values(url: str) -> list[dict]:
    """Valores de um documento oslc:AllowedValues: [{url, title}]."""
    g = _cached(url)
    return [{"url": str(v), "title": text(t) if (t := g.value(v, DCTERMS.title)) is not None else title(str(v))}
            for v in g.objects(None, OSLC.allowedValue)]


def title(url: str) -> str | None:
    """dcterms:title de um recurso (GET com cache); None se não houver representação RDF."""
    try:
        t = _cached(url).value(URIRef(url), DCTERMS.title)
    except Exception:  # valor sem representação RDF: devolve só a URL
        return None
    return text(t) if t is not None else None


def configurations(component_url: str) -> list[dict]:
    """Streams, baselines e change sets de um componente (rm/qm): [{url, title, types}]."""
    g, subject, _ = fetch(component_url)
    container = g.value(subject, OSLC_CONFIG.configurations)
    if container is None:
        raise LookupError(f"{component_url} não expõe oslc_config:configurations.")
    members = fetch(str(container))[0]
    out = []
    # ponytail: 1 GET por configuração para obter título/tipo; trocar por query oslc_config se ficar lento
    for member in members.objects(None, RDFS.member):
        mg = fetch(str(member))[0]
        out.append({"url": str(member), "title": text(mg.value(member, DCTERMS.title) or ""),
                    "types": sorted(qname(str(t)) for t in mg.objects(member, RDF.type))})
    return out
