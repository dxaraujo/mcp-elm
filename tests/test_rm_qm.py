import json
import os

import pytest

from conftest import SERVER, chamar, rdf
from elm_fake import Elm, REQ_BASE

R = SERVER + "/rm/resources/_R{}"


@pytest.fixture
def elm(fake):
    e = Elm(fake)
    e.req(1, "REG-01 Validar CPF", f'Ver <a href="{R.format(2)}">x</a> e '
                                   f'<span><a class="embedded" href="{R.format(2)}"> </a></span>.')
    e.req(2, "GL-01 CPF", "Cadastro de pessoa física")
    return e


def ok(r):
    assert r["ok"], r
    return r["dados"]


def falha(r):
    assert not r["ok"], r
    return r["erro"]


def _sem(projeto, secao):
    arq = os.path.join(projeto, ".kiro/config/sdd/pa_alfa.json")
    dados = json.load(open(arq, encoding="utf-8"))
    del dados[secao]
    json.dump(dados, open(arq, "w", encoding="utf-8"))


def test_buscar_req(elm, projeto):
    assert ok(chamar("elm_buscar_req", projeto=projeto, texto="cpf", limite=1)) == {
        "itens": [{"id": 1, "titulo": "REG-01 Validar CPF", "tipo": "Regra", "modificado": "2026-01-01T10:00:00Z"}],
        "truncado": True}
    _sem(projeto, "rm")
    assert falha(chamar("elm_buscar_req", projeto=projeto, texto="cpf")) == {
        "codigo": "config", "detalhe": "pa sem a seção rm (DOORS Next)", "campo": "rm"}


def test_ler_req(elm, projeto):
    md = chamar("elm_ler_req", projeto=projeto, id=1)
    assert md.startswith('---\nid: 1\ntitulo: REG-01 Validar CPF\ntipo: Regra\npasta: "03-Regras"\n')
    assert f"Ver [2 GL-01 CPF]({R.format(2)}) e [2 GL-01 CPF]({R.format(2)})." in md
    assert falha(chamar("elm_ler_req", projeto=projeto, id=9))["codigo"] == "nao_encontrado"


def test_publicar_req_cria_atualiza_e_conflito(elm, projeto):
    d = ok(chamar("elm_publicar_req", projeto=projeto, sigla="REG", titulo="REG-02 Nova", texto_md="Usa [2](2)."))
    assert d == {"id": 3, "url": R.format(3), "acao": "criado"}
    assert "folders/FR_REG" in elm.req_criado and "types/OT_REG" in elm.req_criado and R.format(2) in elm.req_criado
    e = falha(chamar("elm_publicar_req", projeto=projeto, sigla="REG", titulo="t", texto_md="x", id=1,
                     modificado_em="2025-12-31T00:00:00Z"))
    assert e["codigo"] == "conflito" and "2026-01-01T10:00:00Z" in e["detalhe"] and "2025-12-31T00:00:00Z" in e["detalhe"]
    d = ok(chamar("elm_publicar_req", projeto=projeto, sigla="REG", titulo="REG-01 v2", texto_md="Cita [2](2).",
                  id=1, modificado_em="2026-01-01T10:00:00Z", embutir=[2]))
    assert d["acao"] == "atualizado" and elm.reqs[1]["titulo"] == "REG-01 v2"
    assert 'class="embedded"' in elm.reqs[1]["texto"]
    assert falha(chamar("elm_publicar_req", projeto=projeto, sigla="XX", titulo="t", texto_md="x"))["campo"] == \
        "rm.artefatos.XX"


def test_criar_ct_ja_ligado(elm, projeto):
    elm.wi(42, "x")
    d = ok(chamar("elm_criar_ct", projeto=projeto, titulo="CT-01", cenario_md="Dado...", reqs=[1], wi=42))
    assert d == {"id": 501, "url": SERVER + "/qm/tc/501"}
    assert elm.cts[501]["valida"] == [R.format(1)]
    assert SERVER + "/qm/tc/501" in elm.fake.bodies("PUT", "WorkItem/42")[0]
    _sem(projeto, "qm")
    assert falha(chamar("elm_criar_ct", projeto=projeto, titulo="t", cenario_md="c", reqs=[]))["campo"] == "qm"


