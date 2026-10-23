"""EWM: work items (ler, listar, criar, atualizar, transição, comentário, anexo, horas), iterações e descoberta.

Os campos chegam pelo nome do pa (ccm.tipos.<tipo>.campos) e os valores pelo título; a tradução para o servidor
(attribute, URL da enumeração, categoria do time, iteração, login) é feita aqui.
"""
from __future__ import annotations

import json
import mimetypes
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote

from rdflib import Literal, URIRef
from rdflib.namespace import DCTERMS, RDF, RDFS
from urllib3.filepost import encode_multipart_formdata

from . import oslc, projeto
from .contrato import Erro, erro
from .documento import BRT, document, local_datetime, one_line, to_ewm_html, to_markdown
from .http import ElmHttpError, get_session, get_xml, reportable

WORKITEM = "/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{id}"
# o EWM embute o dcterms:title de cada recurso ligado (estado, iteração, requisito...) na mesma resposta
WITH_TITLES = "?oslc.properties=*%7Bdcterms:title%7D"
CATEGORY = "/ccm/resource/itemOid/com.ibm.team.workitem.Category/{id}"
ITERATION = "/ccm/oslc/iterations/{id}"
JTS_USER = "/jts/users/{login}"
PROJECT_AREA = "/ccm/process/project-areas/{pa}"
CONTRIBUTORS, CONTRIBUTOR_FIELDS = "/ccm/rpt/repository/foundation", "foundation/contributor/(userId|archived)"
CATEGORIES = ("/ccm/rpt/repository/workitem",
              "workitem/category[projectArea/itemId={pa}]/(itemId|name|archived|defaultTeamArea/itemId)")
PLANS = ("/ccm/rpt/repository/apt",
         "apt/iterationPlanRecord[contextId={pa}]/(name|itemId|archived|planType|owner/itemId|iteration/itemId)")
LINKS = {"pai": "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.parent",
         "filhos": "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.children",
         "implementa": "oslc_cm:implementsRequirement",
         "relacionado": "rtc_cm:com.ibm.team.workitem.linktype.relatedworkitem.related"}
ESTIMATE, OWNER, STATE, PLANNED, FILED = (
    "rtc_cm:estimate", "dcterms:contributor", "rtc_cm:state", "rtc_cm:plannedFor", "rtc_cm:filedAgainst")
USERS = {"dcterms:contributor", "dcterms:creator", "rtc_cm:resolvedBy"}
DURATIONS = {"rtc_cm:estimate", "rtc_cm:correctedEstimate", "rtc_cm:timeSpent"}
# preenchidos pelo servidor: não contam como obrigatório faltando na criação
SYSTEM = {"dcterms:type", "dcterms:identifier", "dcterms:creator", "dcterms:created", "dcterms:modified",
          "rtc_cm:projectArea", "rtc_cm:teamArea", "rtc_cm:state", "rtc_cm:type", "process:projectArea",
          "oslc_cmx:project", "oslc:serviceProvider", "rtc_cm:resolution"}
VALUE_TYPES = {"xsd:integer": int, "xsd:int": int, "xsd:long": int, "xsd:double": float, "xsd:float": float,
               "xsd:boolean": lambda v: str(v).lower() in ("true", "sim", "1")}
MS_H = 3_600_000
MAX_HORAS_DIA = 16
# grupo do estado pelo nome, quando o servidor não informa rtc_cm:group
GRUPOS = (("closed", re.compile(r"conclu|resolv|fechad|finaliz|pronto|done|closed|complet|cancel|inv[aá]lid", re.I)),
          ("inprogress", re.compile(r"andamento|progress|desenvolv|execu|implement|trabalh|test|revis|verific", re.I)))
# alvo -> (grupo do estado-alvo, grupos por onde pode passar)
ALVOS = {"assumir": ("inprogress", ()), "concluir": ("closed", ("inprogress",)), "reabrir": ("open", ())}
# "Cancelado"/"Inválido" também são do grupo closed, mas não são conclusão; e assumir é começar, não testar
_DESCARTE = re.compile(r"cancel|inv[aá]lid|rejeit|duplic|descart", re.I)
_INICIO = re.compile(r"andamento|progress|desenvolv|execu|implement|trabalh", re.I)
_DC, _RTC = oslc.clark(DCTERMS), oslc.clark(oslc.RTC_CM)
_ABOUT, _RES = oslc.clark(RDF, "about"), oslc.clark(RDF, "resource")
_JP, _JP06 = oslc.clark(oslc.JP), oslc.clark(oslc.JP06)
# serviços internos da UI web (sem API pública): timesheet, anexos, criar iteração e plano
WORKITEM_REST = "/ccm/service/com.ibm.team.workitem.common.internal.rest.IWorkItemRestService/"
SAVE_PARAMS = ("com.ibm.team.workitem.common.internal.updateExtendedRichText2",
               "com.ibm.team.workitem.common.internal.updateBacklinks")
TIMESHEET = "com.ibm.team.workitem.linktype.timeSheetEntry"
ATTACHMENT_REST = "/ccm/service/com.ibm.team.workitem.service.internal.rest.IAttachmentRestService/"
_UPLOADED = re.compile(r"<textarea>(.*)</textarea>", re.S)
CREATE_ITERATION = "/ccm/service/com.ibm.team.apt.internal.service.rest.IPlanProcessRestService/createIteration"
NO_ITERATION_TYPE = "com.ibm.team.apt.web.ui.internal.iteration.type.none"  # sem ele: NullPointerException
PUT_PLAN = "/ccm/service/com.ibm.team.apt.internal.service.rest.IPlanRestService/putItems"
SPRINT_PLAN, BACKLOG_PLAN = "com.ibm.team.apt.plantype.default", "com.ibm.team.apt.plantype.product.backlog"
# token do guard da UI web; o servidor devolve o atual no WebServiceUsageException
PLAN_GUARD_TOKEN = "_eugWIJstEfGZGNAHniZkPA"
GUARD_ERROR = "com.ibm.team.rtc.common.internal.service.web.guard.WebServiceUsageException"


def _url(template: str, **values) -> str:
    return get_session().url(template.format(**values))


def wi_url(wi_id) -> str:
    if not str(wi_id).isdigit():
        raise Erro("entrada", f"id de work item inválido: {wi_id}", "id")
    return _url(WORKITEM, id=wi_id)


def _first(r: dict, pred: str) -> dict | None:
    return next(iter(r["links"].get(pred, [])), None)


def _title(link: dict | None) -> str | None:
    return (link.get("title") or oslc.title(link["url"])) if link else None


