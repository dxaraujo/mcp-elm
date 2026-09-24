"""Test: artefatos de teste, schema, busca, comentários, componentes e configurações do ETM."""
from __future__ import annotations

from typing import Literal

from rdflib import URIRef

from ..infra import oslc
from ..infra.http import get_session
from ..server import tool

ArtifactType = Literal["TestPlan", "TestCase", "TestSuite", "TestScript", "TestCaseExecutionRecord",
                       "TestCaseResult", "TestSuiteExecutionRecord", "TestSuiteResult"]

# tipo da doc IBM -> rdf:type das creation factories; a query capability do ETM usa o mesmo tipo + "Query"
ARTIFACT_TYPES = {
    "TestPlan": oslc.OSLC_QM.TestPlan,
    "TestCase": oslc.OSLC_QM.TestCase,
    "TestSuite": oslc.RQM.TestSuite,
    "TestScript": oslc.OSLC_QM.TestScript,
    "TestCaseExecutionRecord": oslc.OSLC_QM.TestExecutionRecord,
    "TestCaseResult": oslc.OSLC_QM.TestResult,
    "TestSuiteExecutionRecord": oslc.RQM.TestSuiteExecutionRecord,
    "TestSuiteResult": oslc.RQM.TestSuiteResult,
}
CONFIGURATION = "/qm/oslc_config/resources/com.ibm.team.vvc.Configuration/{uuid}"
# chaves curtas aceitas em filters/properties -> predicado (qname também é aceito)
FIELDS = {"title": "dcterms:title", "name": "dcterms:title", "id": "oslc:shortId", "owner": "dcterms:contributor",
          "creator": "dcterms:creator", "created": "dcterms:created", "modified": "dcterms:modified",
          "description": "dcterms:description"}
BASIC = {"oslc:shortId", "dcterms:contributor", "dcterms:modified"}


def _type(artifact_type: str) -> URIRef:
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError(f"artifact_type inválido: {artifact_type}. Use: {', '.join(ARTIFACT_TYPES)}.")
    return ARTIFACT_TYPES[artifact_type]


def _configuration(configuration: str | None) -> str | None:
    """UUID ('_x') ou URL da configuração local do ETM -> URL."""
    if configuration and not configuration.startswith(("http://", "https://")):
        return get_session().url(CONFIGURATION.format(uuid=configuration))
    return configuration


def _field(key: str) -> str:
    if key in FIELDS:
        return FIELDS[key]
    if ":" in key:
        return key
    raise ValueError(f"Campo desconhecido: {key}. Use {', '.join(FIELDS)} ou um qname (ex.: dcterms:title).")


def _base(project_area_uuid: str, artifact_type: str, configuration: str | None) -> str:
    query_type = URIRef(str(_type(artifact_type)) + "Query")
    return oslc.query_base(oslc.provider("qm", project_area_uuid), query_type, configuration)


@tool
def get_testartifact(
    project_area_uuid: str, artifact_type: ArtifactType, id: str | None = None, url: str | None = None,
    fetch_all: bool = False, configuration: str | None = None,
) -> dict:
    """Artefato de teste pelo id web ou URL: {url, id, title, types, properties, links}. Sem `fetch_all`,
    só título, id, owner e modificação. `configuration`: UUID ou URL da configuração local (opt-in)."""
    if bool(id) == bool(url):
        raise ValueError("Informe id ou url (um dos dois).")
    context = _configuration(configuration)
    if id:
        found = oslc.query(_base(project_area_uuid, artifact_type, context),
                           where=oslc.where_eq("oslc:shortId", int(id) if id.isdigit() else id),
                           select="oslc:shortId", limit=1, configuration=context)
        if not found:
            raise LookupError(f"{artifact_type} {id} não encontrado.")
        url = found[0]["url"]
    else:
        _type(artifact_type)
    resource = oslc.get(url, context)
    if fetch_all:
        return resource
    return {**resource, "properties": {k: v for k, v in resource["properties"].items() if k in BASIC},
            "links": {k: v for k, v in resource["links"].items() if k in BASIC}}


