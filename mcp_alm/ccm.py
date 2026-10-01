"""Tools do EWM para as skills (alm-setup e alm-ccm): saída com as chaves do alm.json."""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, time

from .ibm import common, workitems
from .infra import oslc
from .infra.document import BRT, document, local_datetime, one_line, to_ewm_html, to_markdown
from .infra.http import AlmHttpError, get_session, reportable
from .server import tool

PROJECT_AREA = "/ccm/process/project-areas/{pa}"
TEAM_AREA = "/ccm/process/project-areas/{pa}/team-areas/{team}"
REPORTABLE_PLANS = "/ccm/rpt/repository/apt"
# serviço interno usado pela UI web (IPlanProcessRestService.postCreateIteration); não há API pública
CREATE_ITERATION = "/ccm/service/com.ibm.team.apt.internal.service.rest.IPlanProcessRestService/createIteration"
# "sem tipo" da UI web; sem iterationTypeItemId o servidor responde NullPointerException
NO_ITERATION_TYPE = "com.ibm.team.apt.web.ui.internal.iteration.type.none"
# serviço interno usado pela UI web ao salvar um plano novo (IPlanRestService.putItems)
PUT_PLAN = "/ccm/service/com.ibm.team.apt.internal.service.rest.IPlanRestService/putItems"
# tipos de plano suportados (id do planType -> nome amigável). Só estes dois são aceitos na criação;
# o servidor pode normalizar outros ids conforme a configuração de processo, então restringimos aqui
PLAN_TYPES = {
    "com.ibm.team.apt.plantype.kanbanBoard": "Quadro de tarefas Kanban",
    "com.ibm.team.apt.plantype.product.backlog": "Backlog do Produto",
}
# token do guard da UI web para o IPlanRestService; o servidor devolve o atual no WebServiceUsageException
PLAN_GUARD_TOKEN = "_eugWIJstEfGZGNAHniZkPA"
GUARD_ERROR = "com.ibm.team.rtc.common.internal.service.web.guard.WebServiceUsageException"
# prefixos de tipo de item nos handles da UI ("39;<uuid>")
PLAN_RECORD, PLAN_TYPE, ITERATION_ITEM, TEAM_AREA_ITEM, PROJECT_AREA_ITEM = 16, 8, 35, 39, 40
PLAN_FIELDS = "apt/iterationPlanRecord[contextId={pa}]/(name|itemId|archived|owner/itemId|iteration/itemId)"
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
# durações em ms, mostradas como '4h' / '1h30'
DURATIONS = {"rtc_cm:estimate", "rtc_cm:correctedEstimate", "rtc_cm:timeSpent"}
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


def _is_link(prop: dict) -> bool:
    """Tipo de link (pai, filhos, requisito, teste, commit...), não campo."""
    predicate = prop["predicate"]
    return ".linktype." in predicate.lower() or (
        predicate.startswith(("oslc_cm:", "calm:")) and prop["value_type"] not in VALUE_KINDS)


def _kind(prop: dict) -> str:
    if prop["predicate"] in KINDS:
        return KINDS[prop["predicate"]]
    if any("/oslc/enumerations/" in r for r in prop["range"]):
        return "enumeration"
    return VALUE_KINDS.get(prop["value_type"], "resource")


@tool
def ccm_list_team_areas(project_area_identifier: str) -> list[dict]:
    """Times (team areas) da project area em lista plana: [{name, identifier, parent}] (parent = time pai ou None).
    alm.json: team-areas {name: {identifier, categories}}."""
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
            if not p["read_only"] and p["predicate"] not in PARAMETER_FIELDS and not _is_link(p)]


@tool
def ccm_list_link_types(project_area_identifier: str, workitem_type: str) -> list[dict]:
    """Tipos de link do tipo de work item (pai, filhos, relacionados, requisitos, testes, commits...):
    [{name, attribute}]. alm.json: ccm.link-types {name: attribute} (só os que o projeto usa; nomes à escolha)."""
    return [{"name": p["title"], "attribute": p["predicate"]}
            for p in _props(project_area_identifier, workitem_type) if _is_link(p)]


