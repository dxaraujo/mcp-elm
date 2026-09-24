import pytest

from mcp_alm.ibm import common

from conftest import SERVER, fixture, ok, xml_ok

CONTRIBUTORS = f"{SERVER}/ccm/rpt/repository/foundation"
USERS = """<foundation>
<contributor><itemId>_U1</itemId><userId>joao</userId><name>João Silva</name>
<emailAddress>joao@x</emailAddress><archived>false</archived></contributor>
<contributor><itemId>_U2</itemId><userId>joana</userId><name>Joana Souza</name>
<emailAddress>joana@x</emailAddress><archived>true</archived></contributor>
</foundation>"""
JP06 = "http://jazz.net/xmlns/prod/jazz/process/0.6/"
JP = "http://jazz.net/xmlns/prod/jazz/process/1.0/"
PA_URL = f"{SERVER}/ccm/process/project-areas/_PA1"
AREAS = f"""<jp06:project-areas xmlns:jp06="{JP06}">
<jp06:project-area jp06:name="Projeto Alfa"><jp06:url>{PA_URL}</jp06:url>
<jp06:summary>resumo</jp06:summary><jp06:description>desc</jp06:description>
<jp:configuration-management-enabled xmlns:jp="{JP}">true</jp:configuration-management-enabled></jp06:project-area>
<jp06:project-area jp06:name="Projeto Beta"><jp06:url>{SERVER}/ccm/process/project-areas/_PA2</jp06:url></jp06:project-area>
</jp06:project-areas>"""
TEAMS = f"""<jp06:team-areas xmlns:jp06="{JP06}">
<jp06:team-area jp06:name="Time A"><jp06:url>{SERVER}/ccm/process/project-areas/_PA1/team-areas/_TA</jp06:url>
<jp06:parent-url>{PA_URL}</jp06:parent-url></jp06:team-area>
<jp06:team-area jp06:name="Time A1"><jp06:url>{SERVER}/ccm/process/project-areas/_PA1/team-areas/_TA1</jp06:url>
<jp06:parent-url>{SERVER}/ccm/process/project-areas/_PA1/team-areas/_TA</jp06:parent-url></jp06:team-area>
</jp06:team-areas>"""
TIMELINES = f"""<jp:timelines xmlns:jp="{JP}"><jp:timeline jp:id="main"><jp:label>Principal</jp:label>
<jp:url>{SERVER}/ccm/process/project-areas/_PA1/timelines/_TL</jp:url>
<jp:iterations-url>{SERVER}/its</jp:iterations-url></jp:timeline></jp:timelines>"""
ITERATIONS = f"""<jp:iterations xmlns:jp="{JP}"><jp:iteration jp:id="s1"><jp:label>Sprint 1</jp:label>
<jp:url>{SERVER}/ccm/process/project-areas/_PA1/iterations/_I1</jp:url>
<jp:start-date>2026-01-01T03:00:00.000Z</jp:start-date></jp:iteration></jp:iterations>"""


@pytest.fixture
def srv(fake):
    fake.routes = {
        ("GET", CONTRIBUTORS): xml_ok(USERS),
        ("GET", f"{SERVER}/ccm/process/project-areas"): xml_ok(AREAS),
        ("GET", f"{PA_URL}/team-areas"): xml_ok(TEAMS),
        ("GET", f"{PA_URL}/timelines"): xml_ok(TIMELINES),
        ("GET", f"{SERVER}/its"): xml_ok(ITERATIONS),
    }
    return fake


def test_get_user_by_uuid_and_term(srv):
    assert common.get_user(user_uuid="_U1")["userId"] == "joao"
    assert common.get_user(search_term="silva")["userUUID"] == "_U1"
    many = common.get_user(search_term="joa")
    assert many["requires_selection"] and [u["userId"] for u in many["matching_users"]] == ["joao", "joana"]