def _login_link(link: dict | None) -> str | None:
    if not link or link["url"].endswith("/unassigned"):
        return None
    return unquote(link["url"].rsplit("/", 1)[-1])


def _wi_id(url: str) -> int | None:
    last = oslc.item_id(url)
    return int(last) if "WorkItem" in url and last and last.isdigit() else None


def _horas(ms) -> float | None:
    return round(ms / MS_H, 2) if isinstance(ms, (int, float)) and ms > 0 else None  # -1 = sem estimativa


# --- usuários

@lru_cache(maxsize=1)
def _logins() -> tuple[str, ...]:
    # shortcut: carrega todos os usuários uma vez por processo; filtrar no servidor se a lista ficar grande
    return tuple(c.findtext("userId") for c in reportable(CONTRIBUTORS, CONTRIBUTOR_FIELDS, "contributor"))


def whoami() -> str:
    """Login do usuário autenticado como o servidor o grava (o elm.properties pode diferir na caixa)."""
    login = get_session().cfg.user.lower()
    found = next((u for u in _logins() if (u or "").lower() == login), None)
    if found is None:
        raise Erro("credencial", f"usuário '{login}' autenticou, mas não está no repositório do ccm")
    return found


def _login(valor: str) -> str:
    return whoami() if valor == "eu" else valor


def user_url(login: str) -> str:
    return _url(JTS_USER, login=quote(login))


# --- tipos, campos e valores

def tipos_servidor(pa_id: str) -> list[dict]:
    """Tipos de work item da PA: [{id, title, type_url, creation, shape}]."""
    svc = oslc.services(oslc.provider("ccm", pa_id))
    cr = str(oslc.OSLC_CM.ChangeRequest)
    return [{"id": oslc.item_id(t), "title": f["title"], "type_url": t, "creation": f["creation"],
             "shape": f["resource_shapes"][0] if f["resource_shapes"] else None}
            for f in svc["creation_factories"] if cr in f["resource_types"] for t in f["resource_types"] if t != cr]


def _tipo_servidor(cfg: dict, type_id: str) -> dict:
    found = next((t for t in tipos_servidor(cfg["ccm"]["id"]) if t["id"] == type_id), None)
    if found is None:
        raise Erro("config", f"tipo '{type_id}' não existe na PA {cfg['ccm']['nome']}", "ccm.tipos")
    return found


def _props(cfg: dict, type_id: str) -> dict[str, dict]:
    shape = _tipo_servidor(cfg, type_id)["shape"]
    return {p["predicate"]: p for p in oslc.shape(shape)["properties"]} if shape else {}


def _tipo_cfg(cfg: dict, chave: str) -> dict:
    tipo = cfg["ccm"]["tipos"].get(chave)
    if tipo is None:
        raise Erro("config", f"tipo '{chave}' não está no pa", f"ccm.tipos.{chave}")
    return tipo


def _tipo_de(cfg: dict, r: dict) -> tuple[str | None, dict | None]:
    """(chave do pa, tipo do pa) do work item; (id do servidor, None) se o pa não tem o tipo."""
    link = _first(r, "rtc_cm:type")
    type_id = oslc.item_id(link["url"]) if link else r["properties"].get("dcterms:type")
    chave = next((k for k, t in cfg["ccm"]["tipos"].items() if t["id"] == type_id), None)
    return (chave, cfg["ccm"]["tipos"][chave]) if chave else (type_id, None)


def _links_cfg(cfg: dict) -> dict[str, str]:
    return {**LINKS, **cfg["ccm"].get("links", {})}


def enumeracao(prop: dict) -> list[dict] | None:
    """Valores permitidos de um campo: [{url, title}]; None se o campo não for de lista."""
    enum = next((r for r in prop["range"] if "/oslc/enumerations/" in r), None)
    if enum:
        allowed = {oslc.item_id(u) for u in prop["allowed_values"]}
        g = oslc._cached(enum)
        return [{"url": str(s), "title": oslc.text(t)} for s, t in g.subject_objects(DCTERMS.title)
                if str(s) != enum and (not allowed or oslc.item_id(str(s)) in allowed)]
    if prop["allowed_values"]:
        return [{"url": u, "title": oslc.title(u) or oslc.item_id(u)} for u in prop["allowed_values"]]
    return oslc.allowed_values(prop["allowed_values_url"]) if prop["allowed_values_url"] else None


def iteracao_id(cfg: dict, nome: str) -> str:
    """Iteração pelo nome do cache do pa, pelo nome no servidor ou pelo itemId ('_...')."""
    if nome.startswith("_"):
        return nome
    if found := cfg["ccm"].get("iteracoes", {}).get(nome):
        return found["id"]
    found = [i for i in iteracoes(cfg["ccm"]["id"]) if i["nome"].casefold() == nome.casefold()]
    if len(found) != 1:
        raise Erro("nao_encontrado" if not found else "entrada",
                   f"iteração '{nome}' {'não existe' if not found else 'é ambígua'} na PA", "iteracao")
    return found[0]["id"]


def _no(cfg: dict, props: dict, pred: str, valor, campo: str):
    """Valor do pa ou da chamada (título, texto, número, horas) -> nó RDF do servidor."""
    s = str(valor)
    if pred == FILED:  # categoria: só a do time do pa
        cat = cfg["ccm"]["time"]["categoria"]
        if s not in (cat["nome"], cat["id"]):
            raise Erro("entrada", f"categoria '{s}' não é a do time ({cat['nome']})", campo)
        return URIRef(_url(CATEGORY, id=cat["id"]))
    if pred == PLANNED:
        return URIRef(_url(ITERATION, id=iteracao_id(cfg, s)))
    if pred in USERS:
        return URIRef(user_url(_login(s)))
    if pred in DURATIONS:
        return Literal(int(round(float(valor) * MS_H)))
    prop = props.get(pred)
    opcoes = enumeracao(prop) if prop else None
    if opcoes is not None:
        found = next((o for o in opcoes if (o["title"] or "").casefold() == s.casefold()), None)
        if found is None:  # o pa pode trazer o começo do título ("Desenvolvimento (Transacional / …)"): só se único
            inicio = [o for o in opcoes if (o["title"] or "").casefold().startswith(s.casefold())]
            found = inicio[0] if len(inicio) == 1 else None
        if found is None:
            raise Erro("entrada", f"'{s}' não é valor de {campo}: {', '.join(o['title'] or '' for o in opcoes)}", campo)
        return URIRef(found["url"])
    value_type = prop["value_type"] if prop else ""
    if value_type == "rdf:XMLLiteral":
        return Literal(to_ewm_html(s), datatype=RDF.XMLLiteral)
    return Literal(VALUE_TYPES[value_type](valor) if value_type in VALUE_TYPES else valor)


