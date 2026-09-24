from urllib.parse import parse_qs, urlsplit

import pytest

from mcp_alm.ibm import requirements

from conftest import SERVER

RDF = 'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dcterms="http://purl.org/dc/terms/" ' \
      'xmlns:oslc="http://open-services.net/ns/core#" xmlns:oslc_config="http://open-services.net/ns/config#" ' \
      'xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"'
ROOT = f"{SERVER}/rm/rootservices"
CATALOG = f"{SERVER}/rm/oslc_rm/catalog"
SP = f"{SERVER}/rm/oslc_rm/_PA1/services.xml"
COMP = f"{SERVER}/rm/cm/component/_C1"
STREAM = f"{SERVER}/rm/cm/stream/_S1"
BASELINE = f"{SERVER}/rm/cm/baseline/_B1"
QUERY = f"{SERVER}/rm/views"
FACTORY = f"{SERVER}/rm/requirementFactory"
SHAPE = f"{SERVER}/rm/types/_T1"
R1 = f"{SERVER}/rm/resources/TX_1"

ROUTES = {
    ("GET", ROOT): (200, {}, f'''<rdf:Description {RDF} xmlns:oslc_rm="http://open-services.net/xmlns/rm/1.0/"
        rdf:about="{ROOT}"><oslc_rm:rmServiceProviders rdf:resource="{CATALOG}"/></rdf:Description>'''),
    ("GET", CATALOG): (200, {}, f'''<rdf:RDF {RDF}><oslc:ServiceProviderCatalog rdf:about="{CATALOG}">
        <oslc:serviceProvider><oslc:ServiceProvider rdf:about="{SP}"><dcterms:title>Req Alfa</dcterms:title>
        </oslc:ServiceProvider></oslc:serviceProvider></oslc:ServiceProviderCatalog></rdf:RDF>'''),
    ("GET", SP): (200, {}, f'''<rdf:RDF {RDF}><oslc:ServiceProvider rdf:about="{SP}"><oslc:service><oslc:Service>
        <oslc:queryCapability><oslc:QueryCapability><oslc:queryBase rdf:resource="{QUERY}"/>
          <oslc:resourceType rdf:resource="http://open-services.net/ns/rm#Requirement"/></oslc:QueryCapability>
        </oslc:queryCapability>
        <oslc:creationFactory><oslc:CreationFactory><oslc:creation rdf:resource="{FACTORY}"/>
          <oslc_config:component rdf:resource="{COMP}"/>
          <oslc:resourceType rdf:resource="http://open-services.net/ns/rm#Requirement"/>
          <oslc:resourceShape rdf:resource="{SHAPE}"/></oslc:CreationFactory></oslc:creationFactory>
        </oslc:Service></oslc:service></oslc:ServiceProvider></rdf:RDF>'''),
    ("GET", COMP): (200, {}, f'''<rdf:RDF {RDF}><oslc_config:Component rdf:about="{COMP}"><dcterms:title>Comp 1</dcterms:title>
        <oslc_config:configurations rdf:resource="{COMP}/configurations"/></oslc_config:Component></rdf:RDF>'''),
    ("GET", f"{COMP}/configurations"): (200, {}, f'''<rdf:RDF {RDF}><rdf:Description rdf:about="{COMP}/configurations">
        <rdfs:member rdf:resource="{BASELINE}"/><rdfs:member rdf:resource="{STREAM}"/>
        </rdf:Description></rdf:RDF>'''),
    ("GET", BASELINE): (200, {}, f'''<rdf:RDF {RDF}><oslc_config:Baseline
        rdf:about="{BASELINE}"><dcterms:title>BL 1</dcterms:title></oslc_config:Baseline></rdf:RDF>'''),
    ("GET", STREAM): (200, {}, f'''<rdf:RDF {RDF}><oslc_config:Stream rdf:about="{STREAM}">
        <dcterms:title>Stream inicial</dcterms:title></oslc_config:Stream></rdf:RDF>'''),
    ("GET", QUERY): (200, {}, f'''<rdf:RDF {RDF}><rdf:Description rdf:about="{QUERY}">
        <rdfs:member><rdf:Description rdf:about="{R1}"><dcterms:identifier>123</dcterms:identifier>
        </rdf:Description></rdfs:member></rdf:Description></rdf:RDF>'''),
    ("GET", R1): (200, {}, f'''<rdf:RDF {RDF}><rdf:Description rdf:about="{R1}">
        <dcterms:identifier>123</dcterms:identifier><dcterms:title>Login</dcterms:title></rdf:Description></rdf:RDF>'''),
}


@pytest.fixture
def srv(fake):
    fake.routes = dict(ROUTES)
    return fake


def _query_params(srv):
    return parse_qs(urlsplit(next(c.url for c in srv.calls if c.url.startswith(QUERY))).query)


def test_component_configuration_filter(srv):
    assert [c["title"] for c in requirements.get_rm_component_configuration("_PA1", "_C1", "stream")] == ["Stream inicial"]
    assert len(requirements.get_rm_component_configuration("_PA1", "_C1")) == 2


def test_get_requirement_uses_default_stream_and_identifier(srv):
    r = requirements.get_requirement("_PA1", "_C1", "123")
    assert r["title"] == "Login"
    assert _query_params(srv)["oslc.where"] == ["dcterms:identifier=123"]
    assert srv.calls[-1].headers["Configuration-Context"] == STREAM


