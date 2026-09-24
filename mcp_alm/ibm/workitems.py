"""Work Items: criação, leitura, schema, categorias, releases, busca e comentários do EWM."""
from __future__ import annotations

import json
from urllib.parse import unquote

from rdflib import Literal, URIRef
from rdflib.namespace import DCTERMS, RDF, RDFS

from ..infra import oslc
from ..infra.http import get_session, get_xml, reportable
from ..server import tool
from . import common

WORKITEM = "/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{id}"
REPORTABLE_WORKITEM = "/ccm/rpt/repository/workitem"
REPORTABLE_FIELDS = "workitem/{kind}[projectArea/itemId={pa}]/(itemId|name|archived)"
REPORTABLE_CATEGORY_FIELDS = "workitem/category[projectArea/itemId={pa}]/(itemId|name|archived|defaultTeamArea/itemId|defaultTeamArea/name)"
MAX_PAGE = 500
SCHEMA_SECTIONS = ("attributes", "enumerations", "workflows", "linkTypes", "approvals", "createMetadata")

# chaves de atributo da doc IBM (JSON do EWM) -> predicado OSLC
ATTRIBUTE_PREDICATES = {
    "id": "dcterms:identifier", "summary": "dcterms:title", "description": "dcterms:description",
    "workItemType": "dcterms:type", "owner": "dcterms:contributor", "creator": "dcterms:creator",
    "created": "dcterms:created", "modified": "dcterms:modified", "tags": "dcterms:subject",
    "internalState": "rtc_cm:state", "internalPriority": "oslc_cmx:priority",
    "internalSeverity": "oslc_cmx:severity", "category": "rtc_cm:filedAgainst", "target": "rtc_cm:plannedFor",
    "foundIn": "rtc_cm:foundIn", "teamArea": "rtc_cm:teamArea", "projectArea": "rtc_cm:projectArea",
}
MINIMAL = "workItemType,id,summary,description,owner,internalState,internalPriority,internalSeverity,modified"
ITEM_URLS = {
    "rtc_cm:filedAgainst": "/ccm/resource/itemOid/com.ibm.team.workitem.Category/{id}",
    "rtc_cm:plannedFor": "/ccm/oslc/iterations/{id}",
    "rtc_cm:teamArea": "/ccm/oslc/teamareas/{id}",
    "rtc_cm:foundIn": "/ccm/resource/itemOid/com.ibm.team.workitem.Deliverable/{id}",
}
ENUMERATION_LITERAL = "/ccm/oslc/enumerations/{pa}/{enumeration}/{literal}"
USER_PREDICATES = {"dcterms:contributor", "dcterms:creator", "rtc_cm:resolvedBy"}
TEXT_PREDICATES = {"dcterms:title", "dcterms:description"}
WITH_TITLES = "?oslc.properties=*%7Bdcterms:title%7D"
COMMENT_FACTORY = "{comments}/oslc:comment"
# operador da doc IBM -> operador oslc.where (contains vira oslc.searchTerms)
WHERE_OPERATORS = {"is": "=", "equals": "=", "is not": "!=", "before": "<", "after": ">"}
DATE_OPERATORS = {"before", "after"}
FILTER_KEYS = {"operator", "attributeExpressions", "termExpressions", "similarityExpressions"}

_DC = oslc.clark(DCTERMS)
_RTC_CM = oslc.clark(oslc.RTC_CM)
_RDF_RESOURCE = oslc.clark(RDF, "resource")


def predicate(key: str, names: dict[str, str] | None = None) -> str:
    """Chave da doc IBM, oslc:name do shape ou qname -> predicado em qname. Desconhecida = atributo custom."""
    key = key.strip()
    return ATTRIBUTE_PREDICATES.get(key) or (names or {}).get(key) or (key if ":" in key else f"rtc_ext:{key}")


def workitem_url(id_or_url: str) -> str:
    if id_or_url.startswith(("http://", "https://")):
        return id_or_url
    if not id_or_url.isdigit():
        raise ValueError(f"Id de work item inválido: {id_or_url}")
    return get_session().url(WORKITEM.format(id=id_or_url))


def types(project_area_id: str) -> list[dict]:
    """Tipos de work item da project area: [{id, title, type_url, creation, shape}]."""
    svc = oslc.services(oslc.provider("ccm", project_area_id))
    cr = str(oslc.OSLC_CM.ChangeRequest)
    return sorted(({"id": oslc.item_id(t), "title": f["title"], "type_url": t, "creation": f["creation"],
                    "shape": f["resource_shapes"][0] if f["resource_shapes"] else None}
                   for f in svc["creation_factories"] if cr in f["resource_types"]
                   for t in f["resource_types"] if t != cr), key=lambda t: t["id"])