def _campos(cfg: dict, tipo: dict, props: dict, campos: dict) -> dict:
    """{nome do pa: valor} -> {attribute: nó}."""
    out = {}
    for nome, valor in campos.items():
        attr = tipo["campos"].get(nome)
        if attr is None:
            raise Erro("entrada", f"campo '{nome}' não está no pa ({', '.join(tipo['campos'])})", nome)
        pred = oslc.qname(oslc.expand(attr))
        out[pred] = _no(cfg, props, pred, valor, nome)
    return out


def _comuns(cfg: dict, props: dict, titulo, descricao_md, pai, iteracao, dono, estimativa_h) -> dict:
    out = {}
    if titulo:
        out["dcterms:title"] = Literal(titulo)
    if descricao_md is not None:
        out["dcterms:description"] = Literal(to_ewm_html(descricao_md), datatype=RDF.XMLLiteral)
    if pai:
        out[_links_cfg(cfg)["pai"]] = URIRef(wi_url(pai))  # com o id puro o servidor responde 400
    for pred, valor, campo in ((PLANNED, iteracao, "iteracao"), (OWNER, dono, "dono"),
                               (ESTIMATE, estimativa_h, "estimativa_h")):
        if valor is not None and valor != "":
            out[pred] = _no(cfg, props, pred, valor, campo)
    return out


# --- leitura e listagem

def _ler(wi_id) -> tuple[dict, str]:
    """(recurso com títulos dos links, XHTML da descrição) num GET só."""
    url = wi_url(wi_id)
    g, _, _ = oslc.fetch(url + WITH_TITLES)
    subject = URIRef(url) if (URIRef(url), None, None) in g else next(g.subjects(DCTERMS.identifier, None), URIRef(url))
    desc = g.value(subject, DCTERMS.description)
    return {**oslc.resource(g, subject), "url": url}, str(desc) if desc is not None else ""


def _rotulo(link: dict, pred: str = ""):
    """Link -> login (pessoa), id (work item), título ou URL."""
    url = link["url"]
    if "/jts/users/" in url or pred in USERS:
        return _login_link(link)
    return _wi_id(url) or one_line(link.get("title")) or oslc.title(url) or url


def _valor(r: dict, attr: str):
    key = oslc.qname(oslc.expand(attr))
    if key in r["links"]:
        values = [v for link in r["links"][key] if (v := _rotulo(link, key)) is not None]
        return values[0] if len(values) == 1 else values or None
    v = r["properties"].get(key)
    return _horas(v) if key in DURATIONS else v


def _padroes(t: dict, titulo: str) -> dict:
    """Padrões do tipo, depois os do prefixo do título."""
    valores = dict(t.get("padroes", {}))
    for prefixo, extra in t.get("por-prefixo", {}).items():
        if titulo.startswith(prefixo):
            valores.update(extra)
    return valores



def _comentarios(r: dict, n: int) -> list[str]:
    url = next((link["url"] for pred, links in r["links"].items()
                if pred == "oslc:discussedBy" or pred.endswith("comments") for link in links), None)
    if not url:
        return []
    g = oslc.fetch(url)[0]
    found = sorted((oslc.resource(g, c) for c in set(g.objects(None, oslc.OSLC.comment)) | set(g.objects(None, RDFS.member))),
                   key=lambda c: str(c["properties"].get("dcterms:created")))
    out = []
    for c in found[-n:] if n > 0 else found:
        autor = _login_link(_first(c, "dcterms:creator")) or "?"
        out.append(f"**{autor} · {local_datetime(c['properties'].get('dcterms:created'))}**\n\n"
                   f"{c['properties'].get('dcterms:description') or ''}".strip())
    return out


def ler_wi(cfg: dict, wi_id, comentarios: int = 3) -> str:
    r, desc = _ler(wi_id)
    chave, tipo = _tipo_de(cfg, r)
    campos = {nome: v for nome, attr in (tipo or {}).get("campos", {}).items() if (v := _valor(r, attr)) not in (None, "", [])}
    links = {}
    for nome, attr in _links_cfg(cfg).items():
        values = [v for link in r["links"].get(oslc.qname(oslc.expand(attr)), []) if (v := _rotulo(link))]
        if values:
            links[nome] = values[0] if nome == "pai" else values
    head = {"id": int(r["id"]), "tipo": chave, "titulo": one_line(r["title"]), "estado": _title(_first(r, STATE)),
            "dono": _login_link(_first(r, OWNER)), "pai": links.get("pai"), "iteracao": _title(_first(r, PLANNED)),
            "estimativa_h": _horas(r["properties"].get(ESTIMATE)), "campos": campos or None, "links": links or None,
            "url": r["url"]}
    body = to_markdown(desc) or "*(sem descrição)*"
    if comentarios:
        body += "\n\n## Comentários\n\n" + ("\n\n".join(_comentarios(r, comentarios)) or "*(nenhum)*")
    return document(head, body)


SUMMARY = ("dcterms:identifier,dcterms:title,dcterms:type,rtc_cm:type,rtc_cm:state{dcterms:title},"
           "dcterms:contributor")


def _consulta(cfg: dict, clauses: list[str]) -> list[dict]:
    base = oslc.query_base(oslc.provider("ccm", cfg["ccm"]["id"]), oslc.OSLC_CM.ChangeRequest)
    return oslc.query(base, where=" and ".join(clauses), select=SUMMARY, limit=oslc.MAX_LIMIT)


