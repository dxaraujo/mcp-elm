import json
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from mcp_alm.ibm import workitems

from conftest import SERVER, ok, xml_ok

WI = f"{SERVER}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42"
SHAPE = f"{SERVER}/ccm/oslc/context/_PA1/shapes/workitems/defect"
ROUTES = {
    ("GET", f"{SERVER}/ccm/rootservices"): ok("rootservices_ccm.xml"),
    ("GET", f"{SERVER}/ccm/oslc/workitems/catalog"): ok("catalog_ccm.xml"),
    ("GET", f"{SERVER}/ccm/oslc/contexts/_PA1/workitems/services.xml"): ok("sp_ccm.xml"),
    ("GET", SHAPE): ok("shape_defect.xml"),
    ("GET", f"{SERVER}/ccm/oslc/enumerations/_PA1/priority"): ok("enum_priority.xml"),
    ("GET", WI): ok("workitem_42.xml"),
}
CATEGORIES = """<workitem><category><itemId>_C1</itemId><name>Backend</name><archived>false</archived></category>
<category><itemId>_C2</itemId><name>Antiga</name><archived>true</archived></category></workitem>"""


@pytest.fixture
def srv(fake):
    fake.routes = dict(ROUTES)
    return fake


def test_get_workitem_minimal_and_validation(srv):
    wi = workitems.get_workitem(workitem_id="42")
    assert (wi["id"], wi["title"]) == ("42", "Corrigir login")
    assert "rtc_cm:filedAgainst" not in wi["links"]  # fora do conjunto mínimo
    assert wi["properties"]["dcterms:description"] == "Falha ao logar & sair"
    with pytest.raises(ValueError):
        workitems.get_workitem()
    with pytest.raises(ValueError):
        workitems.get_workitem(workitem_id="abc")