def _comments_url(resource: dict) -> str | None:
    return next((link["url"] for pred, links in resource["links"].items()
                 if pred == "oslc:discussedBy" or pred.endswith("comments") for link in links), None)


def _user_titles(resources: list[dict]) -> list[dict]:
    """Preenche o `title` dos links para usuários: eles ficam no JTS e o EWM não embute o nome."""
    users = [link for r in resources for links in r["links"].values() for link in links
             if "/jts/users/" in link["url"] and "title" not in link and not link["url"].endswith("/unassigned")]
    if users:
        names = {c["userId"]: c["name"] for c in common.contributors()}
        for link in users:
            if name := names.get(unquote(link["url"].rsplit("/", 1)[-1])):
                link["title"] = name
    return resources


@tool
def get_workitem(
    workitem_id: str | None = None, workitem_oslc_url: str | None = None, project_area_id: str | None = None,
    fetch_all: bool = False, gc_uri: str | None = None,
) -> dict:
    """Work item pelo número ou URL OSLC: {url, id, title, types, properties, links}; cada link traz `title`
    (nome do estado, prioridade, usuário...) quando o servidor o expõe. Sem `fetch_all`, só os
    atributos mínimos (tipo, id, resumo, descrição, owner, estado, prioridade, severidade, modificação);
    com `fetch_all`, todos os atributos e links + `comments`. `project_area_id` é aceito por compatibilidade."""
    if bool(workitem_id) == bool(workitem_oslc_url):
        raise ValueError("Informe workitem_id ou workitem_oslc_url (um dos dois).")
    url = workitem_url(workitem_id or workitem_oslc_url).split("?")[0]
    # o EWM embute o dcterms:title de cada recurso ligado (estado, prioridade, iteração...) na mesma resposta
    resource = {**oslc.get(url + WITH_TITLES, gc_uri), "url": url}
    _user_titles([resource])
    if not fetch_all:
        keep = {predicate(k) for k in MINIMAL.split(",")}
        return {**resource, "properties": {k: v for k, v in resource["properties"].items() if k in keep},
                "links": {k: v for k, v in resource["links"].items() if k in keep}}
    comments = []
    if url := _comments_url(resource):
        g = oslc.fetch(url)[0]
        nodes = set(g.objects(None, oslc.OSLC.comment)) | set(g.objects(None, RDFS.member))
        comments = [oslc.resource(g, c) for c in nodes]
    return {**resource, "comments": comments}


# --- schema

def enumeration(prop: dict) -> list[dict] | None:
    """Literais permitidos de uma propriedade de enumeração: [{id, url, title}]; None se não for enumeração."""
    enumeration = next((r for r in prop["range"] if "/oslc/enumerations/" in r), None)
    if enumeration is None:
        return None
    g = oslc.fetch(enumeration)[0]
    allowed = {oslc.item_id(u) for u in prop["allowed_values"]}
    values = [{"id": oslc.item_id(str(s)), "url": str(s), "title": oslc.text(t)}
              for s, t in g.subject_objects(DCTERMS.title) if str(s) != enumeration]
    return sorted((v for v in values if not allowed or v["id"] in allowed), key=lambda v: v["id"])


def workflow(states_url: str) -> dict:
    """Estados e ações de um workflow (.../workflows/{pa}/states/{workflow})."""
    titles = {str(s): oslc.text(t) for s, t in oslc.fetch(states_url)[0].subject_objects(DCTERMS.title)
              if str(s) != states_url}
    # título e estado resultante das ações só vêm no formato OSLC CM 1.0
    actions = []
    for a in get_xml(states_url.replace("/states/", "/actions/"), oslc_v1=True).findall(_RTC_CM + "Action"):
        result = a.find(_RTC_CM + "resultState")
        actions.append({"id": a.findtext(_DC + "identifier"), "title": a.findtext(_DC + "title"),
                        "resultState": titles.get(result.get(_RDF_RESOURCE)) if result is not None else None})
    return {"states": [{"id": oslc.item_id(u), "url": u, "title": t} for u, t in sorted(titles.items())],
            "actions": sorted(actions, key=lambda a: a["title"] or "")}


def _workflow(props: list[dict]) -> dict | None:
    """Workflow do tipo, a partir dos valores permitidos de rtc_cm:state."""
    state = next((p for p in props if p["predicate"] == "rtc_cm:state"), None)
    if state is None:
        return None
    urls = state["allowed_values"] or (
        [v["url"] for v in oslc.allowed_values(state["allowed_values_url"])] if state["allowed_values_url"] else [])
    return workflow(urls[0].rsplit("/", 1)[0]) if urls else None


