"""Tools do DOORS Next para as skills (alm-setup e alm-rm): saída com as chaves do alm.json."""
from __future__ import annotations

from .ibm import common, requirements
from .infra import oslc
from .infra.http import get_session
from .server import tool

PROJECT_AREA = "/rm/process/project-areas/{pa}"
COMPONENT = "/rm/cm/component/{id}"
# ponytail: configuration é sempre id de stream (o que rm_get_configuration devolve); aceitar baseline se precisar
STREAM = "/rm/cm/stream/{id}"
FOLDER = "/rm/folders/{id}"
TYPE = "/rm/types/{id}"
SEARCH_SELECT = "dcterms:identifier,dcterms:title,oslc:instanceShape,nav:parent"
# propriedades que já têm lugar próprio na saída (não entram em attributes)
CORE = {"dcterms:title", "dcterms:identifier", "dcterms:description", "jazz_rm:primaryText", "nav:parent",
        "oslc:instanceShape"}


def _area_url(project_area_identifier: str) -> str:
    return get_session().url(PROJECT_AREA.format(pa=project_area_identifier))


def _url(template: str, identifier: str) -> str:
    return get_session().url(template.format(id=identifier))


@tool
def rm_get_configuration(project_area_identifier: str) -> dict:
    """Componente e stream padrão da project area do DOORS Next: {component, configuration} (identifiers do
    primeiro componente e da primeira stream). alm.json: rm.component e rm.configuration."""
    components = requirements.get_project_components(project_area_identifier)
    if not components:
        raise LookupError(f"Project area {project_area_identifier} não tem componentes no DOORS Next.")
    component = components[0]["url"]
    return {"component": oslc.item_id(component), "configuration": oslc.item_id(requirements.default_stream(component))}


@tool
def rm_list_members(project_area_identifier: str) -> list[dict]:
    """Membros da project area do DOORS Next: [{identifier (login), name}]. alm.json: rm.members {identifier: name}."""
    names = {c["userId"]: c["name"] for c in common.contributors()}
    members = [{"identifier": login, "name": names.get(login)}
               for login in dict.fromkeys(common.area_members(_area_url(project_area_identifier)))]
    return sorted(members, key=lambda m: (m["name"] or m["identifier"]).lower())


@tool
def rm_list_folders(project_area_identifier: str, component: str, configuration: str) -> list[dict]:
    """Pastas do componente com o caminho como nome: [{name ('01-Requisitos'), identifier ('FR_...')}].
    alm.json: rm.folders {name: identifier}. O prefixo 'root/' é removido do nome."""
    folders = requirements.list_rm_component_folders(_url(COMPONENT, component), _url(STREAM, configuration))
    by_url = {f["url"]: f for f in folders}

    def path(folder: dict) -> str:
        parent = by_url.get(folder["parent"])
        return f"{path(parent)}/{folder['title']}" if parent else folder["title"]

    result = []
    for f in folders:
        name = path(f)
        # Remove o prefixo 'root/' ou 'root' se for a pasta raiz
        if name == "root":
            continue  # Não inclui a pasta raiz
        if name.startswith("root/"):
            name = name[5:]  # Remove 'root/'
        result.append({"name": name, "identifier": oslc.item_id(f["url"])})
    return result


@tool
def rm_list_requirement_types(project_area_identifier: str, component: str, configuration: str) -> list[dict]:
    """Tipos de requisito criáveis: [{name, identifier ('OT_...')}]. alm.json: rm.requirements-types
    {name: identifier}."""
    return [{"name": s["title"], "identifier": oslc.item_id(s["url"])}
            for s in requirements.get_rm_component_types(project_area_identifier, component,
                                                         _url(STREAM, configuration))]


# --- alm-rm

def _link(resource: dict, predicate: str) -> dict | None:
    return next(iter(resource["links"].get(predicate, [])), None)


def _summary(resource: dict, configuration: str) -> dict:
    """Requisito enxuto: {id, title, type, folder, url}."""
    shape, folder = _link(resource, "oslc:instanceShape"), _link(resource, "nav:parent")
    return {"id": resource["id"], "title": resource["title"],
            "type": oslc.shape(shape["url"], configuration)["title"] if shape else None,
            "folder": (folder.get("title") or oslc.title(folder["url"])) if folder else None,
            "url": resource["url"]}


def _attributes(shape_url: str, configuration: str, attributes: dict) -> dict:
    """{nome do atributo: valor} -> {predicado: valor}; valor de enumeração pelo nome -> URL permitida."""
    props = {p["title"]: p for p in oslc.shape(shape_url, configuration)["properties"]}
    out = {}
    for name, value in attributes.items():
        prop = props.get(name)
        if prop is None:
            raise ValueError(f"Atributo '{name}' não existe no tipo. Válidos: {', '.join(sorted(props))}.")
        if prop["allowed_values"]:
            allowed = {oslc.title(u): u for u in prop["allowed_values"]}
            if value not in allowed:
                raise ValueError(f"Valor '{value}' inválido para '{name}'. Válidos: "
                                 f"{', '.join(sorted(str(t) for t in allowed))}.")
            value = allowed[value]
        out[prop["predicate"]] = value
    return out