def test_ligar_pares_e_dedup(elm, projeto):
    elm.wi(42, "x")
    elm.wi(43, "y")
    elm.ct(501)
    assert ok(chamar("elm_ligar", projeto=projeto, wi=42, req=1)) == {"ja_existia": False}
    assert elm.wis[42]["reqs"] == [1]
    assert ok(chamar("elm_ligar", projeto=projeto, wi=42, req=1)) == {"ja_existia": True}
    assert ok(chamar("elm_ligar", projeto=projeto, ct=501, req=2)) == {"ja_existia": False}
    assert elm.cts[501]["valida"] == [R.format(2)]
    assert ok(chamar("elm_ligar", projeto=projeto, wi=42, ct=501))["ja_existia"] is False
    assert ok(chamar("elm_ligar", projeto=projeto, wi=42, wi_destino=43, tipo="relacionado")) == {"ja_existia": False}
    assert "relatedworkitem.related" in elm.fake.bodies("PUT", "WorkItem/42")[-1]
    assert falha(chamar("elm_ligar", projeto=projeto, wi=42, wi_destino=43))["campo"] == "ccm.links.depende"
    assert falha(chamar("elm_ligar", projeto=projeto, wi=42, req=1, ct=501))["codigo"] == "entrada"
    assert falha(chamar("elm_ligar", projeto=projeto, wi=42, req=1, tipo="valida"))["campo"] == "tipo"


def test_setup_descobrir_rm(elm, fake):
    r, cmp = fake.routes, SERVER + "/rm/cm/component/_CMP"
    r[("GET", SERVER + "/rm/sp/_RPA")] = rdf(
        f'<oslc:ServiceProvider rdf:about="{SERVER}/rm/sp/_RPA"><oslc:service><oslc:Service>'
        f'<oslc:queryCapability><oslc:QueryCapability><oslc:queryBase rdf:resource="{SERVER}/rm/folders"/>'
        '<oslc:resourceType rdf:resource="http://jazz.net/ns/rm/navigation#folder"/>'
        f'<oslc_config:component rdf:resource="{cmp}"/></oslc:QueryCapability></oslc:queryCapability>'
        f'<oslc:creationFactory><oslc:CreationFactory><oslc:creation rdf:resource="{SERVER}/rm/factory"/>'
        '<oslc:resourceType rdf:resource="http://open-services.net/ns/rm#Requirement"/>'
        f'<oslc:resourceShape rdf:resource="{SERVER}/rm/types/OT_REG"/></oslc:CreationFactory></oslc:creationFactory>'
        '</oslc:Service></oslc:service></oslc:ServiceProvider>')
    r[("GET", cmp)] = rdf(f'<rdf:Description rdf:about="{cmp}"><oslc_config:configurations rdf:resource="{cmp}/cfgs"/>'
                          f'<process:projectArea rdf:resource="{SERVER}/rm/process/project-areas/_RPA"/></rdf:Description>')
    r[("GET", cmp + "/cfgs")] = rdf(f'<rdf:Description rdf:about="{cmp}/cfgs"><rdfs:member '
                                    f'rdf:resource="{SERVER}/rm/cm/stream/_ST"/></rdf:Description>')
    r[("GET", SERVER + "/rm/cm/stream/_ST")] = rdf(
        f'<oslc_config:Stream rdf:about="{SERVER}/rm/cm/stream/_ST"><dcterms:title>Inicial</dcterms:title></oslc_config:Stream>')
    pasta = lambda url, titulo, subs="": (  # noqa: E731
        f'<rdf:Description rdf:about="{url}"><dcterms:title>{titulo}</dcterms:title>{subs}</rdf:Description>')
    r[("GET", SERVER + "/rm/folders")] = rdf(
        f'<rdf:Description rdf:about="{SERVER}/rm/folders"><rdfs:member rdf:resource="{SERVER}/rm/folders/FR_ROOT"/>'
        "</rdf:Description>" + pasta(SERVER + "/rm/folders/FR_ROOT", "root",
                                     f'<nav:subfolders rdf:resource="{SERVER}/rm/sub/root"/>'))
    r[("GET", SERVER + "/rm/sub/root")] = rdf(
        f'<rdf:Description rdf:about="{SERVER}/rm/sub/root"><rdfs:member rdf:resource="{SERVER}/rm/folders/FR_REG"/>'
        "</rdf:Description>" + pasta(SERVER + "/rm/folders/FR_REG", "03-Regras"))
    assert ok(chamar("elm_setup_descobrir", ccm="zzz", rm="alfa"))["rm"] == {
        "pa": {"nome": "Requisitos Alfa", "id": "_RPA"}, "componente": "_CMP", "stream": "_ST",
        "pastas": {"03-Regras": "FR_REG"}, "tipos": {"Regra": "OT_REG"}}
    assert REQ_BASE  # base de requisitos não é usada na descoberta