def test_get_workitem_link_titles(srv):
    body = f"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dcterms="http://purl.org/dc/terms/"
      xmlns:rtc_cm="http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/">
      <rdf:Description rdf:about="{WI}?oslc.properties=*%7Bdcterms:title%7D"><dcterms:identifier>42</dcterms:identifier>
        <rtc_cm:state rdf:resource="{SERVER}/ccm/oslc/workflows/_PA1/states/wf/wf.state.s1"/>
        <dcterms:contributor rdf:resource="{SERVER}/jts/users/joao"/></rdf:Description>
      <rdf:Description rdf:about="{SERVER}/ccm/oslc/workflows/_PA1/states/wf/wf.state.s1">
        <dcterms:title>Novo</dcterms:title></rdf:Description></rdf:RDF>"""
    srv.routes[("GET", WI)] = (200, {"Content-Type": "application/rdf+xml"}, body)
    srv.routes[("GET", f"{SERVER}/ccm/rpt/repository/foundation")] = xml_ok(
        "<foundation><contributor><itemId>_U1</itemId><userId>joao</userId><name>João Silva</name>"
        "<archived>false</archived></contributor></foundation>")
    wi = workitems.get_workitem(workitem_id="42")
    assert "oslc.properties" in srv.calls[0].url and wi["url"] == WI
    assert wi["links"]["rtc_cm:state"][0]["title"] == "Novo"
    assert wi["links"]["dcterms:contributor"][0]["title"] == "João Silva"


def test_get_workitem_fetch_all_keeps_links(srv):
    wi = workitems.get_workitem(workitem_oslc_url=WI, fetch_all=True)
    assert "rtc_cm:filedAgainst" in wi["links"] and wi["comments"] == []


def test_schema_attributes_enumerations_create_metadata(srv):
    schema = workitems.get_workitem_schema("_PA1", "defect", ["attributes", "enumerations", "createMetadata"])
    t = schema["workItemTypes"][0]
    assert (schema["projectAreaId"], t["id"]) == ("_PA1", "defect")
    assert {a["id"] for a in t["attributes"]} == {"filedAgainst", "target", "priority"}
    assert [v["id"] for v in t["enumerations"]["priority"]] == ["priority.literal.l1", "priority.literal.l2"]
    assert t["createMetadata"]["requiredProperties"] == ["filedAgainst"]


def test_schema_validation(srv):
    with pytest.raises(ValueError, match="approvals"):
        workitems.get_workitem_schema("_PA1", "defect", ["approvals"])
    with pytest.raises(ValueError, match="workitem_type"):
        workitems.get_workitem_schema("_PA1", include=["createMetadata"])
    with pytest.raises(LookupError, match="story"):
        workitems.get_workitem_schema("_PA1", "story")


def test_categories_archived_and_paging(fake):
    fake.routes = {("GET", f"{SERVER}/ccm/rpt/repository/workitem"): xml_ok(CATEGORIES)}
    assert [c["name"] for c in workitems.list_workitem_categories("_PA1")] == ["Backend"]
    both = workitems.list_workitem_categories("_PA1", include_archived=True, limit=1, offset=1)
    assert [c["itemId"] for c in both] == ["_C2"]
    assert "category[projectArea/itemId=_PA1]" in unquote(fake.calls[0].url)
    with pytest.raises(ValueError, match="500"):
        workitems.list_workitem_categories("_PA1", limit=501)


# --- busca, criação e comentários

QUERY = f"{SERVER}/ccm/oslc/contexts/_PA1/workitems"
FACTORY = f"{SERVER}/ccm/oslc/contexts/_PA1/workitems/defect"
EMPTY = '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'


def _q(srv):
    return parse_qs(urlsplit(next(c.url for c in srv.calls if c.url.startswith(QUERY + "?"))).query)


def test_search_translates_filter(srv):
    srv.routes[("GET", QUERY)] = (200, {}, EMPTY)
    f = {"operator": "AND", "attributeExpressions": [
        {"attributeId": "summary", "operator": "contains", "values": ["login"]},
        {"attributeId": "internalPriority", "operator": "is", "values": ["priority.literal.l11"]},
        {"attributeId": "category", "operator": "in", "values": ["_C1", "_C2"]},
        {"attributeId": "modified", "operator": "after", "values": ["2026-01-01T00:00:00Z"]}]}
    workitems.search_workitems("_PA1", json.dumps(f), attributes="id,summary")
    q = _q(srv)
    assert q["oslc.searchTerms"] == ['"login"']
    assert q["oslc.select"] == ["dcterms:identifier{dcterms:title},dcterms:title{dcterms:title}"]
    assert q["oslc.where"] == [
        f"oslc_cmx:priority=<{SERVER}/ccm/oslc/enumerations/_PA1/priority/priority.literal.l11> and "
        f"rtc_cm:filedAgainst in [<{SERVER}/ccm/resource/itemOid/com.ibm.team.workitem.Category/_C1>,"
        f"<{SERVER}/ccm/resource/itemOid/com.ibm.team.workitem.Category/_C2>] and "
        'dcterms:modified>"2026-01-01T00:00:00Z"^^xsd:dateTime']


@pytest.mark.parametrize("f,msg", [
    ({"operator": "OR", "attributeExpressions": [{"attributeId": "id", "operator": "is", "values": ["1"]},
                                                  {"attributeId": "id", "operator": "is", "values": ["2"]}]}, "OR"),
    ({"attributeExpressions": [], "termExpressions": [{"x": 1}]}, "termExpressions"),
    ({"attributeExpressions": [{"attributeId": "owner", "operator": "contains", "values": ["x"]}]}, "contains"),
    ({"attributeExpressions": [{"attributeId": "id", "operator": "startsWith", "values": ["1"]}]}, "startsWith"),
])
def test_search_rejects_unsupported(srv, f, msg):
    with pytest.raises(ValueError, match=msg):
        workitems.search_workitems("_PA1", json.dumps(f))
    assert not any(c.url.startswith(QUERY + "?") for c in srv.calls)


def test_search_invalid_json(srv):
    with pytest.raises(ValueError, match="JSON"):
        workitems.search_workitems("_PA1", "{nope")


def test_create_workitem_payload(srv):
    sent = {}
    srv.routes[("POST", FACTORY)] = lambda req: (sent.update(body=req.body), (201, {"Location": WI}, b""))[1]
    attrs = {"summary": "Falha no login", "category": "_C1", "internalPriority": "priority.literal.l2"}
    wi = workitems.create_workitem("_PA1", "defect", json.dumps(attrs))
    assert wi["id"] == "42"
    body = sent["body"].decode()
    assert "Falha no login" in body and "Category/_C1" in body and "priority.literal.l2" in body
    assert "/ccm/oslc/types/_PA1/defect" in body


def test_create_workitem_unknown_link_endpoint(srv):
    links = [{"endpointId": "parent", "targetWorkItemId": "7"}]
    with pytest.raises(ValueError, match="parent"):
        workitems.create_workitem("_PA1", "defect", json.dumps({"summary": "x"}), json.dumps(links))
    assert not any(c.method == "POST" for c in srv.calls)


def test_add_comment_with_mentions(srv):
    comments = f"{WI}/rtc_cm:comments"
    srv.routes[("GET", WI)] = (200, {}, ok("workitem_42.xml")[2].replace(
        b"</oslc_cm:ChangeRequest>",
        f'<oslc:discussedBy xmlns:oslc="http://open-services.net/ns/core#" rdf:resource="{comments}"/>'
        f"</oslc_cm:ChangeRequest>".encode()))
    sent = {}
    srv.routes[("POST", f"{comments}/oslc:comment")] = lambda req: (
        sent.update(body=req.body), (201, {"Location": f"{comments}/0"}, b""))[1]
    srv.routes[("GET", f"{comments}/0")] = (200, {}, EMPTY)
    workitems.add_comment_to_workitem("42", "pronto", ["bob", "sal"])
    assert b"@bob @sal pronto" in sent["body"]


def test_search_state_and_type_use_literal_ids(srv):
    srv.routes[("GET", QUERY)] = (200, {}, EMPTY)
    f = {"attributeExpressions": [
        {"attributeId": "internalState", "operator": "is", "values": ["com.ibm.team.workitem.taskWorkflow.state.s3"]},
        {"attributeId": "workItemType", "operator": "is", "values": ["task"]}]}
    workitems.search_workitems("_PA1", json.dumps(f))
    assert _q(srv)["oslc.where"] == ['rtc_cm:state="com.ibm.team.workitem.taskWorkflow.state.s3" and dcterms:type="task"']


# --- revisão final

@pytest.mark.parametrize("f,msg", [
    ({}, "attributeExpressions"),
    ({"attributeExpression": [{"attributeId": "id", "operator": "is", "values": ["1"]}]}, "attributeExpression"),
    ({"operator": "NOT", "attributeExpressions": [{"attributeId": "id", "operator": "is", "values": ["1"]}]}, "NOT"),
    ({"attributeExpressions": [{"attributeId": "category", "operator": "in", "values": []}]}, "valor"),
    ([], "objeto"),
    ({"attributeExpressions": ["x"]}, "objeto"),
])
def test_search_rejects_bad_filter_shapes(srv, f, msg):
    with pytest.raises(ValueError, match=msg):
        workitems.search_workitems("_PA1", json.dumps(f))
    assert not any(c.url.startswith(QUERY + "?") for c in srv.calls)


def test_search_string_values_is_one_value(srv):
    srv.routes[("GET", QUERY)] = (200, {}, EMPTY)
    f = {"attributeExpressions": [{"attributeId": "workItemType", "operator": "is", "values": "task"}]}
    workitems.search_workitems("_PA1", json.dumps(f))
    assert _q(srv)["oslc.where"] == ['dcterms:type="task"']


@pytest.mark.parametrize("attrs,links", [("[]", None), ('{"summary": "x"}', '{"endpointId": "parent"}')])
def test_create_workitem_rejects_bad_json_shapes(srv, attrs, links):
    with pytest.raises(ValueError, match="attributes|links"):
        workitems.create_workitem("_PA1", "defect", attrs, links)


def test_workflow_from_states_url(srv):
    states = f"{SERVER}/ccm/oslc/workflows/_PA1/states/wf"
    srv.routes[("GET", states)] = ok("wf_states.xml")
    srv.routes[("GET", f"{SERVER}/ccm/oslc/workflows/_PA1/actions/wf")] = ok("wf_actions.xml")
    wf = workitems.workflow(states)
    assert [s["title"] for s in wf["states"]] == ["Novo", "Em Testes", "Pronto"]
    assert {(a["title"], a["resultState"]) for a in wf["actions"]} == {
        ("Concluir", "Pronto"), ("Testar", "Em Testes"), ("Disponibilizar", "Em Testes")}


def test_description_is_xml_literal_only_when_well_formed(srv):
    from rdflib.namespace import RDF
    assert workitems.node("_PA1", "dcterms:description", "a &amp; <b>b</b>").datatype == RDF.XMLLiteral
    assert workitems.node("_PA1", "dcterms:description", "prazo < 5 & tal").datatype is None