@tool
def ccm_list_iterations(project_area_identifier: str) -> list[dict]:
    """Iterações de todas as timelines, em lista plana: [{name, identifier, start-date?, end-date?, parent?}].
    Nomes repetidos viram caminho ('2026/Sprint 01'). alm.json: iterations {name: {identifier, plans}}."""
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


def _ui_post(path: str, data: dict, guarded: bool = False) -> None:
    """POST num serviço interno da UI web (form, header anti-CSRF com o JSESSIONID do /ccm). `guarded`: manda o
    token `_t` e, se o servidor recusar com um token novo, repete uma vez com ele. Chame depois de uma leitura no /ccm
    (garante o login e o cookie)."""
    session = get_session()
    jsession = next((c.value for c in session.session.cookies if c.name == "JSESSIONID" and c.path.startswith("/ccm")),
                    None)
    headers = {"Accept": "text/json", "X-Jazz-CSRF-Prevent": jsession or ""}
    token = PLAN_GUARD_TOKEN if guarded else None
    for _ in range(2):
        resp = session.request("POST", path, data={**data, **({"_t": token} if token else {})}, headers=headers,
                               ok=tuple(range(200, 600)))
        if resp.ok:
            return
        try:
            error = resp.json()
        except ValueError:
            break
        token = (error.get("errorData") or {}).get("token") if error.get("errorClass") == GUARD_ERROR else None
        if not token:
            break
    raise AlmHttpError(resp)


def _epoch_ms(day: str, end: bool = False) -> int:
    """'2026-10-01' -> início (ou fim, 23:59) do dia em Brasília, em ms (formato da UI web)."""
    moment = datetime.combine(date.fromisoformat(day), time(23, 59) if end else time(0), BRT)
    return int(moment.timestamp() * 1000)


@tool
def ccm_create_iteration(
    project_area_identifier: str, parent: str, name: str, start_date: str, end_date: str | None = None,
    iteration_id: str | None = None, iteration_type: str | None = None,
) -> dict:
    """Cria uma iteração filha de `parent` (identifier de ccm_list_iterations), como o diálogo 'Create Iteration'
    da UI web. Datas 'AAAA-MM-DD' (Brasília). `iteration_id`: id interno (padrão: o nome). `iteration_type`: itemId
    do tipo de iteração (padrão: sem tipo). Exige a permissão 'Modify structures of iterations'. Retorna {name, identifier, start-date, end-date?, parent}."""
    before = {i["identifier"] for i in ccm_list_iterations(project_area_identifier)}  # também autentica o /ccm
    if parent not in before:
        raise LookupError(f"Iteração pai '{parent}' não existe em {project_area_identifier}.")
    payload = {"id": iteration_id or name, "name": name, "startDateTime": _epoch_ms(start_date),
               "hasDeliverable": True, "parentIterationId": parent,
               "iterationTypeItemId": iteration_type or NO_ITERATION_TYPE,
               **({"endDateTime": _epoch_ms(end_date, end=True)} if end_date else {})}
    _ui_post(CREATE_ITERATION, {"jsonObject": json.dumps(payload)})
    created = [i for i in ccm_list_iterations(project_area_identifier) if i["identifier"] not in before]
    if not created:
        raise LookupError(f"O servidor aceitou, mas a iteração '{name}' não apareceu sob '{parent}'.")
    return created[0]


@tool
def ccm_list_iteration_plans(project_area_identifier: str, iteration_identifiers: list[str] | None = None) -> list[dict]:
    """Planos de iteração não arquivados, opcionalmente só das iterações informadas:
    [{name, identifier, team-area (time dono; None se o dono é a project area), iteration}]. alm.json:
    iterations[nome da iteração].plans {name: {identifier, team-area}} (sem team-area quando None)."""
    pa, wanted = project_area_identifier, set(iteration_identifiers or [])
    plans = [{"name": r.findtext("name"), "identifier": r.findtext("itemId"),
              "team-area": None if r.findtext("owner/itemId") == pa else r.findtext("owner/itemId"),
              "iteration": r.findtext("iteration/itemId")}
             for r in reportable(REPORTABLE_PLANS, PLAN_FIELDS.format(pa=pa), "iterationPlanRecord")
             if r.findtext("archived") != "true"]
    return [p for p in plans if not wanted or p["iteration"] in wanted]