def test_whoami_uses_login_from_config(srv):
    assert common.whoami()["name"] == "João Silva"
    srv.routes[("GET", CONTRIBUTORS)] = xml_ok("<foundation/>")
    common.contributors.cache_clear()
    with pytest.raises(LookupError, match="joao"):
        common.whoami()


def test_get_user_validation(srv):
    with pytest.raises(ValueError):
        common.get_user()
    with pytest.raises(ValueError):
        common.get_user(user_uuid="_U1", search_term="joao")
    with pytest.raises(ValueError, match="3"):
        common.get_user(search_term="jo")
    with pytest.raises(LookupError):
        common.get_user(user_uuid="_NOPE")


def test_user_url(srv):
    assert common.user_url("_U2") == f"{SERVER}/jts/users/joana"
    assert common.user_url("joao") == f"{SERVER}/jts/users/joao"


def test_list_project_areas(srv):
    areas = common.list_project_areas("CCM", search_name="alfa")
    assert [(a["name"], a["project_area_uuid"], a["cm_enabled"]) for a in areas] == [("Projeto Alfa", "_PA1", True)]
    assert [a["name"] for a in common.list_project_areas("CCM", cm_enabled=False)] == ["Projeto Beta"]
    with pytest.raises(ValueError, match="3"):
        common.list_project_areas("CCM", search_name=" a ")
    with pytest.raises(ValueError, match="GC"):
        common.list_project_areas("GC", cm_enabled=True)


def test_get_project_area_with_teams_and_timelines(srv):
    pa = common.get_project_area("CCM", project_area_uuid="_PA1", include_team_areas=True, include_timelines=True)
    assert (pa["name"], pa["summary"]) == ("Projeto Alfa", "resumo")
    assert pa["team_areas"][0]["name"] == "Time A"
    assert pa["team_areas"][0]["children"][0]["team_area_uuid"] == "_TA1"
    it = pa["timelines"][0]["iterations"][0]
    assert (pa["timelines"][0]["id"], pa["timelines"][0]["label"]) == ("main", "Principal")
    assert (it["id"], it["label"], it["iteration_uuid"]) == ("s1", "Sprint 1", "_I1")


def test_get_project_area_selection_and_validation(srv):
    many = common.get_project_area("CCM", name="Projeto")
    assert many["requires_selection"] and len(many["matching_project_areas"]) == 2
    with pytest.raises(ValueError):
        common.get_project_area("CCM")


# --- global configuration e links

WI = f"{SERVER}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42"
GC1 = f"{SERVER}/gc/configuration/1"
GC_RDF = f"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/" xmlns:oslc_config="http://open-services.net/ns/config#">
  <oslc_config:Stream rdf:about="{GC1}">
    <dcterms:title>GC Release 1</dcterms:title>
    <oslc_config:contributions rdf:resource="{GC1}/contributions"/>
  </oslc_config:Stream>
  <rdf:Description rdf:about="{GC1}/contributions">
    <oslc_config:contribution><rdf:Description>
      <oslc_config:configuration rdf:resource="{SERVER}/gc/configuration/2"/></rdf:Description></oslc_config:contribution>
    <oslc_config:contribution><rdf:Description>
      <oslc_config:configuration rdf:resource="{SERVER}/rm/cm/stream/_S1"/></rdf:Description></oslc_config:contribution>
  </rdf:Description>
