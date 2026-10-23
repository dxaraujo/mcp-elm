"""ETM (casos de teste) e links de rastreabilidade entre work item, requisito e caso de teste."""
from __future__ import annotations

from rdflib import Literal, URIRef
from rdflib.namespace import DCTERMS

from . import ewm, oslc, rm
from .contrato import Erro

# par -> {tipo: (onde grava, atributo ou chave de ccm.links)}; o primeiro tipo é o padrão. O link é gravado
# sempre no work item ou no caso de teste; o DOORS Next mostra o backlink
PARES = {
    ("wi", "req"): {"implementa": "implementa", "rastreia": "oslc_cm:tracksRequirement",
                    "afeta": "oslc_cm:affectsRequirement"},
    ("wi", "ct"): {"testado_por": "oslc_cm:testedByTestCase"},
    ("ct", "req"): {"valida": "oslc_qm:validatesRequirement"},
    ("wi", "wi_destino"): {"depende": "depende", "relacionado": "relacionado"},
}


def _qm(cfg: dict) -> dict:
    if "qm" not in cfg:
        raise Erro("config", "pa sem a seção qm (ETM)", "qm")
    return cfg["qm"]


def url_ct(cfg: dict, ct_id) -> str:
    base = oslc.query_base(oslc.provider("qm", _qm(cfg)["id"]), URIRef(str(oslc.OSLC_QM.TestCase) + "Query"))
    found = oslc.query(base, where=oslc.where_eq("oslc:shortId", int(ct_id) if str(ct_id).isdigit() else ct_id),
                       select="oslc:shortId", limit=1)
    if not found:
        raise Erro("nao_encontrado", f"caso de teste {ct_id} não encontrado", "ct")
    return found[0]["url"]


def _acrescentar(url: str, pred: str, alvo: str) -> bool:
    """Liga `url` -> `alvo` se ainda não houver o link. True se já existia."""
    r = oslc.get(url)
    if any(link["url"].split("?")[0] == alvo for link in r["links"].get(oslc.qname(oslc.expand(pred)), [])):
        return True
    oslc.update(url, {pred: URIRef(alvo)}, append=True)
    return False


def ligar(cfg: dict, wi=None, req=None, ct=None, wi_destino=None, tipo: str | None = None) -> dict:
    dados = {"wi": wi, "ct": ct, "req": req, "wi_destino": wi_destino}  # ordem: origem antes do alvo
    par = tuple(k for k, v in dados.items() if v)
    if par not in PARES:
        raise Erro("entrada", "informe wi + um de req, ct, wi_destino; ou ct + req", "wi")
    tipos = PARES[par]
    tipo = tipo or next(iter(tipos))
    if tipo not in tipos:
        raise Erro("entrada", f"tipo '{tipo}' não vale para {'+'.join(par)} ({', '.join(tipos)})", "tipo")
    pred = tipos[tipo]
    if ":" not in pred:  # vem de ccm.links do pa
        pred = ewm.LINKS.get(pred) if pred != "depende" else None
        pred = cfg["ccm"].get("links", {}).get(tipo, pred)
        if pred is None:
            raise Erro("config", f"pa sem ccm.links.{tipo}", f"ccm.links.{tipo}")
    alvo = {"req": lambda: rm.url_req(cfg, req), "ct": lambda: url_ct(cfg, ct),
            "wi_destino": lambda: ewm.wi_url(wi_destino)}[par[1]]()
    origem = ewm.wi_url(wi) if par[0] == "wi" else url_ct(cfg, ct)
    return {"ja_existia": _acrescentar(origem, pred, alvo)}


def criar_ct(cfg: dict, titulo: str, cenario_md: str, reqs: list, wi=None) -> dict:
    if not titulo.strip():
        raise Erro("entrada", "titulo vazio", "titulo")
    factory = oslc.creation_factories(oslc.provider("qm", _qm(cfg)["id"]), oslc.OSLC_QM.TestCase)[0]["creation"]
    # cenário como texto, como no 1.0.43; o link valida já vai na criação
    g = oslc.new_resource(oslc.OSLC_QM.TestCase, {DCTERMS.title: Literal(titulo),
                                                  DCTERMS.description: Literal(cenario_md) if cenario_md else None})
    for req in reqs:
        g.add((URIRef(""), oslc.OSLC_QM.validatesRequirement, URIRef(rm.url_req(cfg, req))))
    novo = oslc.get(oslc.create(factory, g))
    if wi:
        _acrescentar(ewm.wi_url(wi), "oslc_cm:testedByTestCase", novo["url"])
    ct_id = novo["properties"].get("oslc:shortId") or novo["id"]
    return {"id": int(ct_id) if str(ct_id).isdigit() else ct_id, "url": novo["url"]}


def descobrir(trecho: str) -> dict:
    pas = [{"nome": p["title"], "id": p["project_area_uuid"]} for p in oslc.providers("qm")
           if trecho.casefold() in p["title"].casefold()]
    return pas[0] if len(pas) == 1 else {"pas": pas}
