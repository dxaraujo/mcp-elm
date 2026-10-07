from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF

from mcp_alm.infra import auth, config, oslc
from mcp_alm.infra.http import AlmHttpError, get_session, get_xml, reportable

from conftest import SERVER, fixture, ok, xml_ok

URL = f"{SERVER}/ccm/rootservices"


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "alm.properties"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_config_ok(tmp_path):
    cfg = config.load_config(write(tmp_path, "[DEFAULT]\nserver = https://alm.x/\nuser = u\npassword = p%ss\n"))
    assert (cfg.server, cfg.user, cfg.password) == ("https://alm.x", "u", "p%ss")
    assert "p%ss" not in repr(cfg)


def test_load_config_missing_key_and_file(tmp_path):
    with pytest.raises(ValueError, match="password"):
        config.load_config(write(tmp_path, "[DEFAULT]\nserver = s\nuser = u\n"))
    with pytest.raises(FileNotFoundError):
        config.load_config(tmp_path / "nao-existe")


def test_default_path_env(monkeypatch):
    monkeypatch.setenv("MCP_ALM_CONFIG", "/tmp/x.properties")
    assert config.default_path() == Path("/tmp/x.properties")


def test_context_root():
    assert auth.context_root("https://h:9443/ccm/oslc/x?y=1") == "https://h:9443/ccm"


def test_form_login_then_retry(fake):
    state = {"logged": False}

    def resource(_req):
        if state["logged"]:
            return ok("rootservices_ccm.xml")
        return 200, {auth.AUTH_HEADER: "authrequired"}, b"<html>login</html>"

    def check(req):
        assert "j_username=joao" in req.body and "j_password=segredo" in req.body
        state["logged"] = True
        return 200, {}, b""

    fake.routes = {
        ("GET", URL): resource,
        ("GET", f"{SERVER}/ccm/authenticated/identity"): (200, {}, b""),
        ("POST", f"{SERVER}/ccm/j_security_check"): check,
    }
    assert b"cmServiceProviders" in get_session().request("GET", URL).content
    assert [c.method for c in fake.calls] == ["GET", "GET", "POST", "GET"]


def test_login_failed(fake):
    fake.routes = {
        ("GET", URL): (200, {auth.AUTH_HEADER: "authrequired"}, b""),
        ("GET", f"{SERVER}/ccm/authenticated/identity"): (200, {}, b""),
        ("POST", f"{SERVER}/ccm/j_security_check"): (200, {auth.AUTH_HEADER: "authfailed"}, b""),
    }
    with pytest.raises(auth.AuthError, match="joao"):
        get_session().request("GET", URL)


def test_basic_fallback(fake):
    def resource(req):
        if req.headers.get("Authorization", "").startswith("Basic "):
            return ok("rootservices_ccm.xml")
        return 401, {"WWW-Authenticate": 'Basic realm="jazz"'}, b""

    fake.routes = {("GET", URL): resource}
    get_session().request("GET", URL)
    assert get_session().session.auth == ("joao", "segredo")


def test_http_error_and_configuration_header(fake):
    fake.routes = {("GET", URL): (500, {}, b"boom")}
    with pytest.raises(AlmHttpError, match="HTTP 500"):
        get_session().request("GET", URL, configuration="https://alm.test/rm/cm/stream/_S1")
    req = fake.calls[0]
    assert req.headers["Configuration-Context"] == "https://alm.test/rm/cm/stream/_S1"
    assert "oslc_config.context=" in req.url


def test_get_xml_oslc_v1_drops_header(fake):
    fake.routes = {("GET", f"{SERVER}/x"): xml_ok("<a><b>1</b></a>")}
    assert get_xml("/x", oslc_v1=True).findtext("b") == "1"
    assert "OSLC-Core-Version" not in fake.calls[0].headers


def test_reportable_follows_next_until_empty(fake):
    page1 = f'<foundation rel="next" href="{SERVER}/rpt?p=2"><contributor><userId>a</userId></contributor></foundation>'
    page2 = f'<foundation rel="next" href="{SERVER}/rpt?p=3"></foundation>'
    fake.routes = {("GET", f"{SERVER}/rpt"): xml_ok(page1), ("GET", f"{SERVER}/rpt?p=2"): xml_ok(page2)}
    records = reportable("/rpt", "foundation/contributor/(userId)", "contributor")
    assert [r.findtext("userId") for r in records] == ["a"]


# --- oslc

