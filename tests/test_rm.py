import pytest

from mcp_alm import rm
from mcp_alm.ibm import requirements

from conftest import SERVER, fixture, xml_ok
from test_requirements import COMP, RDF, ROUTES, SHAPE, STREAM

C, S, T, F = "_C1", "_S1", "_T1", "FR_1"  # identifiers que a skill grava no alm.json

USERS = """<foundation><contributor><itemId>_U1</itemId><userId>joao</userId><name>João Silva</name>
<emailAddress>j@x</emailAddress><archived>false</archived></contributor></foundation>"""


@pytest.fixture
def srv(fake):
    fake.routes = dict(ROUTES)
    fake.routes[("GET", SHAPE)] = (200, {}, f'''<rdf:RDF {RDF}><oslc:ResourceShape rdf:about="{SHAPE}">
        <dcterms:title>Requisito</dcterms:title></oslc:ResourceShape></rdf:RDF>''')
    return fake


def test_get_configuration(srv):
    assert rm.rm_get_configuration("_PA1") == {"component": C, "configuration": S}


def test_list_members(srv):
    srv.routes[("GET", f"{SERVER}/rm/process/project-areas/_PA1/members")] = (200, {}, fixture("team_members.xml"))
    srv.routes[("GET", f"{SERVER}/ccm/rpt/repository/foundation")] = xml_ok(USERS)
    assert rm.rm_list_members("_PA1") == [{"identifier": "joao", "name": "João Silva"}]


def test_list_folders_as_paths(srv, monkeypatch):
    f = f"{SERVER}/rm/folders"
    monkeypatch.setattr(requirements, "list_rm_component_folders", lambda component, configuration: [
        {"url": f"{f}/R", "title": "root", "parent": None},
        {"url": f"{f}/A", "title": "01-Requisitos", "parent": f"{f}/R"},
        {"url": f"{f}/B", "title": "Funcionais", "parent": f"{f}/A"}])
    assert rm.rm_list_folders("_PA1", C, S) == [
        {"name": "01-Requisitos", "identifier": "A"}, {"name": "01-Requisitos/Funcionais", "identifier": "B"}]


def test_list_requirement_types(srv):
    assert rm.rm_list_requirement_types("_PA1", C, S) == [{"name": "Requisito", "identifier": T}]


from urllib.parse import parse_qs, urlsplit

from test_requirements import FACTORY, QUERY, R1

PRIO = "https://alm.test/rm/types/AT_PRIO"
ALTA = "https://alm.test/rm/types/AT_PRIO#alta"
FOLDER = f"{SERVER}/rm/folders/FR_1"