@tool
def get_workitem_schema(
    project_area_item_id: str, workitem_type: str | None = None, include: list[str] | None = None,
) -> dict:
    """Schema dos tipos de work item: {projectAreaId, workItemTypes: [{id, title, attributes?, enumerations?,
    workflows?, linkTypes?, createMetadata?}]}. `include` (padrão ["attributes", "enumerations"]):
    attributes, enumerations, workflows, linkTypes, createMetadata (exige workitem_type). approvals não é
    suportado. Categorias e releases: list_workitem_categories / list_workitem_releases."""
    include = include or ["attributes", "enumerations"]
    unknown = set(include) - set(SCHEMA_SECTIONS)
    if unknown:
        raise ValueError(f"include inválido: {sorted(unknown)}. Use: {', '.join(SCHEMA_SECTIONS)}.")
    if "approvals" in include:
        raise ValueError("include=approvals não é suportado (sem API OSLC pública).")
    if "createMetadata" in include and not workitem_type:
        raise ValueError("include=createMetadata exige workitem_type.")
    found = types(project_area_item_id)
    if workitem_type:
        found = [t for t in found if t["id"] == workitem_type]
        if not found:
            raise LookupError(f"Tipo '{workitem_type}' não existe em {project_area_item_id}.")
    out = []
    for t in found:
        props = oslc.shape(t["shape"])["properties"] if t["shape"] else []
        entry = {"id": t["id"], "title": t["title"]}
        if "attributes" in include:
            entry["attributes"] = [{"id": p["name"], "name": p["title"], "predicate": p["predicate"],
                                    "valueType": p["value_type"], "required": p["required"],
                                    "readOnly": p["read_only"]} for p in props]
        if "enumerations" in include:
            entry["enumerations"] = {p["name"]: values for p in props if (values := enumeration(p)) is not None}
        if "workflows" in include:
            entry["workflows"] = _workflow(props)
        if "linkTypes" in include:
            entry["linkTypes"] = [{"id": p["predicate"].rsplit(".", 1)[-1], "name": p["title"],
                                   "predicate": p["predicate"]} for p in props if ".linktype." in p["predicate"]]
        if "createMetadata" in include:
            entry["createMetadata"] = {"requiredProperties": [p["name"] for p in props
                                                              if p["required"] and not p["read_only"]]}
        out.append(entry)
    return {"projectAreaId": project_area_item_id, "workItemTypes": out}


# --- categorias e releases (Reportable REST: traz arquivados, que os shapes omitem)

def _reportable_items(kind: str, project_area_item_id: str, include_archived: bool, limit: int,
                      offset: int) -> list[dict]:
    if not 1 <= limit <= MAX_PAGE:
        raise ValueError(f"limit deve estar entre 1 e {MAX_PAGE}.")
    records = reportable(REPORTABLE_WORKITEM, REPORTABLE_FIELDS.format(kind=kind, pa=project_area_item_id), kind)
    items = [{"itemId": r.findtext("itemId"), "name": r.findtext("name"), "archived": r.findtext("archived") == "true"}
             for r in records]
    items = [i for i in items if include_archived or not i["archived"]]
    return items[offset:offset + limit]


@tool
def list_workitem_categories(
    project_area_item_id: str, include_archived: bool = False, limit: int = 100, offset: int = 0,
) -> list[dict]:
    """Categorias (Filed Against) da project area: [{itemId, name, archived, defaultTeamArea?}]. Use itemId em
    `category` de create_workitem. `defaultTeamArea` (quando presente) indica o Team Area vinculado à categoria:
    {itemId, name}. O Team Area do work item é derivado automaticamente da categoria — não pode ser definido
    diretamente via OSLC. limit máx. 500."""
    if not 1 <= limit <= MAX_PAGE:
        raise ValueError(f"limit deve estar entre 1 e {MAX_PAGE}.")
    records = reportable(REPORTABLE_WORKITEM, REPORTABLE_CATEGORY_FIELDS.format(pa=project_area_item_id), "category")
    items = []
    for r in records:
        item = {"itemId": r.findtext("itemId"), "name": r.findtext("name"), "archived": r.findtext("archived") == "true"}
        team_area = r.find("defaultTeamArea")
        if team_area is not None and team_area.findtext("itemId"):
            item["defaultTeamArea"] = {"itemId": team_area.findtext("itemId"), "name": team_area.findtext("name")}
        items.append(item)
    items = [i for i in items if include_archived or not i["archived"]]
    return items[offset:offset + limit]