def listar_wi(cfg: dict, iteracao=None, dono=None, tipo=None, pai=None, titulo_prefixo=None, estado=None,
              limite: int = 50) -> dict:
    if not (iteracao or dono or pai):
        raise Erro("entrada", "informe ao menos iteracao, dono ou pai", "iteracao")
    clauses = []
    if iteracao:
        clauses.append(oslc.where_eq(PLANNED, _url(ITERATION, id=iteracao_id(cfg, iteracao))))
    if dono:
        clauses.append(oslc.where_eq(OWNER, user_url(_login(dono))))
    if tipo:
        clauses.append(oslc.where_eq("dcterms:type", _tipo_cfg(cfg, tipo)["id"]))
    if pai:  # filhos pelo link do pai: o oslc.where do EWM não filtra por link
        r = oslc.get(wi_url(pai))
        filhos = oslc.qname(oslc.expand(_links_cfg(cfg)["filhos"]))
        ids = [i for link in r["links"].get(filhos, []) if (i := _wi_id(link["url"]))]
        if not ids:
            return {"itens": [], "truncado": False, "total": 0}
        clauses.append(oslc.where_in("dcterms:identifier", [str(i) for i in ids]))
    itens = []
    for r in _consulta(cfg, clauses):
        item = {"id": int(r["id"]), "tipo": _tipo_de(cfg, r)[0], "titulo": one_line(r["title"]),
                "estado": _title(_first(r, STATE)), "dono": _login_link(_first(r, OWNER))}
        if (not titulo_prefixo or (item["titulo"] or "").startswith(titulo_prefixo)) and (
                not estado or (item["estado"] or "").casefold() == estado.casefold()):
            itens.append(item)
    return {"itens": itens[:limite], "truncado": len(itens) > limite, "total": len(itens)}


# --- criação e atualização

def criar_wi(cfg: dict, tipo: str, titulo: str, pai=None, dono=None, estimativa_h=None, iteracao=None,
             descricao_md=None, campos: dict | None = None) -> dict:
    t = _tipo_cfg(cfg, tipo)
    valores = {**_padroes(t, titulo), **(campos or {})}  # padrões, depois o do prefixo do título, depois o da chamada
    # a PA exige alguns campos só depois (iniciar, lançar horas…), sem o tipo marcar: trava aqui, na criação,
    # para nenhuma transição ou lançamento depois esbarrar neles
    for campo in t.get("obrigatorios", []):
        pred = oslc.qname(oslc.expand(t["campos"].get(campo, "")))
        if campo not in valores and not (pred in DURATIONS and estimativa_h is not None):
            raise Erro("precondicao", f"obrigatório no pa sem valor: {campo}", campo)
    servidor = _tipo_servidor(cfg, t["id"])
    props = _props(cfg, t["id"])
    attrs = {**_campos(cfg, t, props, valores),
             **_comuns(cfg, props, titulo, descricao_md, pai, iteracao, dono, estimativa_h)}
    usados = dict(valores)
    if FILED not in attrs:
        attrs[FILED] = _no(cfg, props, FILED, cfg["ccm"]["time"]["categoria"]["id"], "categoria")
        usados["categoria"] = cfg["ccm"]["time"]["categoria"]["nome"]
    nomes = {oslc.qname(oslc.expand(a)): n for n, a in t["campos"].items()}
    faltam = [p for pred, p in props.items() if p["required"] and not p["read_only"] and not p["default"]
              and pred not in attrs and pred not in SYSTEM and ".linktype." not in pred]
    if faltam:
        campo = nomes.get(faltam[0]["predicate"]) or faltam[0]["title"] or faltam[0]["predicate"]
        raise Erro("precondicao", f"obrigatório sem valor no pa nem na chamada: {campo}", campo)
    g = oslc.new_resource(oslc.OSLC_CM.ChangeRequest, {DCTERMS.type: URIRef(servidor["type_url"])})
    for pred, node in attrs.items():
        g.add((URIRef(""), URIRef(oslc.expand(pred)), node))
    url = oslc.create(servidor["creation"], g)
    return {"id": int(oslc.get(url)["id"]), "url": url,
            "valores_usados": {**usados, **{k: v for k, v in (("dono", dono), ("iteracao", iteracao),
                                                               ("estimativa_h", estimativa_h)) if v is not None}}}


def atualizar_wi(cfg: dict, ids: list, titulo=None, descricao_md=None, pai=None, iteracao=None, dono=None,
                 estimativa_h=None, campos: dict | None = None) -> dict:
    if not ids:
        raise Erro("entrada", "informe ao menos um id", "ids")
    if titulo is None and descricao_md is None and not (pai or iteracao or dono or campos) and estimativa_h is None:
        raise Erro("entrada", "nada a atualizar", "campos")

    def mudancas(r: dict) -> dict:
        _, tipo = _tipo_de(cfg, r)
        if campos and tipo is None:
            raise Erro("config", f"tipo do work item {r['id']} não está no pa", "ccm.tipos")
        props = _props(cfg, tipo["id"]) if tipo else {}
        return {**(_campos(cfg, tipo, props, campos) if campos else {}),
                **_comuns(cfg, props, titulo, descricao_md, pai, iteracao, dono, estimativa_h)}

    atualizados, falhas = [], []
    for wi_id in ids:
        try:
            oslc.update(wi_url(wi_id), mudancas)
            atualizados.append(int(wi_id))
        except Exception as exc:
            if (e := erro(exc)) is None:
                raise
            falhas.append({"id": wi_id, "codigo": e.codigo, "detalhe": e.detalhe})
    return {"atualizados": atualizados, "falhas": falhas}


# --- transições

@lru_cache(maxsize=32)
def _workflow(state_url: str) -> tuple[dict, list]:
    """({url do estado: {titulo, grupo}}, [{id, titulo, resultado}]) do workflow do estado."""
    base = state_url.rsplit("/", 1)[0]
    estados = {}
    for el in get_xml(base, oslc_v1=True).iter():  # formato 1.0 (rtc_cm:Status) ou RDF (rdf:Description)
        url, titulo = el.get(_RES) or el.get(_ABOUT), el.findtext(_DC + "title")
        if url and titulo and url != base:
            grupo = next((c.text for c in el if c.tag.endswith("}group") and c.text), None)
            estados[url] = {"titulo": titulo, "grupo": (grupo or "").lower().replace(" ", "") or next(
                (g for g, rx in GRUPOS if rx.search(titulo)), "open")}
    acoes = []
    # título e estado resultante das ações só vêm no formato OSLC CM 1.0
    for a in get_xml(base.replace("/states/", "/actions/"), oslc_v1=True).findall(_RTC + "Action"):
        result = a.find(_RTC + "resultState")
        acoes.append({"id": a.findtext(_DC + "identifier"), "titulo": a.findtext(_DC + "title"),
                      "resultado": result.get(_RES) if result is not None else None})
    return estados, acoes


def _grupo(r: dict, estados: dict) -> str:
    props = r["properties"]
    # só a flag closed vale: o EWM manda oslc_cm:inprogress=true também em "Novo" (lá ela quer dizer "não fechado")
    if str(props.get("oslc_cm:closed")).lower() == "true":
        return "closed"
    return estados.get(_first(r, STATE)["url"], {}).get("grupo", "open")


