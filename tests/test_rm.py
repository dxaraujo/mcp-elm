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
LINK = "http://www.ibm.com/xmlns/rdm/types/Link"


@pytest.fixture
def req_srv(srv):
    srv.routes[("GET", SHAPE)] = (200, {}, f'''<rdf:RDF {RDF}><oslc:ResourceShape rdf:about="{SHAPE}">
        <dcterms:title>Requisito</dcterms:title>
        <oslc:property><oslc:Property><dcterms:title>Prioridade</dcterms:title><oslc:name>prio</oslc:name>
          <oslc:propertyDefinition rdf:resource="{PRIO}"/><oslc:allowedValue rdf:resource="{ALTA}"/>
        </oslc:Property></oslc:property>
        <oslc:property><oslc:Property><dcterms:title>Vincular A</dcterms:title><oslc:name>link</oslc:name>
          <oslc:propertyDefinition rdf:resource="{LINK}"/>
          <oslc:valueType rdf:resource="http://open-services.net/ns/core#Resource"/>
        </oslc:Property></oslc:property>
        <oslc:property><oslc:Property><dcterms:title>Revisor</dcterms:title><oslc:name>revisor</oslc:name>
          <oslc:propertyDefinition rdf:resource="https://alm.test/rm/types/AT_REV"/>
          <oslc:range rdf:resource="http://xmlns.com/foaf/0.1/Person"/>
        </oslc:Property></oslc:property>
        <oslc:property><oslc:Property><oslc:name>semtitulo</oslc:name>
          <oslc:propertyDefinition rdf:resource="http://purl.org/dc/terms/contributor"/>
        </oslc:Property></oslc:property></oslc:ResourceShape></rdf:RDF>''')
    # como no DOORS Next: o valor é um fragmento do documento do tipo e o nome vem em rdfs:label
    srv.routes[("GET", ALTA.split("#")[0])] = (200, {}, f'''<rdf:RDF {RDF}><rdf:Description rdf:about="{ALTA}">
        <rdfs:label>Alta</rdfs:label></rdf:Description></rdf:RDF>''')
    srv.routes[("GET", R1)] = (200, {"ETag": '"1"'}, f'''<rdf:RDF {RDF} xmlns:nav="http://jazz.net/ns/rm/navigation#"
        xmlns:jazz_rm="http://jazz.net/ns/rm#"><rdf:Description rdf:about="{R1}">
        <dcterms:identifier>123</dcterms:identifier><dcterms:title>Login</dcterms:title>
        <jazz_rm:primaryText rdf:parseType="Literal"><div xmlns="http://www.w3.org/1999/xhtml"><p>Passo 1: <a class="embedded" href="{R1}"> </a></p></div></jazz_rm:primaryText>
        <dcterms:created rdf:datatype="http://www.w3.org/2001/XMLSchema#dateTime">2024-09-23T20:30:48.392Z</dcterms:created>
        <dcterms:modified rdf:datatype="http://www.w3.org/2001/XMLSchema#dateTime">2024-10-01T13:45:10.000Z</dcterms:modified>
        <oslc:instanceShape rdf:resource="{SHAPE}"/><nav:parent rdf:resource="{FOLDER}"/>
        <rdf:type rdf:resource="http://open-services.net/ns/rm#Requirement"/>
        <j.0:PRIO xmlns:j.0="https://alm.test/rm/types/AT_" rdf:resource="{ALTA}"/>
        <dcterms:contributor rdf:resource="{SERVER}/jts/users/joao"/>
        </rdf:Description></rdf:RDF>''')
    # página de busca com os campos do oslc.select, como o DN devolve
    srv.routes[("GET", QUERY)] = (200, {}, f'''<rdf:RDF {RDF} xmlns:nav="http://jazz.net/ns/rm/navigation#">
        <rdf:Description rdf:about="{QUERY}"><rdfs:member><rdf:Description rdf:about="{R1}">
        <dcterms:identifier>123</dcterms:identifier><dcterms:title>Login</dcterms:title>
        <dcterms:modified rdf:datatype="http://www.w3.org/2001/XMLSchema#dateTime">2024-10-01T13:45:10.000Z</dcterms:modified>
        <oslc:instanceShape rdf:resource="{SHAPE}"/><nav:parent rdf:resource="{FOLDER}"/>
        </rdf:Description></rdfs:member></rdf:Description></rdf:RDF>''')
    srv.routes[("GET", FOLDER)] = (200, {}, f'''<rdf:RDF {RDF}><rdf:Description rdf:about="{FOLDER}">
        <dcterms:title>01-Requisitos</dcterms:title></rdf:Description></rdf:RDF>''')
    return srv


