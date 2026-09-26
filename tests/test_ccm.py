import pytest

from mcp_alm import ccm

from conftest import SERVER, fixture, ok, xml_ok

JP06 = "http://jazz.net/xmlns/prod/jazz/process/0.6/"
JP = "http://jazz.net/xmlns/prod/jazz/process/1.0/"
PA_URL = f"{SERVER}/ccm/process/project-areas/_PA1"
TEAMS = f"""<jp06:team-areas xmlns:jp06="{JP06}">
<jp06:team-area jp06:name="Time A"><jp06:url>{PA_URL}/team-areas/_TA</jp06:url><jp06:parent-url>{PA_URL}</jp06:parent-url></jp06:team-area>
<jp06:team-area jp06:name="Time B"><jp06:url>{PA_URL}/team-areas/_TB</jp06:url><jp06:parent-url>{PA_URL}</jp06:parent-url></jp06:team-area>
<jp06:team-area jp06:name="Time A1"><jp06:url>{PA_URL}/team-areas/_TA1</jp06:url><jp06:parent-url>{PA_URL}/team-areas/_TA</jp06:parent-url></jp06:team-area>
</jp06:team-areas>"""
USERS = """<foundation><contributor><itemId>_U1</itemId><userId>joao</userId><name>João Silva</name>
<emailAddress>j@x</emailAddress><archived>false</archived></contributor>
<contributor><itemId>_U2</itemId><userId>ana</userId><name>Ana Lima</name>
<emailAddress>a@x</emailAddress><archived>false</archived></contributor></foundation>"""


def members(*logins):
    return xml_ok(f'<jp06:members xmlns:jp06="{JP06}">' + "".join(
        f"<jp06:member><jp06:user-url>{SERVER}/jts/users/{l}</jp06:user-url></jp06:member>" for l in logins)
        + "</jp06:members>")


def timeline(its):
    return xml_ok(f'<jp:iterations xmlns:jp="{JP}">' + "".join(
        f'<jp:iteration jp:id="{i}"><jp:label>{label}</jp:label><jp:url>{PA_URL}/iterations/{i}</jp:url>'
        + (f"<jp:start-date>{start}</jp:start-date>" if start else "")
        + (f"<jp:childern-iterations-url>{SERVER}/its/{i}</jp:childern-iterations-url>" if kids else "")
        + "</jp:iteration>" for i, label, start, kids in its) + "</jp:iterations>")


@pytest.fixture
def srv(fake):
    fake.routes = {
        ("GET", f"{SERVER}/ccm/rootservices"): ok("rootservices_ccm.xml"),
        ("GET", f"{SERVER}/ccm/oslc/workitems/catalog"): ok("catalog_ccm.xml"),
        ("GET", f"{SERVER}/ccm/oslc/contexts/_PA1/workitems/services.xml"): ok("sp_ccm.xml"),
        ("GET", f"{SERVER}/ccm/oslc/context/_PA1/shapes/workitems/defect"): ok("shape_defect.xml"),
        ("GET", f"{SERVER}/ccm/oslc/enumerations/_PA1/priority"): ok("enum_priority.xml"),
        ("GET", f"{PA_URL}/team-areas"): xml_ok(TEAMS),
        ("GET", f"{SERVER}/ccm/rpt/repository/foundation"): xml_ok(USERS),
        ("GET", f"{PA_URL}/team-areas/_TA/members"): members("joao", "ana"),
        ("GET", f"{PA_URL}/team-areas/_TB/members"): members("joao"),
        ("GET", f"{PA_URL}/timelines"): xml_ok(f'<jp:timelines xmlns:jp="{JP}"><jp:timeline jp:id="t">'
                                               f"<jp:label>T</jp:label><jp:iterations-url>{SERVER}/its/root"
                                               "</jp:iterations-url></jp:timeline></jp:timelines>"),
        ("GET", f"{SERVER}/its/root"): timeline([("_Y25", "2025", "2025-01-01T03:00:00.000Z", True),
                                                 ("_Y26", "2026", None, True)]),
        ("GET", f"{SERVER}/its/_Y25"): timeline([("_S25", "Sprint 01", "2025-02-01T02:00:00.000Z", False)]),
        ("GET", f"{SERVER}/its/_Y26"): timeline([("_S26", "Sprint 01", None, False)]),
        # 2ª página (pos=100) vazia encerra a paginação do Reportable
        ("GET", f"{SERVER}/ccm/rpt/repository/apt"): lambda req: xml_ok("<apt/>") if "pos=" in req.url
        else ok("apt_plans.xml"),
    }
    return fake