WI = f"{SERVER}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42"
CATALOG = f"{SERVER}/ccm/oslc/workitems/catalog"
SP = f"{SERVER}/ccm/oslc/contexts/_PA1/workitems/services.xml"
SHAPE = f"{SERVER}/ccm/oslc/context/_PA1/shapes/workitems/defect"
QUERY = f"{SERVER}/ccm/oslc/contexts/_PA1/workitems"
CR = "http://open-services.net/ns/cm#ChangeRequest"


@pytest.fixture
def srv(fake):
    fake.routes = {
        ("GET", f"{SERVER}/ccm/rootservices"): ok("rootservices_ccm.xml"),
        ("GET", CATALOG): ok("catalog_ccm.xml"),
        ("GET", SP): ok("sp_ccm.xml"),
        ("GET", SHAPE): ok("shape_defect.xml"),
    }
    return fake


def test_qname_decodes_accents():
    encoded = "http://jazz.net/xmlns/prod/jazz/rtc/ext/1.0/tarefa.classifica%C3%A7%C3%A3o"
    assert oslc.qname(encoded) == oslc.qname(encoded.replace("%C3%A7%C3%A3", "çã")) == "rtc_ext:tarefa.classificação"


def test_resource_dict():
    r = oslc.resource(oslc.parse(fixture("workitem_42.xml")), URIRef(WI))
    assert (r["id"], r["title"], r["types"]) == ("42", "Corrigir login", ["oslc_cm:ChangeRequest"])
    assert r["properties"]["dcterms:description"] == "Falha ao logar & sair"
    assert r["links"]["calm:implementsRequirement"] == [
        {"url": f"{SERVER}/rm/resources/_R1", "title": "REQ-1 Login seguro"}]


def test_set_properties_replace_and_remove():
    g, s = oslc.parse(fixture("workitem_42.xml")), URIRef(WI)
    oslc.set_properties(g, s, {"dcterms:title": "X", "rtc_cm:filedAgainst": None})
    r = oslc.resource(g, s)
    assert r["title"] == "X" and "rtc_cm:filedAgainst" not in r["links"]


def test_serialize_xhtml_as_parsetype_literal():
    g = Graph()
    g.add((URIRef(""), oslc.JAZZ_RM.primaryText,
           Literal('<div xmlns="http://www.w3.org/1999/xhtml">a &amp; b</div>', datatype=RDF.XMLLiteral)))
    out = oslc.serialize(g).decode()
    assert 'rdf:parseType="Literal"' in out and "&lt;div" not in out


def test_literal_and_where():
    assert oslc.literal('diz "oi"') == '"diz \\"oi\\""'
    assert oslc.literal("https://x/y") == "<https://x/y>"
    assert (oslc.literal(5), oslc.literal(False)) == ("5", "false")
    assert oslc.where_in("rtc_cm:teamArea", ["https://a", "https://b"]) == "rtc_cm:teamArea in [<https://a>,<https://b>]"


def test_query_pagination_and_params(fake):
    fake.routes = {("GET", QUERY): ok("query_page1.xml"), ("GET", f"{QUERY}?page=2"): ok("query_page2.xml")}
    res = oslc.query(QUERY, where='dcterms:title="x"', limit=10)
    assert [r["id"] for r in res] == ["1", "2"]
    first = parse_qs(urlsplit(fake.calls[0].url).query)
    assert first["oslc.where"] == ['dcterms:title="x"'] and first["oslc.paging"] == ["true"]
    assert "oslc.where" not in fake.calls[1].url


def test_update_sends_etag(fake):
    captured = {}

    def put(req):
        captured["req"] = req
        return 200, {}, b""

    fake.routes = {("GET", WI): ok("workitem_42.xml", ETag='"v1"'), ("PUT", WI): put}
    oslc.update(WI, {"calm:implementsRequirement": f"{SERVER}/rm/resources/_R2"}, append=True)
    assert captured["req"].headers["If-Match"] == '"v1"'
    assert b"_R1" in captured["req"].body and b"_R2" in captured["req"].body  # append mantém o existente


def test_providers_by_uuid(srv):
    assert oslc.catalog_url("ccm") == CATALOG
    assert [(p["title"], p["project_area_uuid"]) for p in oslc.providers("ccm")] == [
        ("Projeto Alfa", "_PA1"), ("Projeto Beta", "_PA2")]
    assert oslc.provider("ccm", "_PA1") == SP
    with pytest.raises(LookupError, match="_NOPE"):
        oslc.provider("ccm", "_NOPE")


def test_services_query_base_and_factories(srv):
    assert oslc.query_base(SP, CR) == QUERY
    assert oslc.creation_factories(SP, CR)[0]["resource_shapes"] == [SHAPE]
    assert oslc.resource_shapes(SP, CR) == [SHAPE]
    with pytest.raises(LookupError):
        oslc.query_base(SP, "http://open-services.net/ns/qm#TestCase")