def _ir(url: str, r: dict, alvo: str, attrs: dict) -> tuple[str, bool]:
    """Percorre as ações do workflow até um estado do grupo-alvo. (título do estado, já estava)."""
    grupo_alvo, meios = ALVOS[alvo]
    state = _first(r, STATE)
    if state is None:
        raise Erro("precondicao", f"work item {r['id']} sem rtc_cm:state", "estado")
    estados, acoes = _workflow(state["url"])
    titulo = lambda u: estados.get(u, {}).get("titulo") or oslc.item_id(u)  # noqa: E731
    if _grupo(r, estados) == grupo_alvo:
        if attrs:
            oslc.update(url, attrs)
        return titulo(state["url"]), True
    atual, primeiro_erro = state["url"], None
    for _ in range(4):
        # o workflow não diz de onde cada ação parte: tenta as que levam ao alvo (depois as de passagem) até o
        # estado mudar; o servidor aceita em silêncio, sem mudar nada, uma ação que não sai do estado atual
        candidatas = sorted((a for a in acoes if a["resultado"] != atual
                             and estados.get(a["resultado"], {}).get("grupo") in (grupo_alvo, *meios)
                             and not _DESCARTE.search(titulo(a["resultado"]))),
                            key=lambda a: (estados[a["resultado"]]["grupo"] != grupo_alvo,
                                           not _INICIO.search(titulo(a["resultado"]))))
        for acao in candidatas:
            try:
                oslc.update(url, attrs, params={"_action": acao["id"]})
            except ElmHttpError as exc:
                primeiro_erro = primeiro_erro or exc
                continue
            novo = _first(oslc.get(url), STATE)["url"]
            if novo != atual:
                atual, attrs = novo, {}
                break
        else:
            if primeiro_erro:  # a recusada é a que sai do estado atual: o motivo (atributo) vem nela
                raise primeiro_erro
            raise Erro("precondicao", f"nenhuma ação leva de '{titulo(atual)}' a {alvo}", "estado")
        if estados.get(atual, {}).get("grupo") == grupo_alvo:
            return titulo(atual), False
    raise Erro("precondicao", f"não chegou a {alvo}: parou em '{titulo(atual)}'", "estado")


def transicao(cfg: dict, wi_id, alvo: str, estimativa_h=None, forcar: bool = False, iniciar_pai: bool = False) -> dict:
    if alvo not in ALVOS:
        raise Erro("entrada", f"alvo inválido: {alvo} (assumir, concluir, reabrir)", "alvo")
    url = wi_url(wi_id)
    r = oslc.get(url + WITH_TITLES)
    attrs = {ESTIMATE: Literal(int(round(estimativa_h * MS_H)))} if estimativa_h is not None else {}
    dono, pai, pai_aberto = _login_link(_first(r, OWNER)), None, False
    if alvo == "assumir":
        eu = whoami()
        if dono and dono != eu and not forcar:
            raise Erro("conflito", dono, "dono")
        if _first(r, STATE) and _grupo(r, _workflow(_first(r, STATE)["url"])[0]) == "closed":
            raise Erro("precondicao", f"{wi_id} já concluída ({_title(_first(r, STATE))}): reabra antes", "estado")
        if dono != eu:
            attrs[OWNER] = URIRef(user_url(eu))
            dono = eu
        if iniciar_pai and (link := _first(r, oslc.qname(oslc.expand(_links_cfg(cfg)["pai"])))):
            pai = oslc.get(link["url"] + WITH_TITLES)
            pai_aberto = _grupo(pai, _workflow(_first(pai, STATE)["url"])[0]) == "open"
            if pai_aberto and _login_link(_first(pai, OWNER)) is None:  # confere antes de mexer na tarefa
                raise Erro("precondicao", f"pai {pai['id']} sem dono", "dono do pai")
    estado, ja = _ir(url, r, alvo, attrs)
    out = {"id": int(wi_id), "estado": estado, "dono": dono, "ja_estava": ja and not attrs}
    if pai is not None:
        out["pai"] = {"id": int(pai["id"]), "estado": _ir(wi_url(pai["id"]), pai, "assumir", {})[0] if pai_aberto
                      else _title(_first(pai, STATE))}
    return out


# --- comentário

def comentar(cfg: dict, wi_id, texto_md: str) -> dict:
    r = oslc.get(wi_url(wi_id))
    url = next((link["url"] for pred, links in r["links"].items()
                if pred == "oslc:discussedBy" or pred.endswith("comments") for link in links), None)
    if url is None:
        raise Erro("nao_encontrado", f"work item {wi_id} não expõe comentários", "id")
    # shortcut: texto puro como no 1.0.43 (Markdown aparece literal na UI); converter se o time pedir formatação
    oslc.create(f"{url}/oslc:comment", oslc.new_resource(oslc.OSLC.Comment, {DCTERMS.description: Literal(texto_md)}))
    return {"id": int(wi_id)}


# --- serviços internos da UI web (timesheet, anexos)

def _envelope(body: dict) -> dict:
    try:
        return body["soapenv:Body"]["response"]["returnValue"]["value"]
    except (KeyError, TypeError):
        return body


def _dto(wi_id) -> dict:
    """Work item pelo workItemDTO2 da UI web: itemId, tipo, estado, PA, timesheet e anexos."""
    if not str(wi_id).isdigit():
        raise Erro("entrada", f"id de work item inválido: {wi_id}", "id")
    session = get_session()
    resp = session.request("GET", WORKITEM_REST + "workItemDTO2", headers=session.ui_headers(),
                           params={"id": str(wi_id), "includeAttributes": "true", "includeLinks": "true"})
    try:
        dto = _envelope(resp.json())
    except ValueError as exc:
        raise Erro("nao_encontrado", f"work item {wi_id}: resposta não é JSON") from exc
    dto = dto.get("workItem", dto)
    attrs = {a.get("key"): a.get("value") for a in dto.get("attributes", [])}
    link_types = dto.get("linkTypes", [])
    targets = lambda pred: [(link, link.get("target") or {}) for lt in link_types if pred(lt)  # noqa: E731
                            for link in lt.get("linkDTOs", [])]
    spent = attrs.get("timeSpent")
    return {
        "item_id": dto.get("itemId"), "estado_id": dto.get("stateId"),
        "tipo": (attrs.get("workItemType") or {}).get("id"),
        "pa": dto.get("projectAreaItemId") or (attrs.get("projectArea") or {}).get("itemId"),
        "config_state": dto.get("projectAreaConfigurationStateId"),
        "horas_ms": int(spent.get("id", 0)) if isinstance(spent, dict) else 0,
        "lancamentos": [{"data": _dia(t.get("startDate")), "ms": t.get("timeSpent") or 0,
                         "pessoa": (t.get("creator") or {}).get("userId"), "link": link.get("itemId")}
                        for link, t in targets(lambda lt: lt.get("id") == TIMESHEET)],
        "anexos": [{"id": t.get("id"), "uuid": t.get("itemId"), "nome": t.get("name"), "tamanho": t.get("contentLength"),
                    "url": link.get("url")} for link, t in targets(lambda lt: lt.get("endpointId") == "attachment")],
    }