def test_search_filters_and_summary(req_srv):
    found = rm.rm_search_requirements("_PA1", C, S, text="login", folder=F, requirement_type=T)
    assert found == [{"id": "123", "title": "Login", "type": "Requisito", "folder": "01-Requisitos",
                      "modified": "2024-10-01T13:45:10Z", "path": "01-Requisitos/123-login.md", "url": R1}]
    q = parse_qs(urlsplit(next(c.url for c in req_srv.calls if c.url.startswith(QUERY + "?"))).query)
    assert q["oslc.where"] == [f"nav:parent=<{FOLDER}> and oslc:instanceShape=<{SHAPE}>"]
    assert q["oslc.searchTerms"] == ['"login"']


def test_list_modified_single_and_batch(req_srv):
    assert rm.rm_list_modified("_PA1", C, S, ["123"]) == [
        {"id": "123", "title": "Login", "modified": "2024-10-01T13:45:10Z"}]
    q = parse_qs(urlsplit(next(c.url for c in req_srv.calls if c.url.startswith(QUERY + "?"))).query)
    assert q["oslc.where"] == ['dcterms:identifier in ["123"]']
    assert q["oslc.select"] == [rm.MODIFIED_SELECT]
    req_srv.calls.clear()
    rm.rm_list_modified("_PA1", C, S, [str(i) for i in range(150)] + ["0"])  # repetido não conta
    wheres = [parse_qs(urlsplit(c.url).query)["oslc.where"][0] for c in req_srv.calls
              if c.url.startswith(QUERY + "?")]
    assert [w.count(",") + 1 for w in wheres] == [100, 50]


def test_get_requirement_markdown_with_server_names_and_embeds(req_srv, monkeypatch):
    class FixedNow(rm.datetime):
        @classmethod
        def now(cls, tz=None):
            return rm.datetime(2026, 10, 6, 13, 0, tzinfo=tz)
    monkeypatch.setattr(rm, "datetime", FixedNow)
    req_srv.routes[("GET", R1)] = (200, {"ETag": '"1"'}, req_srv.routes[("GET", R1)][2].replace(
        "</rdf:Description>", f'<j.1:Link xmlns:j.1="http://www.ibm.com/xmlns/rdm/types/" rdf:resource="{R1}"/>'
        "</rdf:Description>", 1))
    from importlib import metadata
    version = metadata.version("mcp-alm")
    doc = rm.rm_get_requirement("_PA1", C, S, "123")
    assert doc == f"""---
type: Requisito
title: Login
resource: "{R1}"
tags:
  - "01-Requisitos"
  - Requisito
sources:
  - id: doors-next
    resource: "{R1}"
    title: DOORS Next 123
    author: "human:joao"
    last_modified: "2024-10-01T13:45:10Z"
generated:
  by: "process:alm-mcp/{version}"
  at: "2026-10-06T13:00:00Z"
id: 123
url: "{R1}"
folder: "01-Requisitos"
contributor: joao
created: "2024-09-23 17:30"
modified: "2024-10-01 10:45"
attributes:
  Prioridade: Alta
links:
  Vincular A:
    - "123: Login"
embedded:
  - "123: Login"
---
Passo 1: ![123 Login]({R1})
"""
    # description/verified/status/stale_after não têm fonte neste requisito: ausentes do cabeçalho
    assert "description:" not in doc
    assert "verified:" not in doc and "status:" not in doc and "stale_after:" not in doc
    # resource é o URI canônico e url é alias com o mesmo valor
    assert f'resource: "{R1}"' in doc and f'url: "{R1}"' in doc


def test_get_requirement_description_present_when_source_exists(req_srv):
    # dcterms:description com espaços repetidos vira a OKF description em uma linha (one_line)
    req_srv.routes[("GET", R1)] = (200, {"ETag": '"1"'}, req_srv.routes[("GET", R1)][2].replace(
        "</rdf:Description>",
        "<dcterms:description>Resumo  do   requisito.</dcterms:description></rdf:Description>", 1))
    doc = rm.rm_get_requirement("_PA1", C, S, "123")
    assert "description: Resumo do requisito." in doc


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


def test_update_requirement_markdown_embed_and_link_by_id(req_srv):
    sent = {}
    req_srv.routes[("PUT", R1)] = lambda req: (sent.update(body=req.body.decode()), (200, {}, b""))[1]
    rm.rm_update_requirement("_PA1", C, S, "123", text="1. Passo ![[123]]", attributes={"Vincular A": "123: Login"})
    assert f'class="embedded" href="{R1}"' in sent["body"] and "<ol>" in sent["body"]
    assert f'rdf:resource="{R1}"' in sent["body"].split("Link")[1]


def test_search_requires_a_filter():
    with pytest.raises(ValueError, match="ao menos um filtro"):
        rm.rm_search_requirements("_PA1", C, S)


