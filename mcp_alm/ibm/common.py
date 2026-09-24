"""Common: usuários, project areas, global configuration e links entre rm, ccm e qm."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..infra import oslc
from ..infra.http import get_session, get_xml, reportable
from ..server import tool

AppType = Literal["CCM", "RM", "QM", "GC"]

PROJECT_AREAS = "/{app}/process/project-areas"
TEAM_AREAS = "{project_area}/team-areas"
TIMELINES = "{project_area}/timelines"
LINKS = "{project_area}/links"
MEMBERS = "{area}/members"
CONTRIBUTORS = "/ccm/rpt/repository/foundation"
CONTRIBUTOR_FIELDS = "foundation/contributor/(itemId|userId|name|emailAddress|archived)"
JTS_USER = "/jts/users/{user_id}"
GLOBAL_CONFIGURATION = "/gc/configuration/{id}"
MAX_MATCHES = 5
MIN_SEARCH = 3

_JP06 = oslc.clark(oslc.JP06)
_JP = oslc.clark(oslc.JP)
_DISCOVERY = oslc.clark("http://open-services.net/xmlns/discovery/1.0/")
_DC = oslc.clark("http://purl.org/dc/terms/")
_RDF_RESOURCE = oslc.clark("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "resource")


def _one_of(**values) -> None:
    """Exige exatamente um dos parâmetros."""
    given = [k for k, v in values.items() if v]
    if len(given) != 1:
        raise ValueError(f"Informe exatamente um entre: {', '.join(values)}.")


def _search(term: str) -> str:
    term = term.strip().lower()
    if len(term) < MIN_SEARCH:
        raise ValueError(f"O termo de busca precisa de ao menos {MIN_SEARCH} caracteres.")
    return term


# --- usuários

@lru_cache(maxsize=1)
def contributors() -> tuple[dict, ...]:
    """Todos os usuários do repositório (Reportable REST), no formato de saída de get_user."""
    # ponytail: carrega todos os usuários uma vez por processo; filtrar no servidor se a lista ficar grande
    return tuple({
        "userUUID": c.findtext("itemId"),
        "userId": c.findtext("userId"),
        "name": c.findtext("name"),
        "emailAddress": c.findtext("emailAddress"),
        "archived": c.findtext("archived") == "true",
    } for c in reportable(CONTRIBUTORS, CONTRIBUTOR_FIELDS, "contributor"))


def user_url(user: str) -> str:
    """URL do usuário no JTS a partir do UUID ('_x...') ou do login."""
    if user.startswith("_"):
        user = get_user(user_uuid=user)["userId"]
    return get_session().url(JTS_USER.format(user_id=quote(user)))


@tool
def get_user(user_uuid: str | None = None, search_term: str | None = None) -> dict:
    """Usuário do ELM pelo UUID ('_...') ou por termo (login ou nome, mín. 3 caracteres).
    Um resultado: {userUUID, userId, name, emailAddress, archived}. Vários: {error_message,
    requires_selection: true, matching_users: [até 5]} — chame de novo com user_uuid."""
    _one_of(user_uuid=user_uuid, search_term=search_term)
    if user_uuid:
        found = [c for c in contributors() if c["userUUID"] == user_uuid]
    else:
        term = _search(search_term)
        found = [c for c in contributors()
                 if term in (c["userId"] or "").lower() or term in (c["name"] or "").lower()]
    if not found:
        raise LookupError(f"Nenhum usuário encontrado para '{user_uuid or search_term}'.")
    if len(found) == 1:
        return found[0]
    return {"error_message": "Mais de um usuário encontrado; chame get_user de novo com user_uuid.",
            "requires_selection": True, "matching_users": found[:MAX_MATCHES]}


@tool
def whoami() -> dict:
    """Usuário autenticado (login do alm.properties): {userUUID, userId, name, emailAddress, archived}.
    Serve para testar a conexão: falha de credencial vira erro de autenticação."""
    login = get_session().cfg.user.lower()
    found = next((c for c in contributors() if (c["userId"] or "").lower() == login), None)
    if found is None:
        raise LookupError(f"Usuário '{login}' autenticou, mas não está no repositório do ccm.")
    return found


# --- project areas

def _project_areas(app: str) -> list[dict]:
    root = get_xml(PROJECT_AREAS.format(app=app))
    return [{
        "name": pa.get(_JP06 + "name"),
        "project_area_uuid": oslc.item_id(pa.findtext(_JP06 + "url")),
        "url": pa.findtext(_JP06 + "url"),
        "summary": pa.findtext(_JP06 + "summary"),
        "description": pa.findtext(_JP06 + "description"),
        "cm_enabled": pa.findtext(_JP + "configuration-management-enabled") == "true",
    } for pa in root.findall(_JP06 + "project-area")]


@tool
def list_project_areas(app_type: AppType, search_name: str | None = None, cm_enabled: bool | None = None) -> list[dict]:
    """Project areas de um app (CCM, RM, QM ou GC): [{name, project_area_uuid, url, summary, description,
    cm_enabled}]. `search_name`: trecho do nome (mín. 3 caracteres). `cm_enabled`: filtra por gerenciamento
    de configuração (não se aplica a GC)."""
    app = app_type.lower()
    if app == "gc" and cm_enabled is not None:
        raise ValueError("cm_enabled não se aplica a GC.")
    areas = _project_areas(app)
    if search_name is not None:
        term = _search(search_name)
        areas = [a for a in areas if term in (a["name"] or "").lower()]
    if cm_enabled is not None:
        areas = [a for a in areas if a["cm_enabled"] == cm_enabled]
    return sorted(areas, key=lambda a: (a["name"] or "").lower())


def team_areas(project_area_url: str) -> list[dict]:
    """Team areas em lista plana: [{name, team_area_uuid, parent}] (parent = UUID do time pai; None na raiz)."""
    root = get_xml(TEAM_AREAS.format(project_area=project_area_url))
    teams = [{"name": t.get(_JP06 + "name"),
              "team_area_uuid": oslc.item_id(t.findtext(_JP06 + "url")),
              "parent": oslc.item_id(t.findtext(_JP06 + "parent-url"))} for t in root.findall(_JP06 + "team-area")]
    ids = {t["team_area_uuid"] for t in teams}
    return [{**t, "parent": t["parent"] if t["parent"] in ids else None} for t in teams]


def _team_tree(project_area_url: str) -> list[dict]:
    """Árvore de team areas: [{name, team_area_uuid, children[]}]."""
    teams = [{**t, "children": []} for t in team_areas(project_area_url)]
    by_id = {t["team_area_uuid"]: t for t in teams}
    roots = []
    for t in teams:
        parent = by_id.get(t.pop("parent"))
        (parent["children"] if parent else roots).append(t)
    return roots


def area_members(area_url: str) -> list[str]:
    """Logins dos membros de uma project area ou team area (API de processo)."""
    root = get_xml(MEMBERS.format(area=area_url))
    return [unquote(oslc.item_id(m.findtext(_JP06 + "user-url"))) for m in root.findall(_JP06 + "member")]


def iterations(url: str | None) -> list[dict]:
    if not url:
        return []
    return [{
        "id": it.get(_JP + "id"),
        "label": it.findtext(_JP + "label"),
        "iteration_uuid": oslc.item_id(it.findtext(_JP + "url")),
        "start_date": it.findtext(_JP + "start-date"),
        "end_date": it.findtext(_JP + "end-date"),
        "children": iterations(it.findtext(_JP + "childern-iterations-url")),  # sic: grafia do servidor
    } for it in get_xml(url).findall(_JP + "iteration")]


def timelines(project_area_url: str) -> list[dict]:
    root = get_xml(TIMELINES.format(project_area=project_area_url))
    return [{
        "id": t.get(_JP + "id"),
        "label": t.findtext(_JP + "label"),
        "timeline_uuid": oslc.item_id(t.findtext(_JP + "url")),
        "iterations": iterations(t.findtext(_JP + "iterations-url")),
    } for t in root.findall(_JP + "timeline")]


def _associations(project_area_url: str) -> dict[str, list[dict]]:
    """Associações da project area agrupadas pelo app da outra ponta: {rm|ccm|qm|gc: [{project_area_name,
    project_area_uuid, link_type}]}."""
    out: dict[str, list[dict]] = {}
    for link in get_xml(LINKS.format(project_area=project_area_url)).findall(_JP + "link"):
        provider = link.find(_DISCOVERY + "ServiceProvider")
        details = provider.find(_DISCOVERY + "details") if provider is not None else None
        url = details.get(_RDF_RESOURCE) if details is not None else None
        if not url:
            continue
        app = url.split("://", 1)[-1].split("/")[1]  # https://host/{app}/process/...
        out.setdefault(app, []).append({"project_area_name": provider.findtext(_DC + "title"),
                                        "project_area_uuid": oslc.item_id(url),
                                        "link_type": link.findtext(_JP + "link-type")})
    return out


@tool
def get_project_area(
    app_type: AppType,
    project_area_uuid: str | None = None,
    name: str | None = None,
    include_team_areas: bool = False,
    include_timelines: bool = False,
    include_associations: bool = False,
) -> dict:
    """Project area pelo UUID ou por nome (trecho, mín. 3 caracteres): {name, project_area_uuid, summary,
    description, cm_enabled, team_areas?, timelines?, associations?}. Vários nomes casando: {error_message,
    requires_selection: true, matching_project_areas} — chame de novo com project_area_uuid."""
    _one_of(project_area_uuid=project_area_uuid, name=name)
    areas = list_project_areas(app_type, search_name=name) if name else [
        a for a in list_project_areas(app_type) if a["project_area_uuid"] == project_area_uuid]
    if not areas:
        raise LookupError(f"Project area '{project_area_uuid or name}' não encontrada em {app_type}.")
    if len(areas) > 1:
        return {"error_message": "Mais de uma project area encontrada; chame de novo com project_area_uuid.",
                "requires_selection": True,
                "matching_project_areas": [{"name": a["name"], "project_area_uuid": a["project_area_uuid"]}
                                           for a in areas]}
    pa = areas[0]
    out = {k: pa[k] for k in ("name", "project_area_uuid", "summary", "description", "cm_enabled")}
    if include_team_areas:
        out["team_areas"] = _team_tree(pa["url"])
    if include_timelines:
        out["timelines"] = timelines(pa["url"])
    if include_associations:
        out["associations"] = _associations(pa["url"])
    return out


# --- global configuration

@tool
def get_global_configuration(gc_config_id: int) -> dict:
    """Global configuration pelo id numérico: recurso {url, id, title, types, properties, links} +
    contributedConfigs (configurações GC filhas) e localConfigs (streams/baselines/change sets de rm/qm/ccm),
    cada uma [{url, title}]."""
    if gc_config_id < 1:
        raise ValueError("gc_config_id deve ser um inteiro positivo.")
    url = get_session().url(GLOBAL_CONFIGURATION.format(id=gc_config_id))
    g, subject, _ = oslc.fetch(url)
    contributions = g.value(subject, oslc.OSLC_CONFIG.contributions)
    cg = g  # as contribuições podem vir no mesmo documento ou num recurso separado
    if (None, oslc.OSLC_CONFIG.contribution, None) not in g and contributions is not None:
        cg = oslc.fetch(str(contributions))[0]
    contributed, local = [], []
    for c in cg.objects(None, oslc.OSLC_CONFIG.contribution):
        target = cg.value(c, oslc.OSLC_CONFIG.configuration)
        if target is not None:
            entry = {"url": str(target), "title": oslc.title(str(target))}
            (contributed if "/gc/" in str(target) else local).append(entry)
    return {**oslc.resource(g, subject), "contributedConfigs": contributed, "localConfigs": local}


@tool
def search_global_configuration(
    gc_project_area_uuid: str, search_term: str = "*", configuration_type: str = "*",
) -> list[dict]:
    """Global configurations de uma project area do GC. `search_term`: trecho do título ('*' = todas).
    `configuration_type`: 'Stream', 'Baseline' ou '*'. Retorna recursos {url, title, types}."""
    base = oslc.query_base(oslc.provider("gc", gc_project_area_uuid, "config"), oslc.OSLC_CONFIG.Configuration)
    term = search_term.strip("*").strip().lower()
    kind = None if configuration_type.strip("*") == "" else f"oslc_config:{configuration_type.capitalize()}"
    found = oslc.query(base, select="dcterms:title,rdf:type", limit=oslc.MAX_LIMIT)
    return [c for c in found if term in (c["title"] or "").lower() and (kind is None or kind in c["types"])]


# --- links de rastreabilidade (gravados sempre no work item ou no teste; o DOORS Next mostra o backlink)

REQUIREMENT_LINKS = {
    "calm:implementsRequirement", "oslc_cm:implementsRequirement", "oslc_cm:affectsRequirement",
    "oslc_cm:tracksRequirement", "oslc_qm:validatesRequirement", "oslc_qm:validatesRequirementCollection",
    "oslc_rm:elaboratedBy", "oslc_rm:elaborates", "oslc_rm:satisfiedBy", "oslc_rm:satisfies",
    "oslc_rm:decomposedBy", "oslc_rm:decomposes", "oslc_rm:constrainedBy", "oslc_rm:constrains",
}
WORKITEM_LINKS = {
    "oslc_rm:implementedBy", "oslc_rm:affectedBy", "oslc_rm:trackedBy",
    "oslc_qm:testsChangeRequest", "oslc_qm:relatedChangeRequest", "oslc_qm:affectedByChangeRequest",
    "oslc_qm:blockedByChangeRequest", "oslc_cm:relatedChangeRequest",
}
TESTARTIFACT_LINKS = {
    "oslc_rm:validatedBy", "oslc_cm:testedByTestCase", "oslc_cm:affectsTestResult",
    "oslc_cm:blocksTestExecutionRecord", "oslc_cm:relatedTestCase", "oslc_cm:relatedTestExecutionRecord",
    "oslc_cm:relatedTestPlan", "oslc_cm:relatedTestScript", "calm:testedByTestCase",
}
WORKITEM_TO_REQUIREMENT = {
    "implements": "calm:implementsRequirement", "implementedby": "calm:implementsRequirement",
    "affects": "oslc_cm:affectsRequirement", "affectedby": "oslc_cm:affectsRequirement",
    "tracks": "oslc_cm:tracksRequirement", "trackedby": "oslc_cm:tracksRequirement",
}
WORKITEM_TO_TEST = {
    "affects": "oslc_cm:affectsTestResult", "affectedby": "oslc_cm:affectsTestResult",
    "blocks": "oslc_cm:blocksTestExecutionRecord", "blockedby": "oslc_cm:blocksTestExecutionRecord",
    "tests": "oslc_cm:testedByTestCase", "testedby": "oslc_cm:testedByTestCase",
}
# link "related" depende do tipo do artefato de teste (trecho da URL do ETM -> predicado)
RELATED_TEST = (("TestPlan", "oslc_cm:relatedTestPlan"), ("TestScript", "oslc_cm:relatedTestScript"),
                ("ExecutionRecord", "oslc_cm:relatedTestExecutionRecord"))


def _linked(source_url: str, predicates: set[str]) -> list[dict]:
    resource = oslc.get(source_url)
    return [{"link_type": pred, **link} for pred, links in resource["links"].items() if pred in predicates
            for link in links]


@tool
def list_linked_requirements(source_url: str) -> list[dict]:
    """Requisitos ligados a um work item ou artefato de teste: [{link_type, url, title?}]."""
    return _linked(source_url, REQUIREMENT_LINKS)


@tool
def list_linked_workitems(source_url: str) -> list[dict]:
    """Work items ligados a um artefato de teste ou requisito: [{link_type, url, title?}]."""
    return _linked(source_url, WORKITEM_LINKS)


@tool
def list_linked_testartifacts(source_url: str) -> list[dict]:
    """Artefatos de teste ligados a um work item ou requisito: [{link_type, url, title?}]."""
    return _linked(source_url, TESTARTIFACT_LINKS)


@tool
def link_workitem_and_requirement(
    workitem_url: str, requirement_url: str,
    link_type: Literal["implements", "affects", "tracks", "implementedby", "affectedby", "trackedby"] = "implements",
) -> dict:
    """Liga work item e requisito (gravado no work item; as formas *by gravam o mesmo link).
    Retorna o work item atualizado."""
    return oslc.update(workitem_url, {WORKITEM_TO_REQUIREMENT[link_type]: requirement_url}, append=True)


@tool
def link_workitem_and_testartifact(
    workitem_url: str, testartifact_url: str,
    link_type: Literal["affects", "blocks", "related", "tests", "affectedby", "blockedby", "testedby"] = "affects",
) -> dict:
    """Liga work item e artefato de teste (gravado no work item). Retorna o work item atualizado."""
    if link_type == "related":
        predicate = next((p for part, p in RELATED_TEST if part in testartifact_url), "oslc_cm:relatedTestCase")
    else:
        predicate = WORKITEM_TO_TEST[link_type]
    return oslc.update(workitem_url, {predicate: testartifact_url}, append=True)


@tool
def link_testartifact_and_requirement(
    testartifact_url: str, requirement_url: str,
    link_type: Literal["validates", "validatedby"] = "validates", gc_context: str | None = None,
) -> dict:
    """Liga artefato de teste e requisito (oslc_qm:validatesRequirement, gravado no teste; as duas formas
    gravam o mesmo link). `gc_context`: stream/baseline GC; se omitido, vem do oslc_config.context de uma
    das URLs."""
    if gc_context is None:
        for url in (requirement_url, testartifact_url):
            if found := parse_qs(urlsplit(url).query).get("oslc_config.context"):
                gc_context = found[0]
                break
    return oslc.update(testartifact_url, {"oslc_qm:validatesRequirement": requirement_url.split("?")[0]},
                       append=True, configuration=gc_context)