def _dia(value: str | None) -> str | None:
    """'2026-10-01T12:00:00.000Z' -> '2026-10-01' em Brasília (cortar a string erra o dia)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(BRT).date().isoformat() if value else None


def _salvar(dto: dict, form: list) -> None:
    """postWorkItem2 da UI web; erro do servidor (severity >= 4) vira precondição."""
    session = get_session()
    base = [("itemId", dto["item_id"]), ("type", dto["tipo"]), ("stateId", dto["estado_id"]),
            ("projectAreaItemId", dto["pa"]), ("sanitizeHTML", "true"),
            *((("projectAreaConfigurationStateId", dto["config_state"]),) if dto["config_state"] else ()),
            *(("additionalSaveParameters", p) for p in SAVE_PARAMS)]
    resp = session.request("POST", WORKITEM_REST + "workItem2", data=base + form, headers=session.ui_headers(),
                           ok=tuple(range(200, 600)))
    try:
        result = _envelope(resp.json())
    except ValueError:
        raise ElmHttpError(resp)
    if result.get("severity", 0) >= 4:
        detalhes = "; ".join(i.get("summary") or i.get("description", "") for i in result.get("infos", []) if i)
        raise Erro("precondicao", f"{result.get('message', 'recusado')}: {detalhes}" if detalhes
                   else result.get("message", "recusado"))
    if not resp.ok:
        raise ElmHttpError(resp)


def anexo(cfg: dict, wi_id, acao: str, caminho: str | None = None) -> dict:
    raiz = Path(cfg["_projeto"])
    if acao == "listar":
        return {"anexos": [{"nome": a["nome"], "tamanho": a["tamanho"]} for a in _dto(wi_id)["anexos"]]}
    if not caminho:
        raise Erro("entrada", "caminho é obrigatório para enviar e baixar", "caminho")
    alvo = (raiz / caminho).resolve()
    if acao == "enviar":
        if not alvo.is_file():
            raise Erro("entrada", f"arquivo não encontrado: {caminho}", "caminho")
        dto = _dto(wi_id)
        tipo = mimetypes.guess_type(alvo.name)[0] or "application/octet-stream"
        body, multipart = encode_multipart_formdata({"attach": (alvo.name, alvo.read_bytes(), tipo)})
        session = get_session()
        resp = session.request("POST", ATTACHMENT_REST, data=body, params={"projectId": dto["pa"], "multiple": "true"},
                               headers={**session.ui_headers(), "Content-Type": multipart})
        if not (m := _UPLOADED.search(resp.text)):  # responde HTML com o JSON dentro de <textarea>
            raise Erro("precondicao", f"upload do anexo recusado: {resp.text}")
        novo = json.loads(m.group(1))["files"][0]
        # anexo de mesmo nome é substituído: fica só a última versão
        link = {"type": "com.ibm.team.workitem.linktype.attachment", "end": "attachment", "name": alvo.name, "comment": ""}
        _salvar(dto, [("updateLinks", json.dumps({**link, "cmd": "addLink", "itemId": novo["uuid"]})),
                      *(("updateLinks", json.dumps({**link, "cmd": "removeLink", "itemId": a["uuid"]}))
                        for a in dto["anexos"] if a["nome"] == alvo.name)])
        return {"nome": novo["name"]}
    if acao == "baixar":
        if raiz.resolve() not in alvo.parents:
            raise Erro("entrada", "caminho precisa ficar dentro do projeto", "caminho")
        found = sorted((a for a in _dto(wi_id)["anexos"] if a["nome"] == alvo.name), key=lambda a: a["id"] or 0)
        if not found:
            raise Erro("nao_encontrado", f"work item {wi_id} não tem anexo '{alvo.name}'", "caminho")
        content = get_session().request("GET", found[-1]["url"], headers={"Accept": "*/*"}).content
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(content)
        return {"caminho": str(alvo.relative_to(raiz.resolve()))}
    raise Erro("entrada", f"acao inválida: {acao} (listar, enviar, baixar)", "acao")


# --- horas

def horas(cfg: dict, wi_id=None, iteracao=None) -> dict:
    if bool(wi_id) == bool(iteracao):
        raise Erro("entrada", "informe id ou iteracao (um dos dois)", "id")
    if wi_id:
        dto = _dto(wi_id)
        lanc = sorted(({"data": e["data"], "horas": round(e["ms"] / MS_H, 2), "pessoa": e["pessoa"]}
                       for e in dto["lancamentos"]), key=lambda e: e["data"] or "")
        total = dto["horas_ms"] if dto["horas_ms"] > 0 else sum(e["ms"] for e in dto["lancamentos"])
        return {"lancamentos": lanc, "total_h": round(total / MS_H, 2)}
    tarefas = _consulta(cfg, [oslc.where_eq(PLANNED, _url(ITERATION, id=iteracao_id(cfg, iteracao))),
                              oslc.where_eq("dcterms:type", _tipo_cfg(cfg, "tarefa")["id"])])
    with ThreadPoolExecutor(8) as pool:  # o tempo é latência do servidor, não CPU
        dtos = list(pool.map(lambda r: _dto(r["id"]), tarefas))
    linhas = []
    for r, dto in zip(tarefas, dtos):
        prefixo = m.group(0) if (m := re.match(r"\[[^\]]+\]", r["title"] or "")) else None
        por_pessoa: dict = {}
        for e in dto["lancamentos"]:
            por_pessoa[e["pessoa"]] = por_pessoa.get(e["pessoa"], 0) + e["ms"]
        if not por_pessoa:  # tarefa sem horas também aparece (relatório de concluídas sem lançamento)
            por_pessoa[_login_link(_first(r, OWNER))] = 0
        linhas += [{"tarefa_id": int(r["id"]), "prefixo": prefixo, "pessoa": p, "horas": round(ms / MS_H, 2)}
                   for p, ms in por_pessoa.items()]
    return {"linhas": linhas, "total_h": round(sum(linha["horas"] for linha in linhas), 2)}


def lancar_horas(cfg: dict, wi_id, horas_: float, data: str | None = None) -> dict:
    if not 0 < horas_ <= MAX_HORAS_DIA:
        raise Erro("entrada", f"horas entre 0 e {MAX_HORAS_DIA}", "horas")
    try:
        dia = date.fromisoformat(data) if data else date.today()
    except ValueError as exc:
        raise Erro("entrada", f"data inválida (AAAA-MM-DD): {data}", "data") from exc
    if dia > date.today():
        raise Erro("entrada", f"data futura: {dia.isoformat()}", "data")
    eu = whoami()
    r = oslc.get(wi_url(wi_id))
    dono = _login_link(_first(r, OWNER))
    if dono != eu:
        raise Erro("entrada", f"só o dono lança horas (dono: {dono or 'ninguém'})", "dono")
    dto = _dto(wi_id)
    no_dia = sum(e["ms"] for e in dto["lancamentos"] if e["data"] == dia.isoformat() and e["pessoa"] == eu) / MS_H
    if no_dia + horas_ > MAX_HORAS_DIA:
        raise Erro("entrada", f"passa de {MAX_HORAS_DIA}h em {dia.isoformat()} (já há {no_dia:g}h)", "horas")
    ms = int(round(horas_ * MS_H))
    total = sum(e["ms"] for e in dto["lancamentos"]) + ms
    entrada = {"cmd": "addLink", "type": TIMESHEET, "end": "target", "name": "Entrada da Planilha de horas",
               # meio-dia UTC: o dia não muda com o fuso
               "item": {"startDate": f"{dia.isoformat()}T12:00:00.000Z", "timeSpent": ms,
                        "workType": cfg["ccm"]["horas"]["work-type"], "timeCode": cfg["ccm"]["horas"]["time-code"],
                        "creator": {"userId": eu}}}
    _salvar(dto, [("attributeIdentifiers", "timeSpent"), ("attributeValues", str(total)),
                  ("updateLinks", json.dumps(entrada))])
    return {"lancado": {"data": dia.isoformat(), "horas": horas_}, "total_h": round(total / MS_H, 2)}


def remover_horas(cfg: dict, wi_id, data: str | None = None) -> dict:
    """Remove os lançamentos de quem chama numa data (corrigir um lançamento errado)."""
    try:
        dia = (date.fromisoformat(data) if data else date.today()).isoformat()
    except ValueError as exc:
        raise Erro("entrada", f"data inválida (AAAA-MM-DD): {data}", "data") from exc
    eu, dto = whoami(), _dto(wi_id)
    meus = [e for e in dto["lancamentos"] if e["data"] == dia and e["pessoa"] == eu]
    if not meus:
        raise Erro("nao_encontrado", f"nenhum lançamento seu em {dia} no {wi_id}", "data")
    total = sum(e["ms"] for e in dto["lancamentos"]) - sum(e["ms"] for e in meus)
    form = [("attributeIdentifiers", "timeSpent"), ("attributeValues", str(total))]
    form += [("updateLinks", json.dumps({"cmd": "removeLink", "type": TIMESHEET, "end": "target", "itemId": e["link"]}))
             for e in meus]
    _salvar(dto, form)
    return {"removidos": [{"data": dia, "horas": round(e["ms"] / MS_H, 2)} for e in meus],
            "total_h": round(total / MS_H, 2)}


# --- iterações e planos

def _iteracoes_xml(url: str | None, pai: str | None) -> list[dict]:
    if not url:
        return []
    out = []
    for it in get_xml(url).findall(_JP + "iteration"):
        item = {"nome": it.findtext(_JP + "label"), "id": oslc.item_id(it.findtext(_JP + "url")), "pai": pai}
        out += [item, *_iteracoes_xml(it.findtext(_JP + "childern-iterations-url"), item["id"])]  # sic: grafia do servidor
    return out


def iteracoes(pa_id: str) -> list[dict]:
    """Iterações de todas as timelines em lista plana: [{nome, id, pai}]."""
    root = get_xml(_url(PROJECT_AREA, pa=pa_id) + "/timelines")
    return [i for t in root.findall(_JP + "timeline") for i in _iteracoes_xml(t.findtext(_JP + "iterations-url"), None)]


def planos(pa_id: str) -> list[dict]:
    """Planos não arquivados: [{nome, id, tipo, dono, iteracao}]."""
    return [{"nome": r.findtext("name"), "id": r.findtext("itemId"), "tipo": r.findtext("planType"),
             "dono": r.findtext("owner/itemId"), "iteracao": r.findtext("iteration/itemId")}
            for r in reportable(PLANS[0], PLANS[1].format(pa=pa_id), "iterationPlanRecord")
            if r.findtext("archived") != "true"]


def _ui_post(path: str, data: dict, guarded: bool = False) -> None:
    """POST num serviço interno da UI web. `guarded`: manda o token `_t` e repete uma vez com o token novo se o
    servidor recusar. Chame depois de uma leitura no /ccm (garante login e cookie)."""
    session = get_session()
    token = PLAN_GUARD_TOKEN if guarded else None
    for _ in range(2):
        resp = session.request("POST", path, data={**data, **({"_t": token} if token else {})},
                               headers=session.ui_headers(), ok=tuple(range(200, 600)))
        if resp.ok:
            return
        try:
            error = resp.json()
        except ValueError:
            break
        token = (error.get("errorData") or {}).get("token") if error.get("errorClass") == GUARD_ERROR else None
        if not token:
            break
    raise ElmHttpError(resp)


def _epoch_ms(day: str, end: bool = False) -> int:
    """'2026-10-01' -> início (ou fim, 23:59) do dia em Brasília, em ms (formato da UI web)."""
    try:
        moment = datetime.combine(date.fromisoformat(day), time(23, 59) if end else time(0), BRT)
    except ValueError as exc:
        raise Erro("entrada", f"data inválida (AAAA-MM-DD): {day}", "fim" if end else "inicio") from exc
    return int(moment.timestamp() * 1000)


def iteracao(cfg: dict, nome: str, inicio: str | None = None, fim: str | None = None) -> dict:
    pa_id, time_ = cfg["ccm"]["id"], cfg["ccm"]["time"]
    todas = iteracoes(pa_id)
    found = [i for i in todas if (i["nome"] or "").casefold() == nome.casefold()]
    if len(found) > 1:
        raise Erro("entrada", f"iteração '{nome}' é ambígua na PA", "nome")
    criados = []
    if not found:
        if not inicio:
            raise Erro("entrada", "iteração nova precisa de inicio (AAAA-MM-DD)", "inicio")
        # nasce irmã da última iteração do cache do pa (mesmo pai: a release/entrega)
        por_id = {i["id"]: i for i in todas}
        ref = next((por_id[c["id"]] for c in reversed(list(cfg["ccm"].get("iteracoes", {}).values()))
                    if c["id"] in por_id and por_id[c["id"]]["pai"]), None)
        if ref is None:
            raise Erro("precondicao", "sem iteração de referência com pai no cache do pa", "ccm.iteracoes")
        payload = {"id": nome, "name": nome, "startDateTime": _epoch_ms(inicio), "hasDeliverable": True,
                   "parentIterationId": ref["pai"], "iterationTypeItemId": NO_ITERATION_TYPE,
                   **({"endDateTime": _epoch_ms(fim, end=True)} if fim else {})}
        _ui_post(CREATE_ITERATION, {"jsonObject": json.dumps(payload)})
        found = [i for i in iteracoes(pa_id) if (i["nome"] or "").casefold() == nome.casefold()]
        if not found:
            raise Erro("precondicao", f"o servidor aceitou, mas a iteração '{nome}' não apareceu", "nome")
        criados.append(nome)
    it = found[0]
    do_time = lambda: [p for p in planos(pa_id) if p["iteracao"] == it["id"] and p["dono"] == time_["id"]]  # noqa: E731
    tipos = {p["tipo"] for p in do_time()}
    for tipo, nome_plano in ((BACKLOG_PLAN, f"Backlog {time_['nome']}"), (SPRINT_PLAN, f"{nome} - {time_['nome']}")):
        if tipo in tipos:
            continue
        # handles da UI: "<tipo do item>;<uuid>" (16 plano, 8 tipo de plano, 35 iteração, 39 time, 40 PA)
        record = {"itemId": "__new_1", "itemType": "item:com.ibm.team.apt:IterationPlanRecord",
                  "planType": f"8;{pa_id}/{tipo}", "projectArea": f"40;{pa_id}", "label": nome_plano,
                  "alwaysLoadAllExecutionItems": False, "fetchChildrenOnDemand": True,
                  "teamArea": f"39;{time_['id']}", "iteration": f"35;{it['id']}", "rankingMode": "explicit"}
        _ui_post(PUT_PLAN, {"h": "16;__new_1", "json": json.dumps(record)}, guarded=True)
        criados.append(nome_plano)
    planos_ = {p["nome"]: p["id"] for p in do_time()}
    projeto.gravar_iteracao(cfg, nome, it["id"], planos_)
    return {"id": it["id"], "planos": planos_, "criados": criados}


# --- descoberta para a sdd-setup

def _team_areas(pa_id: str) -> list[dict]:
    root = get_xml(_url(PROJECT_AREA, pa=pa_id) + "/team-areas")
    return [{"nome": t.get(_JP06 + "name"), "id": oslc.item_id(t.findtext(_JP06 + "url"))}
            for t in root.findall(_JP06 + "team-area")]


def _is_link(prop: dict) -> bool:
    pred = prop["predicate"]
    return ".linktype." in pred.lower() or (pred.startswith(("oslc_cm:", "calm:"))
                                            and prop["value_type"] not in ("", *VALUE_TYPES, "xsd:string",
                                                                           "rdf:XMLLiteral", "xsd:dateTime"))


# só o que o fluxo grava: o resto dos tipos e links só inflaria a resposta (a setup o lê uma vez, mas inteiro)
TIPOS_FLUXO = ("story", "defect", "task", "epic")
LINKS_FLUXO = ("parentworkitem", "implementsrequirement", "relatedworkitem", "tracksrequirement",
               "affectsrequirement", "blocks", "depend", "testedbytestcase")
ITERACOES_RECENTES = 6


def descobrir(trecho: str, valores_de: list[str] | None = None) -> dict:
    pas = [{"nome": p["title"], "id": p["project_area_uuid"]} for p in oslc.providers("ccm")
           if trecho.casefold() in p["title"].casefold()]
    if len(pas) != 1:
        return {"pas": pas}
    pa_id = pas[0]["id"]
    cats = [r for r in reportable(CATEGORIES[0], CATEGORIES[1].format(pa=pa_id), "category")
            if r.findtext("archived") != "true"]
    times = [{**t, "categorias": [{"nome": c.findtext("name"), "id": c.findtext("itemId")} for c in cats
                                  if c.findtext("defaultTeamArea/itemId") == t["id"]]} for t in _team_areas(pa_id)]
    tipos, outros, links = [], [], {}
    for t in tipos_servidor(pa_id):
        if not t["id"].casefold().endswith(TIPOS_FLUXO):
            outros.append({"nome": t["title"], "id": t["id"]})
            continue
        shape = oslc.shape(t["shape"]) if t["shape"] else {"title": t["title"], "properties": []}
        campos, obrigatorios, valores, enums = {}, [], {}, []
        for p in shape["properties"]:
            if p["read_only"] or p["predicate"] in ("dcterms:title", "dcterms:description", *SYSTEM):
                continue
            if _is_link(p):
                if any(k in p["predicate"].casefold() for k in LINKS_FLUXO):
                    links[p["title"]] = p["predicate"]
                continue
            campos[p["title"]] = p["predicate"]
            if p["required"] and not p["default"]:
                obrigatorios.append(p["title"])
            if any("/oslc/enumerations/" in r for r in p["range"]):
                enums.append(p["title"])
                # a PA também exige campos por pré-condição de processo, que o shape não marca: a setup pede pelo nome
                if p["title"] in obrigatorios or p["title"] in (valores_de or ()):
                    valores[p["title"]] = [o["title"] for o in enumeracao(p) or []]
        tipos.append({"nome": shape["title"] or t["title"], "id": t["id"], "campos": campos,
                      "obrigatorios": obrigatorios, "enumeracoes": enums, "valores": valores})
    por_id = {i["id"]: i for i in iteracoes(pa_id)}
    its: dict = {}
    for p in planos(pa_id):
        if it := por_id.get(p["iteracao"]):
            its.setdefault(it["nome"], {"id": it["id"], "planos": {}})["planos"][p["nome"]] = p["id"]
    recentes = dict(list(its.items())[-ITERACOES_RECENTES:])
    return {"pa": pas[0], "times": times, "tipos": tipos, "outros_tipos": outros, "links": links,
            "iteracoes": recentes, "iteracoes_total": len(its)}