def test_list_team_areas(srv):
    assert ccm.ccm_list_team_areas("_PA1") == [
        {"name": "Time A", "identifier": "_TA", "parent": None},
        {"name": "Time B", "identifier": "_TB", "parent": None},
        {"name": "Time A1", "identifier": "_TA1", "parent": "_TA"}]


def test_list_members_dedupes_across_teams(srv):
    assert ccm.ccm_list_members("_PA1", ["_TA", "_TB"]) == [
        {"identifier": "ana", "name": "Ana Lima", "team-areas": ["_TA"]},
        {"identifier": "joao", "name": "João Silva", "team-areas": ["_TA", "_TB"]}]
    with pytest.raises(ValueError):
        ccm.ccm_list_members("_PA1", [])


def test_workitem_types_and_fields(srv):
    assert ccm.ccm_list_workitem_types("_PA1") == [{"identifier": "defect", "name": "Defect"}]
    fields = {f["attribute"]: f for f in ccm.ccm_list_workitem_fields("_PA1", "defect")}
    assert (fields["rtc_cm:filedAgainst"]["kind"], fields["rtc_cm:filedAgainst"]["required"]) == ("category", True)
    assert fields["rtc_cm:plannedFor"]["kind"] == "iteration"
    assert (fields["oslc_cmx:priority"]["kind"], fields["oslc_cmx:priority"]["name"]) == ("enumeration", "Priority")
    with pytest.raises(LookupError, match="story"):
        ccm.ccm_list_workitem_fields("_PA1", "story")


def test_list_iterations_dates_and_duplicate_names(srv):
    assert ccm.ccm_list_iterations("_PA1") == [
        {"name": "2025", "identifier": "_Y25", "start-date": "2025-01-01"},
        {"name": "2025/Sprint 01", "identifier": "_S25", "start-date": "2025-01-31", "parent": "_Y25"},
        {"name": "2026", "identifier": "_Y26"},
        {"name": "2026/Sprint 01", "identifier": "_S26", "parent": "_Y26"}]


def test_list_iteration_plans_skips_archived_and_filters(srv):
    assert ccm.ccm_list_iteration_plans("_PA1", ["_I1"]) == [
        {"name": "Sprint 1 - Time A", "identifier": "_P1", "team-area": "_TA", "iteration": "_I1"}]
    assert {p["identifier"] for p in ccm.ccm_list_iteration_plans("_PA1")} == {"_P1", "_P2"}


def test_list_iteration_plans_team_area_none_when_owned_by_project_area(srv):
    srv.routes[("GET", f"{SERVER}/ccm/rpt/repository/apt")] = lambda r: xml_ok("<apt/>") if "pos=" in r.url \
        else xml_ok(NEW_PLAN.replace("_TA", "_PA1"))
    assert ccm.ccm_list_iteration_plans("_PA1") == [
        {"name": "Novo Plano", "identifier": "_P9", "team-area": None, "iteration": "_I1"}]


import json
from urllib.parse import parse_qs, unquote, urlsplit