@tool
def rm_search_requirements(
    project_area_identifier: str, component: str, configuration: str, text: str | None = None,
    folder: str | None = None, requirement_type: str | None = None,
) -> list[dict]:
    """Requisitos (até 1000): [{id, title, type, folder, url}]. Filtros combinam com 'e': texto no título/corpo,
    pasta (identifier de rm.folders) e tipo (identifier de rm.requirements-types). Exige ao menos um filtro."""
    if not (text or folder or requirement_type):
        raise ValueError("Informe ao menos um filtro: text, folder ou requirement_type.")
    configuration = _url(STREAM, configuration)
    clauses = [oslc.where_eq("nav:parent", _url(FOLDER, folder))] if folder else []
    if requirement_type:
        clauses.append(oslc.where_eq("oslc:instanceShape", _url(TYPE, requirement_type)))
    found = oslc.query(requirements.requirements_base(project_area_identifier, configuration),
                       where=" and ".join(clauses) or None, search_terms=text, select=SEARCH_SELECT,
                       limit=oslc.MAX_LIMIT, configuration=configuration)
    return [_summary(r, configuration) for r in found]


@tool
def rm_get_requirement(project_area_identifier: str, component: str, configuration: str, requirement_id: str) -> dict:
    """Requisito pelo id numérico: {id, title, type, folder, text, attributes {nome: valor}, links {qname: [...]},
    url}. Atributos pelo nome do tipo; valores de enumeração pelo nome."""
    configuration = _url(STREAM, configuration)
    resource = requirements.get_requirement(project_area_identifier, component, requirement_id,
                                            configuration_url=configuration)
    shape = _link(resource, "oslc:instanceShape")
    props = oslc.shape(shape["url"], configuration)["properties"] if shape else []
    names = {p["predicate"]: p["title"] for p in props if p["title"]}  # sem título não dá para nomear
    attributes = {names[k]: v for k, v in resource["properties"].items() if k in names and k not in CORE}
    attributes |= {names[k]: [link.get("title") or oslc.title(link["url"]) or link["url"] for link in v]
                   for k, v in resource["links"].items() if k in names and k not in CORE}
    links = {k: v for k, v in resource["links"].items() if k not in names and k not in CORE and k != "rdf:type"}
    return {**_summary(resource, configuration), "text": resource["properties"].get("jazz_rm:primaryText"),
            "attributes": attributes, "links": links}


@tool
def rm_create_requirement(
    project_area_identifier: str, component: str, configuration: str, requirement_type: str, folder: str,
    title: str, text: str, attributes: dict | None = None,
) -> dict:
    """Cria um requisito na pasta e no tipo do alm.json (identifiers). `text`: texto ou XHTML. `attributes`:
    {nome: valor} (valor de enumeração pelo nome). Retorna {id, title, url}."""
    configuration, requirement_type = _url(STREAM, configuration), _url(TYPE, requirement_type)
    values = _attributes(requirement_type, configuration, attributes or {})  # valida antes do POST
    created = requirements.create_requirement(_area_url(project_area_identifier), _url(COMPONENT, component),
                                              requirement_type, title, "", text, configuration_url=configuration,
                                              folder_url=_url(FOLDER, folder))
    if values:  # ponytail: 2ª gravação para os atributos; montar tudo no POST se virar gargalo
        created = oslc.update(created["url"], values, configuration=configuration)
    return {"id": created["id"], "title": created["title"], "url": created["url"]}


@tool
def rm_update_requirement(
    project_area_identifier: str, component: str, configuration: str, requirement_id: str,
    title: str | None = None, text: str | None = None, attributes: dict | None = None,
) -> dict:
    """Atualiza título, texto e/ou atributos ({nome: valor}) do requisito pelo id numérico. Retorna {id, title, url}."""
    if not (title or text is not None or attributes):
        raise ValueError("Informe title, text e/ou attributes.")
    configuration = _url(STREAM, configuration)
    resource = requirements.get_requirement(project_area_identifier, component, requirement_id,
                                            configuration_url=configuration)
    changes: dict = {}
    if title:
        changes["dcterms:title"] = title
    if text is not None:
        changes["jazz_rm:primaryText"] = requirements.xhtml(text)
    if attributes:
        shape = _link(resource, "oslc:instanceShape")
        if shape is None:
            raise LookupError(f"Requisito {requirement_id} não informa o tipo (oslc:instanceShape).")
        changes |= _attributes(shape["url"], configuration, attributes)
    updated = oslc.update(resource["url"], changes, configuration=configuration)
    return {"id": updated["id"], "title": updated["title"], "url": updated["url"]}