def test_get_requirement_validation(srv):
    with pytest.raises(ValueError, match="numérico"):
        requirements.get_requirement("_PA1", "_C1", "abc")
    with pytest.raises(ValueError, match="não os dois"):
        requirements.get_requirement("_PA1", "_C1", "1", configuration_url=STREAM,
                                     global_configuration_url=f"{SERVER}/gc/configuration/1")


def test_search_requirement_search_terms(srv):
    requirements.search_requirement("_PA1", "_C1", "login", configuration_url=STREAM)
    assert _query_params(srv)["oslc.searchTerms"] == ['"login"']


def test_create_requirement_payload(srv):
    sent = {}

    def post(req):
        sent["body"] = req.body
        return 201, {"Location": R1}, b""

    srv.routes[("POST", FACTORY)] = post
    r = requirements.create_requirement(
        f"{SERVER}/rm/process/project-areas/_PA1", COMP, SHAPE, "Login", "desc", "texto & mais",
        configuration_url=STREAM, folder_url=f"{SERVER}/rm/folders/FR_1")
    assert r["url"] == R1
    body = sent["body"].decode()
    assert "instanceShape" in body and "FR_1" in body and "texto &amp; mais" in body and 'parseType="Literal"' in body


def test_create_requirement_rejects_baseline_and_module(srv):
    pa = f"{SERVER}/rm/process/project-areas/_PA1"
    with pytest.raises(ValueError, match="baseline"):
        requirements.create_requirement(pa, COMP, SHAPE, "t", "d", "p", configuration_url=BASELINE)
    with pytest.raises(ValueError, match="MD_"):
        requirements.create_requirement(pa, COMP, SHAPE, "t", "d", "p", configuration_url=STREAM,
                                        folder_url=f"{SERVER}/rm/resources/MD_1")
    assert not any(c.method == "POST" for c in srv.calls)


def test_get_project_components_from_provider(srv):
    assert [c["title"] for c in requirements.get_project_components("Req Alfa")] == ["Comp 1"]
    assert [c["url"] for c in requirements.get_project_components("_PA1", "_C1")] == [COMP]
    assert requirements.get_project_components("_PA1", "_OUTRO") == []
    with pytest.raises(LookupError):
        requirements.get_project_components("Nao Existe")


def test_list_folders_follows_subfolders(srv):
    folders = f"{SERVER}/rm/folders"
    base = f"{folders}?oslc.where=public_rm:parent={SERVER}/rm/process/project-areas/_PA1"
    NAV = 'xmlns:nav="http://jazz.net/ns/rm/navigation#"'

    def page(folder_id, title, sub):
        return (200, {}, f'''<rdf:RDF {RDF} {NAV}><rdf:Description rdf:about="{folders}"><rdfs:member>
            <nav:folder rdf:about="{folders}/{folder_id}"><dcterms:title>{title}</dcterms:title>
            <nav:subfolders rdf:resource="{folders}?sub={sub}"/></nav:folder></rdfs:member></rdf:Description></rdf:RDF>''')

    srv.routes[("GET", SP)] = (200, {}, f'''<rdf:RDF {RDF}><oslc:ServiceProvider rdf:about="{SP}"><oslc:service>
        <oslc:Service><oslc:queryCapability><oslc:QueryCapability><oslc:queryBase rdf:resource="{base}"/>
        <oslc:resourceType rdf:resource="http://jazz.net/ns/rm/navigation#folder"/></oslc:QueryCapability>
        </oslc:queryCapability></oslc:Service></oslc:service></oslc:ServiceProvider></rdf:RDF>''')
    srv.routes[("GET", COMP)] = (200, {}, f'''<rdf:RDF {RDF} xmlns:process="http://jazz.net/ns/process#">
        <oslc_config:Component rdf:about="{COMP}">
        <process:projectArea rdf:resource="{SERVER}/rm/process/project-areas/_PA1"/>
        <oslc_config:configurations rdf:resource="{COMP}/configurations"/></oslc_config:Component></rdf:RDF>''')
    pages = {None: page("FR_ROOT", "root", "root"), "root": page("FR_A", "A", "a"), "a": (200, {}, f"<rdf:RDF {RDF}/>")}
    srv.routes[("GET", folders)] = lambda req: pages[parse_qs(urlsplit(req.url).query).get("sub", [None])[0]]
    found = requirements.list_rm_component_folders(COMP, configuration_url=STREAM)
    assert [(f["title"], f["parent"]) for f in found] == [("root", None), ("A", f"{folders}/FR_ROOT")]


# --- revisão final

def test_create_requirement_rejects_gc_baseline(srv):
    gc = f"{SERVER}/gc/configuration/5"
    srv.routes[("GET", gc)] = (200, {}, f'''<rdf:RDF {RDF}><oslc_config:Baseline rdf:about="{gc}">
        <dcterms:title>GC BL</dcterms:title></oslc_config:Baseline></rdf:RDF>''')
    with pytest.raises(ValueError, match="baseline"):
        requirements.create_requirement(f"{SERVER}/rm/process/project-areas/_PA1", COMP, SHAPE, "t", "d", "p",
                                        global_configuration_url=gc)
    assert not any(c.method == "POST" for c in srv.calls)

