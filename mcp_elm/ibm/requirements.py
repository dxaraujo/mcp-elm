"""Requirements: componentes, tipos, pastas, configurações e requisitos do DOORS Next."""
from __future__ import annotations

from html import escape
from typing import Literal

from rdflib import Literal as RdfLiteral, URIRef
from rdflib.namespace import DCTERMS, RDF

from ..infra import oslc
from ..infra.http import get_session
from ..server import tool

COMPONENT = "/rm/cm/component/{id}"
CONFIGURATION_TYPES = {"stream": "oslc_config:Stream", "baseline": "oslc_config:Baseline",
                       "changeset": "oslc_config:ChangeSet"}


def xhtml(text: str) -> RdfLiteral:
    """Texto simples (escapado) ou XHTML -> jazz_rm:primaryText (XMLLiteral)."""
    body = text if text.lstrip().startswith("<") else escape(text)
    return RdfLiteral(f'<div xmlns="{oslc.XHTML}">{body}</div>', datatype=RDF.XMLLiteral)


def _component_url(component_id: str) -> str:
    return get_session().url(COMPONENT.format(id=component_id))


def default_stream(component_url: str) -> str:
    stream = next((c["url"] for c in oslc.configurations(component_url) if "oslc_config:Stream" in c["types"]), None)
    if stream is None:
        raise LookupError(f"Componente {component_url} não tem stream.")
    return stream


def _context(component_url: str, configuration_url: str | None = None,
             global_configuration_url: str | None = None) -> str:
    """Configuração a usar: a local, a global ou, sem nenhuma, a primeira stream do componente."""
    if configuration_url and global_configuration_url:
        raise ValueError("Informe configuration_url ou global_configuration_url, não os dois.")
    return configuration_url or global_configuration_url or default_stream(component_url)


def requirements_base(project_area_uuid: str, context: str) -> str:
    return oslc.query_base(oslc.provider("rm", project_area_uuid), oslc.OSLC_RM.Requirement, context)


@tool
def get_project_components(project_area: str, component_id: str | None = None) -> list[dict]:
    """Componentes de uma project area do DOORS Next (título exato ou UUID): [{url, id, title, ...}].
    `component_id`: devolve só o componente com esse UUID."""
    sp = next((p["url"] for p in oslc.providers("rm") if project_area in (p["project_area_uuid"], p["title"])), None)
    if sp is None:
        raise LookupError(f"Project area '{project_area}' não encontrada no DOORS Next.")
    return [oslc.get(c) for c in oslc.services(sp)["components"]
            if not component_id or oslc.item_id(c) == component_id]


@tool
def get_rm_component_types(
    project_area_uuid: str, component_id: str, configuration_url: str | None = None, filter_text_only: bool = False,
) -> list[dict]:
    """Tipos de artefato criáveis (shapes): [{url, title, describes, properties[]}]. Use `url` como
    artifact_type_url em create_requirement. `filter_text_only` é redundante aqui: a creation factory de
    requisitos só publica tipos de texto."""
    context = _context(_component_url(component_id), configuration_url)
    shapes = {s for f in oslc.creation_factories(oslc.provider("rm", project_area_uuid), oslc.OSLC_RM.Requirement,
                                                  context) for s in f["resource_shapes"]}
    return sorted((oslc.shape(s, context) for s in shapes), key=lambda s: s["title"].lower())


@tool
def list_rm_component_folders(
    component_url: str, configuration_url: str | None = None, include_private: bool = True,
) -> list[dict]:
    """Todas as pastas do componente, em lista plana: [{url, title, parent, ...}]. A primeira é a raiz
    (parent None). `include_private` é aceito por compatibilidade; o OSLC só devolve o que o usuário pode ver."""
    context = configuration_url or default_stream(component_url)
    g, subject, _ = oslc.fetch(component_url)
    project_area = next((str(o) for p, o in g.predicate_objects(subject) if str(p).endswith("projectArea")), None)
    if project_area is None:
        raise LookupError(f"Componente {component_url} não informa a project area.")
    # a query base do DOORS Next já filtra pela project area e devolve a pasta raiz
    base = oslc.query_base(oslc.provider("rm", oslc.item_id(project_area)), oslc.RM_NAV.folder, context)
    folders, pending = [], [(base, None)]
    while pending:  # ponytail: 1 query por pasta (nav:subfolders); trocar por consulta única se ficar lento
        url, parent = pending.pop(0)
        for folder in oslc.query(url, select=None, configuration=context, limit=oslc.MAX_LIMIT):
            folders.append({**folder, "parent": parent})
            pending += [(sub["url"], folder["url"]) for sub in folder["links"].get("nav:subfolders", [])]
    return folders


