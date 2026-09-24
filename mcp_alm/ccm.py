"""Tools do EWM para as skills (alm-setup e alm-ccm): saída com as chaves do alm.json."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from .ibm import common, workitems
from .infra import oslc
from .infra.http import AlmHttpError, get_session, reportable
from .server import tool

PROJECT_AREA = "/ccm/process/project-areas/{pa}"
TEAM_AREA = "/ccm/process/project-areas/{pa}/team-areas/{team}"
REPORTABLE_PLANS = "/ccm/rpt/repository/apt"
PLAN_FIELDS = "apt/iterationPlanRecord[contextId={pa}]/(name|itemId|archived|owner/itemId|iteration/itemId)"
# ponytail: fuso fixo de Brasília (como a UI mostra); tornar configurável se houver servidor em outro fuso
BRT = timezone(timedelta(hours=-3))
# campos que já são parâmetros de ccm_create_workitem (summary, description)
PARAMETER_FIELDS = {"dcterms:title", "dcterms:description"}
ITERATION = "/ccm/oslc/iterations/{id}"
TEAM_AREA_OSLC = "/ccm/oslc/teamareas/{id}"
SUMMARY_SELECT = ("dcterms:identifier,dcterms:title,dcterms:type,rtc_cm:state{dcterms:title},"
                  "dcterms:contributor,rtc_cm:plannedFor{dcterms:title}")
# predicado -> tipo do campo (de onde vêm os valores)
KINDS = {"rtc_cm:filedAgainst": "category", "rtc_cm:plannedFor": "iteration", "rtc_cm:teamArea": "team-area",
         "rtc_cm:foundIn": "release", "dcterms:contributor": "member", "dcterms:creator": "member",
         "rtc_cm:resolvedBy": "member"}
VALUE_KINDS = {"xsd:string": "text", "rdf:XMLLiteral": "text", "xsd:integer": "integer", "xsd:int": "integer",
               "xsd:long": "integer", "xsd:dateTime": "date", "xsd:date": "date", "xsd:boolean": "boolean"}


def _url(template: str, **values: str) -> str:
    return get_session().url(template.format(**values))


def _local_date(value: str | None) -> str | None:
    """'2026-12-19T02:59:00.000Z' -> '2026-12-18' (dia em Brasília; cortar a string erra o dia)."""
    # replace("Z"): fromisoformat só aceita o sufixo Z a partir do Python 3.11
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(BRT).date().isoformat() if value else None


def _type(project_area_identifier: str, workitem_type: str) -> dict:
    found = next((t for t in workitems.types(project_area_identifier) if t["id"] == workitem_type), None)
    if found is None:
        raise LookupError(f"Tipo '{workitem_type}' não existe em {project_area_identifier}.")
    return found


def _props(project_area_identifier: str, workitem_type: str) -> list[dict]:
    t = _type(project_area_identifier, workitem_type)
    return oslc.shape(t["shape"])["properties"] if t["shape"] else []


def _kind(prop: dict) -> str:
    if prop["predicate"] in KINDS:
        return KINDS[prop["predicate"]]
    if any("/oslc/enumerations/" in r for r in prop["range"]):
        return "enumeration"
    return VALUE_KINDS.get(prop["value_type"], "resource")


@tool
def ccm_list_team_areas(project_area_identifier: str) -> list[dict]:
    """Times (team areas) da project area em lista plana: [{name, identifier, parent}] (parent = time pai ou None).
    alm.json: team-areas {name: identifier}."""
    return [{"name": t["name"], "identifier": t["team_area_uuid"], "parent": t["parent"]}
            for t in common.team_areas(_url(PROJECT_AREA, pa=project_area_identifier))]


@tool
def ccm_list_members(project_area_identifier: str, team_area_identifiers: list[str]) -> list[dict]:
    """Membros dos times escolhidos, sem repetição: [{identifier (login), name, team-areas: [identifier]}].
    alm.json: members {identifier: name}."""
    if not team_area_identifiers:
        raise ValueError("Informe ao menos um time em team_area_identifiers.")
    names = {c["userId"]: c["name"] for c in common.contributors()}
    found: dict[str, dict] = {}
    for team in team_area_identifiers:
        for login in common.area_members(_url(TEAM_AREA, pa=project_area_identifier, team=team)):
            member = found.setdefault(login, {"identifier": login, "name": names.get(login), "team-areas": []})
            member["team-areas"].append(team)
    return sorted(found.values(), key=lambda m: (m["name"] or m["identifier"]).lower())


@tool
def ccm_list_workitem_types(project_area_identifier: str) -> list[dict]:
    """Tipos de work item: [{identifier, name}]. alm.json: workitem-types {name: {identifier, fields}}."""
    return [{"identifier": t["id"], "name": oslc.shape(t["shape"])["title"] if t["shape"] else t["title"]}
            for t in workitems.types(project_area_identifier)]


@tool
def ccm_list_workitem_fields(project_area_identifier: str, workitem_type: str) -> list[dict]:
    """Campos editáveis do tipo: [{name, attribute, required, kind}]. kind: category, iteration, team-area,
    release, member, enumeration, text, integer, date, boolean ou resource. Inclua sempre os required.
    alm.json: workitem-types[name].fields {name: attribute}."""
    return [{"name": p["title"], "attribute": p["predicate"], "required": p["required"], "kind": _kind(p)}
            for p in _props(project_area_identifier, workitem_type)
            if not p["read_only"] and p["predicate"] not in PARAMETER_FIELDS]


@tool
def ccm_list_iterations(project_area_identifier: str) -> list[dict]:
    """Iterações de todas as timelines, em lista plana: [{name, identifier, start-date?, end-date?, parent?}].
    Nomes repetidos viram caminho ('2026/Sprint 01'). alm.json: iterations {name: identifier}."""
    rows = []

    timeline_of: dict[str, str] = {}

    def walk(nodes: list[dict], parent: str | None, timeline: str) -> None:
        for n in nodes:
            rows.append({"name": n["label"], "identifier": n["iteration_uuid"],
                         "start-date": _local_date(n["start_date"]), "end-date": _local_date(n["end_date"]),
                         "parent": parent})
            timeline_of[n["iteration_uuid"]] = timeline
            walk(n["children"], n["iteration_uuid"], timeline)

    for timeline in common.timelines(_url(PROJECT_AREA, pa=project_area_identifier)):
        walk(timeline["iterations"], None, timeline["label"] or "")
    by_id = {r["identifier"]: r for r in rows}

    def path(r: dict) -> str:
        return f"{path(by_id[r['parent']])}/{r['name']}" if r["parent"] in by_id else r["name"]

    def unique(names: dict[str, str], fallback) -> dict[str, str]:
        counts = Counter(names.values())
        return {i: fallback(i) if counts[n] > 1 else n for i, n in names.items()}

    # nome repetido vira caminho; caminho ainda repetido (timelines diferentes) ganha o nome da timeline
    names = unique({r["identifier"]: r["name"] for r in rows}, lambda i: path(by_id[i]))
    names = unique(names, lambda i: f"{timeline_of[i]}/{path(by_id[i])}")
    return [{k: v for k, v in {**r, "name": names[r["identifier"]]}.items() if v is not None} for r in rows]


@tool
def ccm_list_iteration_plans(project_area_identifier: str, iteration_identifiers: list[str] | None = None) -> list[dict]:
    """Planos de iteração não arquivados, opcionalmente só das iterações informadas:
    [{name, identifier, owner (time ou project area), iteration}]. alm.json: plans {name: {identifier, owner,
    iteration}}."""
    wanted = set(iteration_identifiers or [])
    plans = [{"name": r.findtext("name"), "identifier": r.findtext("itemId"), "owner": r.findtext("owner/itemId"),
              "iteration": r.findtext("iteration/itemId")}
             for r in reportable(REPORTABLE_PLANS, PLAN_FIELDS.format(pa=project_area_identifier), "iterationPlanRecord")
             if r.findtext("archived") != "true"]
    return [p for p in plans if not wanted or p["iteration"] in wanted]


# --- alm-ccm

def _first(resource: dict, predicate: str) -> dict | None:
    return next(iter(resource["links"].get(predicate, [])), None)


def _title(link: dict | None) -> str | None:
    return (link.get("title") or oslc.title(link["url"])) if link else None


def _summary(resource: dict) -> dict:
    """Work item enxuto: {id, title, type, state, owner (login), iteration, url}."""
    owner = _first(resource, "dcterms:contributor")
    return {"id": resource["id"], "title": resource["title"], "type": resource["properties"].get("dcterms:type"),
            "state": _title(_first(resource, "rtc_cm:state")), "owner": oslc.item_id(owner["url"]) if owner else None,
            "iteration": _title(_first(resource, "rtc_cm:plannedFor")), "url": resource["url"]}


@tool
def ccm_list_workitems(
    project_area_identifier: str,
    iteration: str | None = None,
    team_areas: list[str] | None = None,
    owner: str | None = None,
    state: str | None = None,
    workitem_type: str | None = None,
) -> list[dict]:
    """Work items (até 1000): [{id, title, type, state, owner, iteration, url}]. Filtros combinam com 'e';
    informe ao menos iteration, team_areas ou owner. Plano do alm.json: iteration=plans[nome].iteration e
    team_areas=[plans[nome].owner] (dono = project area não filtra time). team_areas inclui os subtimes.
    `state`: nome do estado ('Em Desenvolvimento'). `workitem_type`: identifier ('task')."""
    teams = [t for t in team_areas or [] if t != project_area_identifier]
    if not (iteration or teams or owner):
        raise ValueError("Informe ao menos um filtro: iteration, team_areas (time) ou owner.")
    clauses = []
    if iteration:
        clauses.append(oslc.where_eq("rtc_cm:plannedFor", _url(ITERATION, id=iteration)))
    if teams:
        all_teams, ids = common.team_areas(_url(PROJECT_AREA, pa=project_area_identifier)), set(teams)
        frontier = set(ids)
        while frontier:  # subtimes em qualquer nível
            frontier = {t["team_area_uuid"] for t in all_teams if t["parent"] in frontier} - ids
            ids |= frontier
        clauses.append(oslc.where_in("rtc_cm:teamArea", [_url(TEAM_AREA_OSLC, id=i) for i in sorted(ids)]))
    if owner:
        clauses.append(oslc.where_eq("dcterms:contributor", common.user_url(owner)))
    if workitem_type:
        clauses.append(oslc.where_eq("dcterms:type", workitem_type))
    base = oslc.query_base(oslc.provider("ccm", project_area_identifier), oslc.OSLC_CM.ChangeRequest)
    found = [_summary(r) for r in oslc.query(base, where=" and ".join(clauses), select=SUMMARY_SELECT,
                                             limit=oslc.MAX_LIMIT)]
    return [w for w in found if not state or (w["state"] or "").lower() == state.lower()]


@tool
def ccm_list_field_values(project_area_identifier: str, workitem_type: str, attribute: str) -> dict:
    """Tipo, obrigatoriedade e valores de um campo do alm.json: {kind, required, values: [{identifier, name,
    default}]}. Use `identifier` em fields de ccm_create_workitem/ccm_update_workitem. values vem vazio para
    text/integer/date/member (membros: alm.json)."""
    prop = next((p for p in _props(project_area_identifier, workitem_type) if p["predicate"] == attribute), None)
    if prop is None:
        raise LookupError(f"Atributo '{attribute}' não existe no tipo '{workitem_type}'.")
    kind, pa = _kind(prop), project_area_identifier
    if kind == "category":
        values = [{"identifier": c["itemId"], "name": c["name"]}
                  for c in workitems.list_workitem_categories(pa, limit=workitems.MAX_PAGE)]
    elif kind == "release":
        values = [{"identifier": r["itemId"], "name": r["name"]}
                  for r in workitems.list_workitem_releases(pa, limit=workitems.MAX_PAGE)]
    elif kind == "iteration":
        values = [{"identifier": i["identifier"], "name": i["name"]} for i in ccm_list_iterations(pa)]
    elif kind == "team-area":
        values = [{"identifier": t["identifier"], "name": t["name"]} for t in ccm_list_team_areas(pa)]
    elif kind == "enumeration":
        values = [{"identifier": v["url"], "name": v["title"]} for v in workitems.enumeration(prop)]
    else:
        values = []
    default = oslc.item_id(prop["default"])
    return {"kind": kind, "required": prop["required"],
            "values": [{**v, "default": default is not None and oslc.item_id(v["identifier"]) == default}
                       for v in values]}


@tool
def ccm_create_workitem(
    project_area_identifier: str, workitem_type: str, summary: str, description: str | None = None,
    fields: dict | None = None, parent: str | None = None,
) -> dict:
    """Cria um work item. `fields`: {atributo do alm.json: identifier de ccm_list_field_values ou valor}.
    `parent`: id do work item pai. Retorna {id, title, type, state, owner, iteration, url}."""
    attributes = {"dcterms:title": summary, **({"dcterms:description": description} if description else {}),
                  **(fields or {})}
    links = [{"endpointId": "parent", "targetWorkItemId": parent}] if parent else []
    return _summary(workitems.create_workitem(project_area_identifier, workitem_type, json.dumps(attributes),
                                              json.dumps(links)))


def _workflow_of(resource: dict) -> tuple[str | None, list[dict]]:
    """(nome do estado atual, ações do workflow do work item)."""
    state = _first(resource, "rtc_cm:state")
    if state is None:
        raise LookupError(f"Work item {resource['id']} não expõe rtc_cm:state.")
    wf = workitems.workflow(state["url"].rsplit("/", 1)[0])
    return next((s["title"] for s in wf["states"] if s["url"] == state["url"]), None), wf["actions"]


@tool
def ccm_list_workitem_states(workitem_id: str) -> dict:
    """Estado atual e ações do workflow: {state, actions: [{name, result-state}]}. O servidor só aceita ações
    que saem do estado atual."""
    current, actions = _workflow_of(oslc.get(workitems.workitem_url(workitem_id)))
    return {"state": current, "actions": [{"name": a["title"], "result-state": a["resultState"]} for a in actions]}


@tool
def ccm_update_workitem(workitem_id: str, fields: dict | None = None, state: str | None = None) -> dict:
    """Atualiza campos ({atributo: identifier ou valor}) e/ou muda o estado pelo nome ('Pronto').
    Retorna {id, title, type, state, owner, iteration, url}."""
    if not fields and not state:
        raise ValueError("Informe fields e/ou state.")
    url = workitems.workitem_url(workitem_id)
    resource = oslc.get(url)
    pa = oslc.item_id((_first(resource, "rtc_cm:projectArea") or {}).get("url")) or ""
    attributes = {attribute: workitems.node(pa, attribute, value) for attribute, value in (fields or {}).items()}
    if not state:
        return _summary(oslc.update(url, attributes))
    _, actions = _workflow_of(resource)
    candidates = [a for a in actions if (a["resultState"] or "").lower() == state.lower()]
    if not candidates:
        states = sorted({a["resultState"] for a in actions if a["resultState"]})
        raise ValueError(f"Estado '{state}' não existe no workflow. Estados: {', '.join(states)}.")
    # o workflow não diz de qual estado cada ação parte: tenta as ações que levam ao destino até o servidor aceitar
    first_error = None
    for action in candidates:
        try:
            updated = _summary(oslc.update(url, attributes, params={"_action": action["id"]}))
            break
        except AlmHttpError as exc:
            first_error = first_error or exc
    else:
        raise first_error
    if (updated["state"] or "").lower() != state.lower():
        raise ValueError(f"O servidor não mudou o estado: continua '{updated['state']}' (pedido: '{state}').")
    return updated