@pytest.fixture
def req_srv(srv):
    srv.routes[("GET", SHAPE)] = (200, {}, f'''<rdf:RDF {RDF}><oslc:ResourceShape rdf:about="{SHAPE}">
        <dcterms:title>Requisito</dcterms:title>
        <oslc:property><oslc:Property><dcterms:title>Prioridade</dcterms:title><oslc:name>prio</oslc:name>
          <oslc:propertyDefinition rdf:resource="{PRIO}"/><oslc:allowedValue rdf:resource="{ALTA}"/>
        </oslc:Property></oslc:property>
        <oslc:property><oslc:Property><oslc:name>semtitulo</oslc:name>
          <oslc:propertyDefinition rdf:resource="http://purl.org/dc/terms/contributor"/>
        </oslc:Property></oslc:property></oslc:ResourceShape></rdf:RDF>''')
    srv.routes[("GET", ALTA)] = (200, {}, f'''<rdf:RDF {RDF}><rdf:Description rdf:about="{ALTA}">
        <dcterms:title>Alta</dcterms:title></rdf:Description></rdf:RDF>''')
    srv.routes[("GET", R1)] = (200, {"ETag": '"1"'}, f'''<rdf:RDF {RDF} xmlns:nav="http://jazz.net/ns/rm/navigation#"
        xmlns:jazz_rm="http://jazz.net/ns/rm#"><rdf:Description rdf:about="{R1}">
        <dcterms:identifier>123</dcterms:identifier><dcterms:title>Login</dcterms:title>
        <jazz_rm:primaryText rdf:parseType="Literal"><div xmlns="http://www.w3.org/1999/xhtml">texto</div></jazz_rm:primaryText>
        <oslc:instanceShape rdf:resource="{SHAPE}"/><nav:parent rdf:resource="{FOLDER}"/>
        <rdf:type rdf:resource="http://open-services.net/ns/rm#Requirement"/>
        <j.0:PRIO xmlns:j.0="https://alm.test/rm/types/AT_" rdf:resource="{ALTA}"/>
        <dcterms:contributor rdf:resource="{SERVER}/jts/users/joao"/>
        </rdf:Description></rdf:RDF>''')
    # página de busca com os campos do oslc.select, como o DN devolve
    srv.routes[("GET", QUERY)] = (200, {}, f'''<rdf:RDF {RDF} xmlns:nav="http://jazz.net/ns/rm/navigation#">
        <rdf:Description rdf:about="{QUERY}"><rdfs:member><rdf:Description rdf:about="{R1}">
        <dcterms:identifier>123</dcterms:identifier><dcterms:title>Login</dcterms:title>
        <oslc:instanceShape rdf:resource="{SHAPE}"/><nav:parent rdf:resource="{FOLDER}"/>
        </rdf:Description></rdfs:member></rdf:Description></rdf:RDF>''')
    srv.routes[("GET", FOLDER)] = (200, {}, f'''<rdf:RDF {RDF}><rdf:Description rdf:about="{FOLDER}">
        <dcterms:title>01-Requisitos</dcterms:title></rdf:Description></rdf:RDF>''')
    return srv


def test_search_filters_and_summary(req_srv):
    found = rm.rm_search_requirements("_PA1", C, S, text="login", folder=F, requirement_type=T)
    assert found == [{"id": "123", "title": "Login", "type": "Requisito", "folder": "01-Requisitos", "url": R1}]
    q = parse_qs(urlsplit(next(c.url for c in req_srv.calls if c.url.startswith(QUERY + "?"))).query)
    assert q["oslc.where"] == [f"nav:parent=<{FOLDER}> and oslc:instanceShape=<{SHAPE}>"]
    assert q["oslc.searchTerms"] == ['"login"']


def test_get_requirement_attributes_by_name(req_srv):
    r = rm.rm_get_requirement("_PA1", C, S, "123")
    assert (r["title"], r["type"], r["folder"], r["text"]) == ("Login", "Requisito", "01-Requisitos", "texto")
    assert r["attributes"] == {"Prioridade": ["Alta"]}


def test_create_requirement_validates_attributes_before_post(req_srv):
    with pytest.raises(ValueError, match="Prioridade"):
        rm.rm_create_requirement("_PA1", C, S, T, F, "t", "x", {"Inexistente": "1"})
    with pytest.raises(ValueError, match="Alta"):
        rm.rm_create_requirement("_PA1", C, S, T, F, "t", "x", {"Prioridade": "Baixa"})
    assert not any(c.method == "POST" for c in req_srv.calls)


def test_create_requirement_with_attribute(req_srv):
    sent = {}
    req_srv.routes[("POST", FACTORY)] = (201, {"Location": R1}, b"")
    req_srv.routes[("PUT", R1)] = lambda req: (sent.update(body=req.body), (200, {}, b""))[1]
    assert rm.rm_create_requirement("_PA1", C, S, T, F, "Login", "texto",
                                    {"Prioridade": "Alta"}) == {"id": "123", "title": "Login", "url": R1}
    assert ALTA.encode() in sent["body"]


def test_update_requirement_title_text(req_srv):
    sent = {}
    req_srv.routes[("PUT", R1)] = lambda req: (sent.update(body=req.body), (200, {}, b""))[1]
    rm.rm_update_requirement("_PA1", C, S, "123", title="Novo", text="a & b")
    assert b"Novo" in sent["body"] and b"a &amp; b" in sent["body"]
    with pytest.raises(ValueError):
        rm.rm_update_requirement("_PA1", C, S, "123")


def test_search_requires_a_filter():
    with pytest.raises(ValueError, match="ao menos um filtro"):
        rm.rm_search_requirements("_PA1", C, S)