@tool
def ccm_create_iteration_plan(
    project_area_identifier: str, name: str, iteration: str, plan_type: str, team_area: str | None = None,
) -> dict:
    """Cria um plano de iteração, como 'Create Plan' da UI web. `iteration`: identifier de ccm_list_iterations.
    `plan_type`: id do tipo de plano; só 'com.ibm.team.apt.plantype.kanbanBoard' (Quadro de tarefas Kanban) e
    'com.ibm.team.apt.plantype.product.backlog' (Backlog do Produto) são aceitos. `team_area`: identifier do time
    dono (ccm_list_team_areas); padrão: a project area. Retorna {name, identifier, team-area, iteration}."""
    pa = project_area_identifier
    if plan_type not in PLAN_TYPES:
        opcoes = ", ".join(f"{i} ({n})" for i, n in PLAN_TYPES.items())
        raise ValueError(f"plan_type '{plan_type}' não é suportado. Use um destes: {opcoes}.")
    before = {p["identifier"] for p in ccm_list_iteration_plans(pa)}  # também autentica o /ccm
    owner = team_area or pa
    owner_item = PROJECT_AREA_ITEM if owner == pa else TEAM_AREA_ITEM
    record = {"itemId": "__new_1", "itemType": "item:com.ibm.team.apt:IterationPlanRecord",
              "planType": f"{PLAN_TYPE};{pa}/{plan_type}", "projectArea": f"{PROJECT_AREA_ITEM};{pa}",
              "label": name, "alwaysLoadAllExecutionItems": False, "fetchChildrenOnDemand": True,
              "teamArea": f"{owner_item};{owner}", "iteration": f"{ITERATION_ITEM};{iteration}", "rankingMode": "explicit"}
    _ui_post(PUT_PLAN, {"h": f"{PLAN_RECORD};__new_1", "json": json.dumps(record)}, guarded=True)
    created = [p for p in ccm_list_iteration_plans(pa) if p["identifier"] not in before]
    if not created:
        raise LookupError(f"O servidor aceitou, mas o plano '{name}' não apareceu em {pa}.")
    return created[0]


# --- alm-ccm

def _first(resource: dict, predicate: str) -> dict | None:
    return next(iter(resource["links"].get(predicate, [])), None)


def _project_area(resource: dict) -> str:
    """Id da project area do work item ('' se o recurso não informar)."""
    link = _first(resource, "rtc_cm:projectArea") or _first(resource, "process:projectArea")
    return (oslc.item_id(link["url"]) or "") if link else ""


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
    informe ao menos iteration, team_areas ou owner. Plano do alm.json: iteration=iterations[it].identifier e
    team_areas=[iterations[it].plans[nome].team-area] (plano sem team-area: omita). team_areas inclui os subtimes.
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