@tool
def get_testartifact_schema(project_area_uuid: str, artifact_type: ArtifactType) -> dict:
    """Atributos do tipo de artefato de teste a partir do resource shape de criação: {artifactType, shapes:
    [{url, title, properties: [{name, title, predicate, required, allowed_values, ...}]}]} (inclui custom
    attributes, categorias, estados e prioridades que o shape publica)."""
    shapes = oslc.resource_shapes(oslc.provider("qm", project_area_uuid), _type(artifact_type))
    return {"artifactType": artifact_type, "shapes": [oslc.shape(s) for s in shapes]}


@tool
def search_testartifact(
    project_area_uuid: str,
    artifact_type: ArtifactType,
    filters: dict | None = None,
    customAttributeFilters: list | None = None,
    categoryFilters: list | None = None,
    linkFilters: dict | None = None,
    properties: list[str] | None = None,
    configuration: str | None = None,
) -> list[dict]:
    """Busca artefatos de teste (até 1000). `filters`: {campo: valor} com title, id, owner, creator, created,
    modified, description ou qname (igualdade, combinados com and). `properties`: campos a devolver.
    customAttributeFilters, categoryFilters e linkFilters não são suportados."""
    unsupported = [n for n, v in (("customAttributeFilters", customAttributeFilters),
                                  ("categoryFilters", categoryFilters), ("linkFilters", linkFilters)) if v]
    if unsupported:
        raise ValueError(f"Não suportado: {', '.join(unsupported)}.")
    where = " and ".join(oslc.where_eq(_field(k), v) for k, v in (filters or {}).items()) or None
    select = ",".join(_field(p) for p in properties) if properties else oslc.DEFAULT_SELECT
    context = _configuration(configuration)
    return oslc.query(_base(project_area_uuid, artifact_type, context), where=where, select=select,
                      limit=oslc.MAX_LIMIT, configuration=context)


@tool
def get_qm_component(
    project_area_uuid: str, component_name: str | None = None, component_uuid: str | None = None,
) -> list[dict]:
    """Componentes da project area do ETM (opt-in): [{url, id, title, ...}]. Filtros: trecho do nome e/ou UUID.
    Usa a query de componentes do provider de configuração do ETM, filtrada por process:projectArea."""
    providers = oslc.providers("qm", "config")
    sp = next((p["url"] for p in providers if p["project_area_uuid"] == project_area_uuid), providers[0]["url"])
    components = oslc.query(oslc.query_base(sp, oslc.OSLC_CONFIG.Component),
                            select="dcterms:title,process:projectArea", limit=oslc.MAX_LIMIT)
    return [c for c in components
            if all(oslc.item_id(p["url"]) == project_area_uuid for p in c["links"].get("process:projectArea", []))
            and (not component_uuid or oslc.item_id(c["url"]) == component_uuid)
            and (not component_name or component_name.lower() in (c["title"] or "").lower())]


@tool
def get_qm_component_configuration(
    project_area_uuid: str, component_uuid: str | None = None, configuration_name: str | None = None,
    configuration_type: Literal["Stream", "Baseline"] | None = None, configuration_uuid: str | None = None,
) -> list[dict]:
    """Streams e baselines dos componentes da project area do ETM: [{url, title, types, component}].
    Filtros: componente, trecho do nome, tipo e UUID da configuração."""
    out = []
    for component in get_qm_component(project_area_uuid, component_uuid=component_uuid):
        for c in oslc.configurations(component["url"]):
            if ((not configuration_name or configuration_name.lower() in c["title"].lower())
                    and (not configuration_type or f"oslc_config:{configuration_type}" in c["types"])
                    and (not configuration_uuid or oslc.item_id(c["url"]) == configuration_uuid)):
                out.append({**c, "component": component["url"]})
    return out