@tool
def get_rm_component_configuration(
    project_area_uuid: str, component_id: str,
    configuration_type: Literal["stream", "baseline", "changeset", "all"] = "all",
) -> list[dict]:
    """Streams, baselines e change sets do componente: [{url, title, types}]."""
    configurations = oslc.configurations(_component_url(component_id))
    if configuration_type == "all":
        return configurations
    return [c for c in configurations if CONFIGURATION_TYPES[configuration_type] in c["types"]]


@tool
def get_requirement(
    project_area_uuid: str, component_id: str, requirement_id: str,
    configuration_url: str | None = None, global_configuration_url: str | None = None,
) -> dict:
    """Requisito pelo id numérico do DOORS Next: {url, id, title, types, properties, links}.
    Sem configuração, usa a primeira stream do componente."""
    if not requirement_id.isdigit():
        raise ValueError(f"requirement_id deve ser numérico (use search_requirement para texto): {requirement_id}")
    context = _context(_component_url(component_id), configuration_url, global_configuration_url)
    found = oslc.query(requirements_base(project_area_uuid, context),
                       where=oslc.where_eq("dcterms:identifier", int(requirement_id)),
                       select="dcterms:identifier", limit=1, configuration=context)
    if not found:
        raise LookupError(f"Requisito {requirement_id} não encontrado.")
    return oslc.get(found[0]["url"], context)


@tool
def search_requirement(
    project_area_uuid: str, component_id: str, search_text: str,
    configuration_url: str | None = None, global_configuration_url: str | None = None,
) -> list[dict]:
    """Requisitos cujo título ou texto contém `search_text` (oslc.searchTerms), até 100."""
    context = _context(_component_url(component_id), configuration_url, global_configuration_url)
    return oslc.query(requirements_base(project_area_uuid, context), search_terms=search_text,
                      configuration=context)


@tool
def create_requirement(
    project_area_url: str,
    component_url: str,
    artifact_type_url: str,
    title: str,
    description: str,
    primary_text: str,
    configuration_url: str | None = None,
    folder_url: str | None = None,
    global_configuration_url: str | None = None,
) -> dict:
    """Cria um requisito. `artifact_type_url`: url de get_rm_component_types. `primary_text`: texto ou XHTML.
    `folder_url`: pasta (FR_*), não módulo (MD_*). Só em stream ou change set; baseline é recusada."""
    if folder_url and "MD_" in folder_url:
        raise ValueError("folder_url é um módulo (MD_); informe uma pasta (FR_).")
    context = _context(component_url, configuration_url, global_configuration_url)
    g, subject, _ = oslc.fetch(context)  # baseline local ou GC: pelo tipo, não pelo formato da URL
    if (subject, RDF.type, oslc.OSLC_CONFIG.Baseline) in g:
        raise ValueError("Não é possível criar requisito em baseline; use uma stream ou um change set.")
    factories = oslc.creation_factories(oslc.provider("rm", oslc.item_id(project_area_url)),
                                        oslc.OSLC_RM.Requirement, context)
    factory = next((f["creation"] for f in factories if artifact_type_url in f["resource_shapes"]),
                   factories[0]["creation"])
    graph = oslc.new_resource(oslc.OSLC_RM.Requirement, {
        DCTERMS.title: RdfLiteral(title),
        DCTERMS.description: RdfLiteral(description),
        oslc.OSLC.instanceShape: URIRef(artifact_type_url),
        oslc.JAZZ_RM.primaryText: xhtml(primary_text),
        oslc.RM_NAV.parent: URIRef(folder_url) if folder_url else None,
    })
    return oslc.get(oslc.create(factory, graph, context), context)