def _duration(ms) -> str:
    hours, minutes = divmod(int(ms) // 60000, 60)
    return f"{hours}h" + (f"{minutes:02d}" if minutes else "")


def _label(link: dict) -> str | None:
    """Link -> texto: título (nome da pessoa, do estado, 'id: resumo', mensagem do commit) ou URL.
    None para o usuário 'unassigned' (campo sem pessoa)."""
    if link["url"].endswith("/jts/users/unassigned"):
        return None
    return one_line(link.get("title")) or link["url"]


@tool
def ccm_get_workitem(workitem_id: str, fields: dict, link_types: dict | None = None) -> str:
    """Work item em Markdown + YAML. Cabeçalho: id, type, title, state, url, creator, created, modified, closed,
    attributes {nome do alm.json: valor} e links {nome do alm.json: ['id: título', ...]}. Corpo: descrição em
    Markdown e comentários (outro WI citado no texto, 'Tarefa 479977', fica como texto e aparece no link
    'Menções' quando mapeado). Só entram os campos e links informados: fields=workitem-types[tipo].fields e
    link_types=link-types do alm.json. Pessoas vêm pelo nome (login: members do alm.json); durações como '4h'
    (na gravação, em ms). Para gravar, use a chave do alm.json (fields[nome]) em ccm_update_workitem."""
    wi = workitems.get_workitem(workitem_id, fetch_all=True)
    props, links = wi["properties"], wi["links"]

    def value(attribute: str):
        key = oslc.qname(oslc.expand(attribute))
        if key in links:
            values = [label for link in links[key] if (label := _label(link))]
            return (values[0] if len(values) == 1 else values) or None
        v = props.get(key)
        if key in DURATIONS:  # o EWM usa -1 para "sem estimativa"
            return _duration(v) if isinstance(v, int) and v > 0 else None
        return v

    def related(attribute: str) -> list[str]:
        return list(dict.fromkeys(label for link in links.get(oslc.qname(oslc.expand(attribute)), [])
                                  if (label := _label(link))))

    body = to_markdown(oslc.markup(wi["url"], "dcterms:description"))
    comments = []
    for c in sorted(wi.get("comments", []), key=lambda c: str(c["properties"].get("dcterms:created"))):
        author = next((link["url"].rsplit("/", 1)[-1] for link in c["links"].get("dcterms:creator", [])), "?")
        created = local_datetime(c["properties"].get("dcterms:created"))
        comments.append(f"**{author} · {created}**\n\n{c['properties'].get('dcterms:description') or ''}".strip())
    head = {"id": int(wi["id"]), "type": props.get("dcterms:type"), "title": one_line(wi["title"]),
            "state": _title(_first(wi, "rtc_cm:state")), "url": wi["url"],
            "creator": next((_label(link) for link in links.get("dcterms:creator", [])), None),
            "created": local_datetime(props.get("dcterms:created")),
            "modified": local_datetime(props.get("dcterms:modified")),
            "closed": local_datetime(props.get("oslc_cm:closeDate")),
            "attributes": {name: v for name, attribute in fields.items()
                           if (v := value(attribute)) not in (None, "", [])},
            "links": {name: v for name, attribute in (link_types or {}).items() if (v := related(attribute))}}
    return document(head, (body or "*(sem descrição)*") + "\n\n## Comentários\n\n"
                    + ("\n\n".join(comments) or "*(nenhum)*"))


@tool
def ccm_create_workitem(
    project_area_identifier: str, workitem_type: str, summary: str, description: str | None = None,
    fields: dict | None = None, parent: str | None = None,
) -> dict:
    """Cria um work item. `description`: Markdown (vira o texto formatado do EWM; 'Tarefa 123' no texto faz o
    EWM criar o link de menção). `fields`: {chave do alm.json (fields[nome]): identifier de ccm_list_field_values
    ou valor}. `parent`: id do work item pai. Retorna {id, title, type, state, owner, iteration, url}."""
    attributes = {"dcterms:title": summary,
                  **({"dcterms:description": to_ewm_html(description)} if description else {}),
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
def ccm_update_workitem(
    workitem_id: str, fields: dict | None = None, state: str | None = None, description: str | None = None,
) -> dict:
    """Atualiza campos ({chave do alm.json: identifier ou valor}), a descrição (Markdown; substitui a descrição
    inteira) e/ou muda o estado pelo nome ('Pronto'). Retorna {id, title, type, state, owner, iteration, url}."""
    if not fields and not state and description is None:
        raise ValueError("Informe fields, description e/ou state.")
    if description is not None:
        fields = {**(fields or {}), "dcterms:description": to_ewm_html(description)}
    url = workitems.workitem_url(workitem_id)
    resource = oslc.get(url)
    pa = _project_area(resource)
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