def test_update_multi_value_enum_member_login_and_unreadable_link(req_srv):
    sent = {}
    req_srv.routes[("PUT", R1)] = lambda req: (sent.update(body=req.body.decode()), (200, {}, b""))[1]
    rm.rm_update_requirement("_PA1", C, S, "123", attributes={"Prioridade": ["Alta"]})
    assert f'rdf:resource="{ALTA}"' in sent["body"]
    with pytest.raises(ValueError, match="Baixa"):
        rm.rm_update_requirement("_PA1", C, S, "123", attributes={"Prioridade": ["Alta", "Baixa"]})
    rm.rm_update_requirement("_PA1", C, S, "123", attributes={"Revisor": "joao"})
    assert f'rdf:resource="{SERVER}/jts/users/joao"' in sent["body"]


def test_unreadable_artifact_does_not_break_read(req_srv):
    gone = f"{SERVER}/rm/resources/TX_GONE"
    req_srv.routes[("GET", gone)] = (403, {}, b"forbidden")
    assert rm._artifact(gone, STREAM) is None


def test_get_requirement_bundle_links_are_relative_file_paths(req_srv):
    root, regras = f"{SERVER}/rm/folders/FR_ROOT", f"{SERVER}/rm/folders/FR_3"
    r2 = f"{SERVER}/rm/resources/TX_2"
    nav = 'xmlns:nav="http://jazz.net/ns/rm/navigation#"'
    req_srv.routes[("GET", root)] = (200, {}, f'<rdf:RDF {RDF}><rdf:Description rdf:about="{root}">'
                                              '<dcterms:title>root</dcterms:title></rdf:Description></rdf:RDF>')
    for url, title in ((FOLDER, "01-Requisitos"), (regras, "03 Regras")):
        req_srv.routes[("GET", url)] = (200, {}, f'''<rdf:RDF {RDF} {nav}><rdf:Description rdf:about="{url}">
            <dcterms:title>{title}</dcterms:title><nav:parent rdf:resource="{root}"/></rdf:Description></rdf:RDF>''')
    req_srv.routes[("GET", r2)] = (200, {}, f'''<rdf:RDF {RDF} {nav}><rdf:Description rdf:about="{r2}">
        <dcterms:identifier>2</dcterms:identifier><dcterms:title>RN - Validar CPF</dcterms:title>
        <nav:parent rdf:resource="{regras}"/></rdf:Description></rdf:RDF>''')
    req_srv.routes[("GET", R1)] = (200, {"ETag": '"1"'}, req_srv.routes[("GET", R1)][2].replace(
        f'href="{R1}"> </a>', f'href="{r2}"> </a> e <a href="/rm/resources/TX_2">RN 2</a>'))
    doc = rm.rm_get_requirement("_PA1", C, S, "123", links="bundle")
    target = "<../03 Regras/2-rn-validar-cpf.md>"
    assert f"Passo 1: ![2 RN - Validar CPF]({target}) e [2 RN - Validar CPF]({target})" in doc
    assert '- "2: RN - Validar CPF"' in doc.split("embedded:")[1]
    assert f"![2 RN - Validar CPF]({r2})" in rm.rm_get_requirement("_PA1", C, S, "123")


def test_list_folder_only_direct_members(req_srv):
    assert rm.rm_list_folder("_PA1", C, S, F) == [
        {"id": "123", "title": "Login", "modified": "2024-10-01T13:45:10Z"}]
    q = parse_qs(urlsplit(next(c.url for c in req_srv.calls if c.url.startswith(QUERY + "?"))).query)
    assert q["oslc.where"] == [f"nav:parent=<{FOLDER}>"] and q["oslc.select"] == [rm.MODIFIED_SELECT]
    assert "oslc.searchTerms" not in q


def test_count_folder_falls_back_to_listing(req_srv):
    assert rm.rm_count_folder("_PA1", C, S, F) == {"folder": F, "count": 1}  # fixture sem oslc:totalCount


def test_download_requirements_writes_bundle_file_and_replaces_old(req_srv, tmp_path, monkeypatch):
    old = tmp_path / "antiga" / "123-titulo-velho.md"
    old.parent.mkdir()
    old.write_text("velho")
    [item] = rm.rm_download_requirements("_PA1", C, S, ["123"], str(tmp_path))
    assert item["path"] == "01-Requisitos/123-login.md" and item["replaced"] == ["antiga/123-titulo-velho.md"]
    assert item["last_modified"] == "2024-10-01T13:45:10Z" and not old.exists()
    text = (tmp_path / item["path"]).read_text(encoding="utf-8")
    assert text.startswith("---") and f'at: "{item["generated_at"]}"' in text
    monkeypatch.setattr(rm, "_requirement", lambda *a: (_ for _ in ()).throw(RuntimeError("HTTP 404")))
    assert rm.rm_download_requirements("_PA1", C, S, ["999"], str(tmp_path)) == [{"id": "999", "error": "HTTP 404"}]
    with pytest.raises(ValueError):
        rm.rm_download_requirements("_PA1", C, S, ["123"], "relativo")