def test_shape(srv):
    props = {p["predicate"]: p for p in oslc.shape(SHAPE)["properties"]}
    assert props["rtc_cm:filedAgainst"]["required"] and props["rtc_cm:filedAgainst"]["allowed_values_url"]
    assert props["oslc_cmx:priority"]["name"] == "priority"
    assert props["rtc_cm:plannedFor"]["allowed_values"] == [f"{SERVER}/ccm/oslc/iterations/_IT1"]


def test_discovery_cache(srv):
    oslc.providers("ccm")
    oslc.providers("ccm")
    assert sum(c.url == CATALOG for c in srv.calls) == 1


def test_invalid_app():
    with pytest.raises(ValueError, match="rm, ccm, qm, gc"):
        oslc.catalog_url("xyz")


def test_url_relative_path_with_absolute_url_in_query(fake):
    assert get_session().url("/rm/x?u=https://a/b") == f"{SERVER}/rm/x?u=https://a/b"
    assert get_session().url("https://outro/y") == "https://outro/y"


def test_query_without_rdfs_member_uses_typed_subjects(fake):
    base = f"{SERVER}/qm/oslc_qm/contexts/_PA1/resources/com.ibm.rqm.planning.VersionedTestCase"
    fake.routes = {("GET", base): (200, {}, f'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:dcterms="http://purl.org/dc/terms/" xmlns:oslc="http://open-services.net/ns/core#">
        <rdf:Description rdf:about="{base}/_T1"><dcterms:title>Login</dcterms:title>
          <rdf:type rdf:resource="http://open-services.net/ns/qm#TestCase"/></rdf:Description>
        <rdf:Description rdf:about="{base}?oslc.paging=true"><rdf:type rdf:resource="http://open-services.net/ns/core#ResponseInfo"/>
          <oslc:totalCount>1</oslc:totalCount></rdf:Description>
        <rdf:Description rdf:about="{base}"><dcterms:title>container</dcterms:title></rdf:Description>
        </rdf:RDF>''')}
    assert [r["title"] for r in oslc.query(base)] == ["Login"]


def test_non_rdf_response_is_value_error(fake):
    fake.routes = {("GET", WI): (200, {"Content-Type": "text/html"}, b"<html><body>login</body")}
    with pytest.raises(ValueError, match="RDF"):
        oslc.get(WI)
    fake.routes = {("GET", f"{SERVER}/x"): (200, {}, b"<html><body>")}
    with pytest.raises(ValueError, match="XML"):
        get_xml("/x")


def _page(ids, next_page=None, total=None):
    """Página de query com membros 'R<id>', oslc:nextPage e oslc:totalCount opcionais."""
    members = "".join(f'<rdfs:member><rdf:Description rdf:about="{SERVER}/r/{i}"><dcterms:identifier>{i}'
                      f'</dcterms:identifier></rdf:Description></rdfs:member>' for i in ids)
    info = (f'<oslc:ResponseInfo rdf:about="{QUERY}?info">'
            + (f'<oslc:nextPage rdf:resource="{next_page}"/>' if next_page else "")
            + (f'<oslc:totalCount>{total}</oslc:totalCount>' if total is not None else "") + '</oslc:ResponseInfo>')
    return 200, {}, (f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
                     f'xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#" xmlns:dcterms="http://purl.org/dc/terms/" '
                     f'xmlns:oslc="http://open-services.net/ns/core#"><rdf:Description rdf:about="{QUERY}">'
                     f'{members}</rdf:Description>{info}</rdf:RDF>').encode()


def test_query_without_limit_follows_all_pages_and_skips_repeated(fake):
    fake.routes = {("GET", QUERY): _page(range(1000), f"{QUERY}?page=2"),
                   ("GET", f"{QUERY}?page=2"): _page([999, 1000])}  # 999 repetido entre páginas
    assert [r["id"] for r in oslc.query(QUERY, limit=None)] == [str(i) for i in range(1001)]
    assert len(oslc.query(QUERY, limit=5000)) == 1000  # com limite, o teto MAX_LIMIT continua


def test_count_uses_total_count_or_pages(fake):
    fake.routes = {("GET", QUERY): _page([1], total=1500)}
    assert oslc.count(QUERY, where="x=1") == 1500
    assert parse_qs(urlsplit(fake.calls[0].url).query)["oslc.pageSize"] == ["1"]
    fake.routes = {("GET", QUERY): _page([1, 2])}  # sem totalCount: conta pelas páginas
    assert oslc.count(QUERY) == 2