@tool
def list_workitem_releases(
    project_area_item_id: str, include_archived: bool = False, limit: int = 100, offset: int = 0,
) -> list[dict]:
    """Releases (Found In) da project area: [{itemId, name, archived}]. Use itemId em `foundIn`
    de create_workitem. limit máx. 500."""
    return _reportable_items("deliverable", project_area_item_id, include_archived, limit, offset)


# --- busca, criação e comentários

def node(project_area_id: str, pred: str, value) -> URIRef | Literal:
    """Valor da doc IBM (UUID, literal de enumeração, login, texto) -> nó RDF."""
    if not isinstance(value, str):
        return Literal(value)
    url = get_session().url
    if value.startswith(("http://", "https://")):
        return URIRef(value)
    if pred in USER_PREDICATES:
        return URIRef(common.user_url(value))
    if pred in ITEM_URLS and value.startswith("_"):
        return URIRef(url(ITEM_URLS[pred].format(id=value)))
    if ".literal." in value:
        return URIRef(url(ENUMERATION_LITERAL.format(pa=project_area_id, enumeration=value.split(".literal.")[0],
                                                     literal=value)))
    return Literal(value)  # inclusive ids de estado e de tipo: o oslc.where do EWM os compara como texto


def _json(text: str | None, name: str, kind: type, default):
    """JSON de parâmetro -> valor do tipo esperado (dict ou list); ValueError com o motivo."""
    try:
        value = json.loads(text) if text else default
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} não é JSON válido: {exc}") from exc
    if not isinstance(value, kind):
        raise ValueError(f"{name} deve ser {'um objeto' if kind is dict else 'uma lista'} JSON.")
    return value


def _clause(project_area_id: str, expression: dict) -> tuple[str | None, list[str]]:
    """Expressão de atributo da doc IBM -> (cláusula oslc.where, termos de busca)."""
    pred, operator = predicate(expression["attributeId"]), expression["operator"]
    values = expression.get("values")
    values = values if isinstance(values, list) else [] if values is None else [values]
    if not values:
        raise ValueError(f"Expressão de {expression['attributeId']} sem valor.")
    if operator == "contains":
        if pred not in TEXT_PREDICATES:
            raise ValueError(f"Operador contains só é suportado em summary/description, não em "
                             f"{expression['attributeId']}.")
        return None, [str(v) for v in values]
    nodes = [oslc.literal(str(n) if isinstance(n := node(project_area_id, pred, v), URIRef) else n.toPython())
             for v in values]
    if operator == "in" or (operator in ("is", "equals") and len(nodes) > 1):
        return f"{pred} in [{','.join(nodes)}]", []
    if operator not in WHERE_OPERATORS or len(nodes) != 1:
        raise ValueError(f"Operador '{operator}' com {len(nodes)} valor(es) não é suportado. "
                         f"Use: {', '.join(WHERE_OPERATORS)}, in, contains.")
    if operator in DATE_OPERATORS:
        return f'{pred}{WHERE_OPERATORS[operator]}"{values[0]}"^^xsd:dateTime', []
    return f"{pred}{WHERE_OPERATORS[operator]}{nodes[0]}", []