QUERY = f"{SERVER}/ccm/oslc/contexts/_PA1/workitems"
WI7 = f"{SERVER}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/7"
RESULTS = f"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#" xmlns:rtc_cm="http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/">
 <rdf:Description rdf:about="{QUERY}">
  <rdfs:member><rdf:Description rdf:about="{WI7}"><dcterms:identifier>7</dcterms:identifier>
   <dcterms:title>Login</dcterms:title><dcterms:type>Tarefa</dcterms:type>
   <dcterms:contributor rdf:resource="{SERVER}/jts/users/joao"/>
   <rtc_cm:state><rdf:Description rdf:about="{SERVER}/ccm/oslc/workflows/_PA1/states/wf/wf.state.s1">
     <dcterms:title>Novo</dcterms:title></rdf:Description></rtc_cm:state>
  </rdf:Description></rdfs:member>
 </rdf:Description></rdf:RDF>"""


def _where(srv):
    return parse_qs(urlsplit(next(c.url for c in srv.calls if c.url.startswith(QUERY + "?"))).query)["oslc.where"][0]


def test_list_workitems_plan_filters(srv):
    srv.routes[("GET", QUERY)] = (200, {}, RESULTS)
    found = ccm.ccm_list_workitems("_PA1", iteration="_I1", team_areas=["_TA"], owner="joao", workitem_type="task")
    assert found == [{"id": "7", "title": "Login", "type": "Tarefa", "state": "Novo", "owner": "joao",
                      "iteration": None, "url": WI7}]
    where = unquote(_where(srv))
    assert f"rtc_cm:plannedFor=<{SERVER}/ccm/oslc/iterations/_I1>" in where
    assert f"<{SERVER}/ccm/oslc/teamareas/_TA>,<{SERVER}/ccm/oslc/teamareas/_TA1>" in where  # subtime incluído
    assert f"dcterms:contributor=<{SERVER}/jts/users/joao>" in where and 'dcterms:type="task"' in where
    assert ccm.ccm_list_workitems("_PA1", iteration="_I1", state="pronto") == []  # estado filtrado pelo nome


def test_list_workitems_requires_filter(srv):
    with pytest.raises(ValueError, match="filtro"):
        ccm.ccm_list_workitems("_PA1")
    with pytest.raises(ValueError, match="filtro"):
        ccm.ccm_list_workitems("_PA1", team_areas=["_PA1"])  # plano da própria PA sem iteração
    assert not any(c.url.startswith(QUERY + "?") for c in srv.calls)


def test_field_values_enumeration_with_default(srv):
    values = ccm.ccm_list_field_values("_PA1", "defect", "oslc_cmx:priority")
    assert values["kind"] == "enumeration" and not values["required"]
    assert [(v["name"], v["default"]) for v in values["values"]] == [("Alta", False), ("Não designado", True)]
    assert values["values"][0]["identifier"] == f"{SERVER}/ccm/oslc/enumerations/_PA1/priority/priority.literal.l1"


def test_field_values_category(srv):
    srv.routes[("GET", f"{SERVER}/ccm/rpt/repository/workitem")] = xml_ok(
        "<workitem><category><itemId>_C1</itemId><name>Backend</name><archived>false</archived></category></workitem>")
    assert ccm.ccm_list_field_values("_PA1", "defect", "rtc_cm:filedAgainst")["values"] == [
        {"identifier": "_C1", "name": "Backend", "default": False}]


def test_create_workitem_uses_alm_json_keys(srv):
    sent = {}
    factory = f"{SERVER}/ccm/oslc/contexts/_PA1/workitems/defect"
    srv.routes[("POST", factory)] = lambda req: (sent.update(body=req.body), (201, {"Location": WI7}, b""))[1]
    srv.routes[("GET", WI7)] = ok("workitem_7.xml")
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/states/wf/wf.state.s1")] = ok("wf_states.xml")
    wi = ccm.ccm_create_workitem("_PA1", "defect", "Falha", fields={"rtc_cm:filedAgainst": "_C1",
                                                                    "rtc_cm:plannedFor": "_I1"})
    assert (wi["id"], wi["state"]) == ("7", "Novo")
    body = sent["body"].decode()
    assert "Falha" in body and "Category/_C1" in body and "iterations/_I1" in body


def test_update_state_by_name_sends_action(srv):
    now = {"state": b"wf.state.s1"}
    srv.routes[("GET", WI7)] = lambda req: (200, {"ETag": '"1"'}, fixture("workitem_7.xml").replace(
        b"wf.state.s1", now["state"]))
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/states/wf/wf.state.s3")] = ok("wf_states.xml")
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/states/wf")] = ok("wf_states.xml")
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/actions/wf")] = ok("wf_actions.xml")
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/states/wf/wf.state.s1")] = ok("wf_states.xml")
    sent = {}
    srv.routes[("PUT", WI7)] = lambda req: (sent.update(url=req.url), now.update(state=b"wf.state.s3"),
                                            (200, {}, b""))[2]
    assert ccm.ccm_list_workitem_states("7")["state"] == "Novo"
    ccm.ccm_update_workitem("7", state="pronto")
    assert "_action=wf.action.a5" in sent["url"]
    with pytest.raises(ValueError, match="Pronto"):
        ccm.ccm_update_workitem("7", state="Inexistente")
    with pytest.raises(ValueError):
        ccm.ccm_update_workitem("7")


def test_fields_skip_summary_and_description(srv, monkeypatch):
    props = [{"title": "Resumo", "predicate": "dcterms:title", "required": True, "read_only": False, "range": [],
              "value_type": "xsd:string"},
             {"title": "Descrição", "predicate": "dcterms:description", "required": False, "read_only": False,
              "range": [], "value_type": "rdf:XMLLiteral"}]
    monkeypatch.setattr(ccm, "_props", lambda pa, wt: props)
    assert ccm.ccm_list_workitem_fields("_PA1", "task") == []  # já são os parâmetros summary/description


# --- revisão final

def test_update_tries_actions_to_same_state_and_verifies(srv):
    """Testar (a9) e Disponibilizar (a11) levam a 'Em Testes'; o servidor só aceita a9 a partir do estado atual."""
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/states/wf")] = ok("wf_states.xml")
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/actions/wf")] = ok("wf_actions.xml")
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/states/wf/wf.state.s2")] = ok("wf_states.xml")
    state = {"now": "wf.state.s1"}
    srv.routes[("GET", WI7)] = lambda req: (200, {"ETag": '"1"'}, fixture("workitem_7.xml").replace(
        b"wf.state.s1", state["now"].encode()))

    def put(req):
        if "_action=wf.action.a9" in req.url:
            state["now"] = "wf.state.s2"
            return 200, {}, b""
        return 409, {}, b"acao invalida"

    srv.routes[("PUT", WI7)] = put
    assert ccm.ccm_update_workitem("7", state="Em Testes")["state"] == "Em Testes"


def test_update_raises_when_state_did_not_change(srv):
    srv.routes[("GET", WI7)] = ok("workitem_7.xml", ETag='"1"')
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/states/wf")] = ok("wf_states.xml")
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/actions/wf")] = ok("wf_actions.xml")
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/states/wf/wf.state.s1")] = ok("wf_states.xml")
    srv.routes[("PUT", WI7)] = (200, {}, b"")  # servidor ignora a ação: estado continua Novo
    with pytest.raises(ValueError, match="Novo"):
        ccm.ccm_update_workitem("7", state="Pronto")


def test_iterations_repeated_root_names_get_timeline_prefix(srv):
    srv.routes[("GET", f"{PA_URL}/timelines")] = xml_ok(
        f'<jp:timelines xmlns:jp="{JP}">'
        f'<jp:timeline jp:id="a"><jp:label>Principal</jp:label><jp:iterations-url>{SERVER}/its/a</jp:iterations-url></jp:timeline>'
        f'<jp:timeline jp:id="b"><jp:label>Suporte</jp:label><jp:iterations-url>{SERVER}/its/b</jp:iterations-url></jp:timeline>'
        "</jp:timelines>")
    srv.routes[("GET", f"{SERVER}/its/a")] = timeline([("_BA", "Backlog", None, False)])
    srv.routes[("GET", f"{SERVER}/its/b")] = timeline([("_BB", "Backlog", None, False)])
    assert [i["name"] for i in ccm.ccm_list_iterations("_PA1")] == ["Principal/Backlog", "Suporte/Backlog"]


def test_local_date_with_z_suffix():
    assert ccm._local_date("2026-12-19T02:59:00.000Z") == "2026-12-18"


def test_create_iteration_posts_ui_payload_and_returns_new(srv):
    def create(req):
        srv.routes[("GET", f"{SERVER}/its/_Y26")] = timeline([("_S26", "Sprint 01", None, False),
                                                             ("_S27", "Sprint 02", "2026-10-01T03:00:00.000Z", False)])
        return 200, {"Content-Type": "text/json"}, b"{}"
    srv.routes[("POST", f"{SERVER}{ccm.CREATE_ITERATION}")] = create
    assert ccm.ccm_create_iteration("_PA1", "_Y26", "Sprint 02", "2026-10-01", "2026-10-14") == {
        "name": "Sprint 02", "identifier": "_S27", "start-date": "2026-10-01", "parent": "_Y26"}
    post = next(c for c in srv.calls if c.method == "POST")
    payload = json.loads(parse_qs(post.body)["jsonObject"][0])
    assert payload == {"id": "Sprint 02", "name": "Sprint 02", "startDateTime": 1790823600000,
                       "endDateTime": 1792033140000, "hasDeliverable": True, "parentIterationId": "_Y26",
                       "iterationTypeItemId": ccm.NO_ITERATION_TYPE}


def test_create_iteration_unknown_parent(srv):
    with pytest.raises(LookupError, match="_NAO"):
        ccm.ccm_create_iteration("_PA1", "_NAO", "Sprint 02", "2026-10-01")


NEW_PLAN = ('<apt><iterationPlanRecord><name>Novo Plano</name><archived>false</archived><itemId>_P9</itemId>'
            '<owner><itemId>_TA</itemId></owner><iteration><itemId>_I1</itemId></iteration></iterationPlanRecord></apt>')


def test_create_iteration_plan_sends_ui_record_and_retries_guard(srv):
    posts = []

    def put(req):
        posts.append(parse_qs(req.body))
        if len(posts) == 1:  # guard recusa o token embutido e devolve o atual
            return 400, {"Content-Type": "text/json"}, json.dumps(
                {"errorClass": ccm.GUARD_ERROR, "errorData": {"token": "_NOVO", "serviceName": "x"}})
        srv.routes[("GET", f"{SERVER}/ccm/rpt/repository/apt")] = lambda r: xml_ok("<apt/>") if "pos=" in r.url \
            else xml_ok(NEW_PLAN)
        return 200, {"Content-Type": "text/json"}, b"{}"
    srv.routes[("POST", f"{SERVER}{ccm.PUT_PLAN}")] = put
    assert ccm.ccm_create_iteration_plan("_PA1", "Novo Plano", "_I1", team_area="_TA") == {
        "name": "Novo Plano", "identifier": "_P9", "team-area": "_TA", "iteration": "_I1"}
    assert [p["_t"] for p in posts] == [[ccm.PLAN_GUARD_TOKEN], ["_NOVO"]]
    assert posts[1]["h"] == ["16;__new_1"]
    assert json.loads(posts[1]["json"][0]) == {
        "itemId": "__new_1", "itemType": "item:com.ibm.team.apt:IterationPlanRecord",
        "planType": "8;_PA1/com.ibm.team.apt.plantype.default", "projectArea": "40;_PA1", "label": "Novo Plano",
        "alwaysLoadAllExecutionItems": False, "fetchChildrenOnDemand": True, "teamArea": "39;_TA",
        "iteration": "35;_I1", "rankingMode": "explicit"}


def test_create_iteration_plan_team_area_defaults_to_project_area_and_raises_http_error(srv):
    srv.routes[("POST", f"{SERVER}{ccm.PUT_PLAN}")] = (403, {"Content-Type": "text/json"},
                                                       b'{"errorMessage": "Permission Denied"}')
    with pytest.raises(RuntimeError, match="Permission Denied"):
        ccm.ccm_create_iteration_plan("_PA1", "Plano", "_I1")
    post = next(c for c in srv.calls if c.method == "POST")
    assert json.loads(parse_qs(post.body)["json"][0])["teamArea"] == "40;_PA1"