</rdf:RDF>"""


def test_get_global_configuration(fake):
    fake.routes = {("GET", GC1): (200, {}, GC_RDF),
                   ("GET", f"{SERVER}/gc/configuration/2"): (404, {}, b""),
                   ("GET", f"{SERVER}/rm/cm/stream/_S1"): (404, {}, b"")}
    gc = common.get_global_configuration(1)
    assert gc["title"] == "GC Release 1"
    assert [c["url"] for c in gc["contributedConfigs"]] == [f"{SERVER}/gc/configuration/2"]
    assert [c["url"] for c in gc["localConfigs"]] == [f"{SERVER}/rm/cm/stream/_S1"]
    with pytest.raises(ValueError):
        common.get_global_configuration(0)


def test_linked_by_kind(fake):
    fake.routes = {("GET", WI): ok("workitem_42.xml")}
    assert [l["url"] for l in common.list_linked_requirements(WI)] == [f"{SERVER}/rm/resources/_R1"]
    assert [l["link_type"] for l in common.list_linked_testartifacts(WI)] == ["oslc_cm:testedByTestCase"]
    assert common.list_linked_workitems(WI) == []


@pytest.mark.parametrize("link_type,predicate", [
    ("implements", b"implementsRequirement"), ("trackedby", b"tracksRequirement")])
def test_link_workitem_and_requirement(fake, link_type, predicate):
    sent = {}
    fake.routes = {("GET", WI): ok("workitem_42.xml", ETag='"1"'),
                   ("PUT", WI): lambda req: (sent.update(body=req.body), (200, {}, b""))[1]}
    common.link_workitem_and_requirement(WI, f"{SERVER}/rm/resources/_R9", link_type)
    assert predicate in sent["body"] and b"_R9" in sent["body"]


@pytest.mark.parametrize("url,predicate", [
    (f"{SERVER}/qm/resource/itemName/com.ibm.rqm.planning.VersionedTestCase/5", b"relatedTestCase"),
    (f"{SERVER}/qm/resource/itemName/com.ibm.rqm.planning.VersionedTestPlan/5", b"relatedTestPlan"),
    (f"{SERVER}/qm/resource/itemName/com.ibm.rqm.execution.TestcaseExecutionRecord/5", b"relatedTestExecutionRecord")])
def test_link_workitem_and_testartifact_related_by_type(fake, url, predicate):
    sent = {}
    fake.routes = {("GET", WI): ok("workitem_42.xml"),
                   ("PUT", WI): lambda req: (sent.update(body=req.body), (200, {}, b""))[1]}
    common.link_workitem_and_testartifact(WI, url, "related")
    assert predicate in sent["body"]


def test_link_testartifact_gc_context_from_url(fake):
    tc = f"{SERVER}/qm/resource/itemName/com.ibm.rqm.planning.VersionedTestCase/5"
    fake.routes = {("GET", tc): ok("workitem_42.xml"), ("PUT", tc): (200, {}, b"")}
    common.link_testartifact_and_requirement(
        tc, f"{SERVER}/rm/resources/_R1?oslc_config.context={SERVER}/gc/configuration/1")
    assert fake.calls[0].headers["Configuration-Context"] == f"{SERVER}/gc/configuration/1"


LINKS = f"""<jp:links xmlns:jp="{JP}"><jp:link jp:projectArea="{PA_URL}"><jp:link-type>implements</jp:link-type>
<oslc:ServiceProvider xmlns:oslc="http://open-services.net/xmlns/discovery/1.0/">
<dc:title xmlns:dc="http://purl.org/dc/terms/">Req Alfa</dc:title>
<oslc:details xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" rdf:resource="{SERVER}/rm/process/project-areas/_R1"/>
</oslc:ServiceProvider></jp:link></jp:links>"""


def test_get_project_area_associations(srv):
    srv.routes[("GET", f"{PA_URL}/links")] = xml_ok(LINKS)
    pa = common.get_project_area("CCM", project_area_uuid="_PA1", include_associations=True)
    assert pa["associations"] == {"rm": [{"project_area_name": "Req Alfa", "project_area_uuid": "_R1",
                                          "link_type": "implements"}]}


def test_team_areas_flat_and_members(srv):
    teams = common.team_areas(PA_URL)
    assert [(t["name"], t["parent"]) for t in teams] == [("Time A", None), ("Time A1", "_TA")]
    srv.routes[("GET", f"{PA_URL}/members")] = (200, {}, fixture("team_members.xml"))
    assert common.area_members(PA_URL) == ["joao"]