@tool
def search_workitems(
    project_area_item_id: str, filter: str, attributes: str | None = None, gc_uri: str | None = None,
) -> list[dict]:
    """Busca work items (até 1000). `filter`: JSON da doc IBM ({"operator": "AND", "attributeExpressions":
    [{"attributeId", "operator", "values"}]}), traduzido para oslc.where. Suportado: AND (OR só com uma
    expressão), operadores is/equals, is not, in, before/after (datas ISO), contains (só summary/description).
    Valores: UUIDs (category, target, teamArea, foundIn, owner), literais ('priority.literal.l1'), ids de
    estado ('...workflow.state.s1') ou URLs. `attributes`: ids separados por vírgula (padrão: conjunto mínimo).
    Cada link traz `title` (nome do estado, prioridade, usuário...) quando o servidor o expõe."""
    query = _json(filter, "filter", dict, None)
    if unknown := sorted(set(query) - FILTER_KEYS):
        raise ValueError(f"Chaves não suportadas em filter: {', '.join(unknown)}.")
    if query.get("termExpressions") or query.get("similarityExpressions"):
        raise ValueError("termExpressions e similarityExpressions não são suportados.")
    operator = str(query.get("operator", "AND")).upper()
    if operator not in ("AND", "OR"):
        raise ValueError(f"operator '{query['operator']}' inválido; use AND ou OR.")
    expressions = query.get("attributeExpressions") or []
    if not expressions:
        raise ValueError("filter precisa de ao menos uma expressão em attributeExpressions.")
    if not isinstance(expressions, list) or not all(isinstance(e, dict) for e in expressions):
        raise ValueError("attributeExpressions deve ser uma lista de objetos JSON.")
    if len(expressions) > 1 and operator != "AND":
        raise ValueError("Operador OR entre expressões não é suportado pelo oslc.where; use AND ou 'in'.")
    clauses, terms = [], []
    for expression in expressions:
        clause, found = _clause(project_area_item_id, expression)
        clauses += [clause] if clause else []
        terms += found
    # {dcterms:title}: o EWM embute o nome de cada recurso ligado (estado, prioridade...); em literais é ignorado
    select = ",".join(f"{predicate(a)}{{dcterms:title}}" for a in (attributes or MINIMAL).split(","))
    base = oslc.query_base(oslc.provider("ccm", project_area_item_id), oslc.OSLC_CM.ChangeRequest)
    return _user_titles(oslc.query(base, where=" and ".join(clauses) or None, search_terms=" ".join(terms) or None,
                                   select=select, limit=oslc.MAX_LIMIT, configuration=gc_uri))


@tool
def create_workitem(
    project_area_id: str, work_item_type: str, attributes: str | None = None, links: str | None = None,
) -> dict:
    """Cria um work item. `attributes`: JSON {chave: valor} com chaves da doc IBM (summary, description,
    category, owner, internalPriority, internalSeverity, foundIn, target...) ou oslc:name do shape (custom).
    Valores: UUIDs (category de list_workitem_categories, foundIn de list_workitem_releases, owner de get_user),
    literais de enumeração ou URLs. `links`: JSON [{endpointId, targetWorkItemId}] com endpointId parent,
    children, related, blocks, dependsOn, predecessor, successor, duplicateOf, duplicates, resolves, resolvedBy.
    Campos obrigatórios: get_workitem_schema(include=["createMetadata"]); o servidor recusa se faltar algum.
    Retorna o work item criado."""
    values = _json(attributes, "attributes", dict, {})
    requested_links = _json(links, "links", list, [])
    if not all(isinstance(link, dict) for link in requested_links):
        raise ValueError("links deve ser uma lista de objetos JSON {endpointId, targetWorkItemId}.")
    found = next((t for t in types(project_area_id) if t["id"] == work_item_type), None)
    if found is None:
        raise LookupError(f"Tipo '{work_item_type}' não existe em {project_area_id}.")
    props = oslc.shape(found["shape"])["properties"] if found["shape"] else []
    names = {p["name"]: p["predicate"] for p in props}
    link_predicates = {p["predicate"].rsplit(".", 1)[-1]: p["predicate"] for p in props
                       if ".linktype." in p["predicate"]}

    graph = oslc.new_resource(oslc.OSLC_CM.ChangeRequest, {DCTERMS.type: URIRef(found["type_url"])})
    subject = URIRef("")
    for key, value in values.items():
        pred = predicate(key, names)
        for v in value if isinstance(value, list) else [value]:
            graph.add((subject, URIRef(oslc.expand(pred)), node(project_area_id, pred, v)))
    for link in requested_links:
        pred = link_predicates.get(link.get("endpointId"))
        if pred is None:
            raise ValueError(f"endpointId '{link.get('endpointId')}' não existe no tipo {work_item_type}. "
                             f"Válidos: {', '.join(sorted(link_predicates)) or 'nenhum'}.")
        graph.add((subject, URIRef(oslc.expand(pred)), URIRef(workitem_url(str(link["targetWorkItemId"])))))
    return oslc.get(oslc.create(found["creation"], graph))


@tool
def add_comment_to_workitem(workitem_id: str, comment: str, mentions: list[str] | None = None) -> dict:
    """Adiciona um comentário ao work item. `mentions`: logins citados como @login no início do texto.
    Retorna o comentário criado."""
    resource = oslc.get(workitem_url(workitem_id))
    comments = _comments_url(resource)
    if comments is None:
        raise LookupError(f"Work item {workitem_id} não expõe coleção de comentários.")
    text = " ".join([*(f"@{m}" for m in mentions or []), comment])
    location = oslc.create(COMMENT_FACTORY.format(comments=comments),
                           oslc.new_resource(oslc.OSLC.Comment, {DCTERMS.description: Literal(text)}))
    return oslc.get(location)
