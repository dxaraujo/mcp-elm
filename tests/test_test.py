from urllib.parse import parse_qs, urlsplit

import pytest

from mcp_alm.ibm import test

from conftest import SERVER

RDF = 'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dcterms="http://purl.org/dc/terms/" ' \
      'xmlns:oslc="http://open-services.net/ns/core#" xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"'
ROOT = f"{SERVER}/qm/rootservices"
CATALOG = f"{SERVER}/qm/oslc_qm/catalog"
SP = f"{SERVER}/qm/oslc_qm/contexts/_PA1/services.xml"
QUERY = f"{SERVER}/qm/oslc_qm/contexts/_PA1/resources/com.ibm.rqm.planning.VersionedTestCase"
SHAPE = f"{SERVER}/qm/oslc_qm/contexts/_PA1/shape/creation/com.ibm.rqm.planning.VersionedTestCase"
TC = f"{SERVER}/qm/resource/itemName/com.ibm.rqm.planning.VersionedTestCase/209"
TC_RDF = f'''<rdf:RDF {RDF}><rdf:Description rdf:about="{TC}"><oslc:shortId>209</oslc:shortId>
    <dcterms:title>Login</dcterms:title><dcterms:description>passos</dcterms:description>
    <dcterms:contributor rdf:resource="{SERVER}/jts/users/joao"/></rdf:Description></rdf:RDF>'''


@pytest.fixture
def srv(fake):
    fake.routes = {
        ("GET", ROOT): (200, {}, f'''<rdf:Description {RDF} xmlns:oslc_qm="http://open-services.net/xmlns/qm/1.0/"
            rdf:about="{ROOT}"><oslc_qm:qmServiceProviders rdf:resource="{CATALOG}"/></rdf:Description>'''),
        ("GET", CATALOG): (200, {}, f'''<rdf:RDF {RDF}><oslc:ServiceProviderCatalog rdf:about="{CATALOG}">
            <oslc:serviceProvider><oslc:ServiceProvider rdf:about="{SP}"><dcterms:title>Testes</dcterms:title>
            </oslc:ServiceProvider></oslc:serviceProvider></oslc:ServiceProviderCatalog></rdf:RDF>'''),
        ("GET", SP): (200, {}, f'''<rdf:RDF {RDF}><oslc:ServiceProvider rdf:about="{SP}"><oslc:service><oslc:Service>
            <oslc:queryCapability><oslc:QueryCapability><oslc:queryBase rdf:resource="{QUERY}"/>
            <oslc:resourceType rdf:resource="http://open-services.net/ns/qm#TestCaseQuery"/></oslc:QueryCapability>
            </oslc:queryCapability>
            <oslc:creationFactory><oslc:CreationFactory><oslc:creation rdf:resource="{QUERY}"/>
            <oslc:resourceType rdf:resource="http://open-services.net/ns/qm#TestCase"/>
            <oslc:resourceShape rdf:resource="{SHAPE}"/></oslc:CreationFactory></oslc:creationFactory>
            </oslc:Service></oslc:service></oslc:ServiceProvider></rdf:RDF>'''),
        ("GET", SHAPE): (200, {}, f'''<rdf:RDF {RDF}><oslc:ResourceShape rdf:about="{SHAPE}">
            <dcterms:title>Caso de teste</dcterms:title></oslc:ResourceShape></rdf:RDF>'''),
        ("GET", QUERY): (200, {}, f'''<rdf:RDF {RDF}><rdf:Description rdf:about="{QUERY}"><rdfs:member>
            <rdf:Description rdf:about="{TC}"><oslc:shortId>209</oslc:shortId></rdf:Description>
            </rdfs:member></rdf:Description></rdf:RDF>'''),
        ("GET", TC): (200, {}, TC_RDF),
    }
    return fake


def _q(srv):
    return parse_qs(urlsplit(next(c.url for c in srv.calls if c.url.startswith(QUERY + "?"))).query)


def test_get_testartifact_by_id_minimal(srv):
    tc = test.get_testartifact("_PA1", "TestCase", id="209")
    assert tc["title"] == "Login" and "dcterms:description" not in tc["properties"]
    assert _q(srv)["oslc.where"] == ["oslc:shortId=209"]
    assert "dcterms:description" in test.get_testartifact("_PA1", "TestCase", url=TC, fetch_all=True)["properties"]


def test_get_testartifact_validation(srv):
    with pytest.raises(ValueError):
        test.get_testartifact("_PA1", "TestCase")
    with pytest.raises(ValueError, match="Bogus"):
        test.get_testartifact("_PA1", "Bogus", id="1")


def test_configuration_uuid_becomes_url(srv):
    test.get_testartifact("_PA1", "TestCase", url=TC, configuration="_CFG1")
    assert srv.calls[-1].headers["Configuration-Context"] == \
        f"{SERVER}/qm/oslc_config/resources/com.ibm.team.vvc.Configuration/_CFG1"


def test_schema_uses_creation_shape(srv):
    schema = test.get_testartifact_schema("_PA1", "TestCase")
    assert schema["artifactType"] == "TestCase" and [s["title"] for s in schema["shapes"]] == ["Caso de teste"]


def test_search_filters_and_properties(srv):
    test.search_testartifact("_PA1", "TestCase", filters={"title": "Login"}, properties=["title", "owner"])
    q = _q(srv)
    assert q["oslc.where"] == ['dcterms:title="Login"']
    assert q["oslc.select"] == ["dcterms:title,dcterms:contributor"]


@pytest.mark.parametrize("kwargs", [{"customAttributeFilters": [{}]}, {"categoryFilters": [{}]},
                                    {"linkFilters": {"TestScript": [1]}}, {"filters": {"nao_existe": 1}}])
def test_search_rejects_unsupported(srv, kwargs):
    with pytest.raises(ValueError):
        test.search_testartifact("_PA1", "TestCase", **kwargs)


def test_qm_components_filtered_by_project_area(srv):
    cfg_catalog, cfg_sp = f"{SERVER}/qm/oslc_config/catalog", f"{SERVER}/qm/oslc_config/serviceProviders/configuration"
    comps = f"{SERVER}/qm/oslc_config/resources/com.ibm.team.vvc.Component"
    PROC = 'xmlns:process="http://jazz.net/ns/process#"'
    srv.routes[("GET", ROOT)] = (200, {}, f'''<rdf:Description {RDF} xmlns:oc="http://open-services.net/xmlns/config/1.0/"
        rdf:about="{ROOT}"><oc:cmServiceProviders rdf:resource="{cfg_catalog}"/></rdf:Description>''')
    srv.routes[("GET", cfg_catalog)] = (200, {}, f'''<rdf:RDF {RDF}><oslc:ServiceProviderCatalog rdf:about="{cfg_catalog}">
        <oslc:serviceProvider><oslc:ServiceProvider rdf:about="{cfg_sp}"><dcterms:title>Config</dcterms:title>
        </oslc:ServiceProvider></oslc:serviceProvider></oslc:ServiceProviderCatalog></rdf:RDF>''')
    srv.routes[("GET", cfg_sp)] = (200, {}, f'''<rdf:RDF {RDF}><oslc:ServiceProvider rdf:about="{cfg_sp}"><oslc:service>
        <oslc:Service><oslc:queryCapability><oslc:QueryCapability><oslc:queryBase rdf:resource="{comps}"/>
        <oslc:resourceType rdf:resource="http://open-services.net/ns/config#Component"/></oslc:QueryCapability>
        </oslc:queryCapability></oslc:Service></oslc:service></oslc:ServiceProvider></rdf:RDF>''')

    def comp(uuid, pa, title):
        return f'''<rdfs:member><rdf:Description rdf:about="{comps}/{uuid}"><dcterms:title>{title}</dcterms:title>
            <process:projectArea rdf:resource="{SERVER}/qm/process/project-areas/{pa}"/></rdf:Description></rdfs:member>'''

    srv.routes[("GET", comps)] = (200, {}, f'''<rdf:RDF {RDF} {PROC}><rdf:Description rdf:about="{comps}">
        {comp("_K1", "_PA1", "Principal")}{comp("_K2", "_PA2", "Outro")}</rdf:Description></rdf:RDF>''')
    assert [c["title"] for c in test.get_qm_component("_PA1")] == ["Principal"]
    assert test.get_qm_component("_PA1", component_name="xyz") == []
