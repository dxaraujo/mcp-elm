"""ELM falso com estado: EWM (work items, workflow, DTO da UI web, iterações), DOORS Next e ETM, sobre o FakeAdapter."""
import json
import re
from html import escape
from urllib.parse import parse_qs, unquote, urlsplit

from rdflib import Graph, URIRef
from rdflib.namespace import DCTERMS

from conftest import SERVER, js, rdf, xml

S = SERVER
WI = S + "/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{}"
WF = S + "/ccm/oslc/workflows/_PA1/states/wf"
ESTADOS = {"s1": ("Novo", "open"), "s2": ("Em Andamento", "inprogress"), "s3": ("Concluído", "closed"),
           "s4": ("Cancelado", "closed"), "s5": ("Em Testes", "inprogress")}
# ação -> (estado de onde parte, estado resultante); o servidor ignora em silêncio ação que não parte do atual
# como no PAB: Cancelar (closed) e Testar (inprogress) vêm antes na lista e também partem de "Novo"
ACOES = {"a4": ("Cancelar", "s1", "s4"), "a9": ("Testar", "s1", "s5"), "a1": ("Iniciar", "s1", "s2"),
         "a5": ("Concluir", "s2", "s3"), "a7": ("Reabrir", "s3", "s1")}
PARENT = "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.parent"
CHILDREN = "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.children"
RTC_CM = "http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/"
DTO = S + "/ccm/service/com.ibm.team.workitem.common.internal.rest.IWorkItemRestService/"
REQ_BASE = S + "/rm/views"
CT_BASE = S + "/qm/oslc_qm/contexts/_QPA/resources/TestCaseQuery"


def params(request) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(request.url).query).items()}


def envelope(value: dict):
    return js({"soapenv:Body": {"response": {"returnValue": {"value": value}}}})


class Elm:
    def __init__(self, fake):
        self.fake, self.r = fake, fake.routes
        self.wis: dict[int, dict] = {}
        self.reqs: dict[int, dict] = {}
        self.cts: dict[int, dict] = {}
        self.iteracoes = [("Sprint 1", "_I1")]
        self.planos = [("Sprint 1 - Time A", "_P1", "com.ibm.team.apt.plantype.default", "_I1")]
        self.salvos: list[list] = []  # forms do workItem2
        self._ccm()
        self._rm()
        self._qm()

    # --- EWM

    def _ccm(self):
        r = self.r
        r[("GET", S + "/ccm/rootservices")] = rdf(
            f'<rdf:Description rdf:about="{S}/ccm/rootservices"><oslc_cm1:cmServiceProviders '
            f'xmlns:oslc_cm1="http://open-services.net/xmlns/cm/1.0/" rdf:resource="{S}/ccm/oslc/workitems/catalog"/>'
            '</rdf:Description>')
        r[("GET", S + "/ccm/oslc/workitems/catalog")] = rdf(
            f'<oslc:ServiceProviderCatalog rdf:about="{S}/ccm/oslc/workitems/catalog"><oslc:serviceProvider>'
            f'<oslc:ServiceProvider rdf:about="{S}/ccm/oslc/contexts/_PA1/workitems/services.xml">'
            f'<dcterms:title>Projeto Alfa</dcterms:title><oslc:details rdf:resource="{S}/ccm/process/project-areas/_PA1"/>'
            '</oslc:ServiceProvider></oslc:serviceProvider></oslc:ServiceProviderCatalog>')
        r[("GET", S + "/ccm/oslc/contexts/_PA1/workitems/services.xml")] = rdf(
            f'<oslc:ServiceProvider rdf:about="{S}/ccm/oslc/contexts/_PA1/workitems/services.xml"><oslc:service>'
            '<oslc:Service><oslc:queryCapability><oslc:QueryCapability>'
            f'<oslc:queryBase rdf:resource="{S}/ccm/oslc/contexts/_PA1/workitems"/>'
            '<oslc:resourceType rdf:resource="http://open-services.net/ns/cm#ChangeRequest"/>'
            '</oslc:QueryCapability></oslc:queryCapability><oslc:creationFactory><oslc:CreationFactory>'
            f'<dcterms:title>Task</dcterms:title><oslc:creation rdf:resource="{S}/ccm/oslc/contexts/_PA1/workitems/task"/>'
            '<oslc:resourceType rdf:resource="http://open-services.net/ns/cm#ChangeRequest"/>'
            f'<oslc:resourceType rdf:resource="{S}/ccm/oslc/types/_PA1/task"/>'
            f'<oslc:resourceShape rdf:resource="{S}/ccm/oslc/shapes/task"/>'
            '</oslc:CreationFactory></oslc:creationFactory></oslc:Service></oslc:service></oslc:ServiceProvider>')
        self.shape_extra = ""
        r[("GET", S + "/ccm/oslc/shapes/task")] = lambda req: rdf(self._shape())
        enum = lambda nome, vals: rdf("".join(  # noqa: E731
            f'<rdf:Description rdf:about="{S}/ccm/oslc/enumerations/_PA1/{nome}/{nome}.literal.{i}">'
            f'<dcterms:title>{v}</dcterms:title></rdf:Description>' for i, v in enumerate(vals)))
        r[("GET", S + "/ccm/oslc/enumerations/_PA1/priority")] = enum("priority", ["Alta", "Baixa"])
        r[("GET", S + "/ccm/oslc/enumerations/_PA1/classif")] = enum("classif", ["Desenvolvimento (Transacional / IA)", "Outra"])
        r[("POST", S + "/ccm/oslc/contexts/_PA1/workitems/task")] = self._criar_wi
        r[("GET", S + "/ccm/rpt/repository/foundation")] = xml(
            "<foundation><contributor><userId>joao</userId></contributor>"
            "<contributor><userId>maria</userId></contributor></foundation>")
        r[("GET", WF)] = xml(
            '<oslc_cm:Collection xmlns:oslc_cm="http://open-services.net/xmlns/cm/1.0/" '
            'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dc="http://purl.org/dc/terms/" '
            f'xmlns:rtc_cm="{RTC_CM}">' + "".join(
                f'<rtc_cm:Status rdf:resource="{WF}/{s}"><dc:title>{t}</dc:title><rtc_cm:group>{g}</rtc_cm:group>'
                '</rtc_cm:Status>' for s, (t, g) in ESTADOS.items()) + "</oslc_cm:Collection>")
        r[("GET", WF.replace("/states/", "/actions/"))] = xml(
            '<oslc_cm:Collection xmlns:oslc_cm="http://open-services.net/xmlns/cm/1.0/" '
            'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dc="http://purl.org/dc/terms/" '
            f'xmlns:rtc_cm="{RTC_CM}">' + "".join(
                f'<rtc_cm:Action rdf:resource="{WF}/{a}"><dc:identifier>{a}</dc:identifier><dc:title>{t}</dc:title>'
                f'<rtc_cm:resultState rdf:resource="{WF}/{dst}"/></rtc_cm:Action>' for a, (t, _, dst) in ACOES.items())
            + "</oslc_cm:Collection>")
        r[("GET", S + "/ccm/oslc/contexts/_PA1/workitems")] = lambda req: rdf(
            f'<rdf:Description rdf:about="{S}/ccm/oslc/contexts/_PA1/workitems">'
            + "".join(f'<rdfs:member rdf:resource="{WI.format(i)}"/>' for i in self.wis) + "</rdf:Description>"
            + "".join(self._wi_rdf(i) for i in self.wis))
        r[("GET", DTO + "workItemDTO2")] = lambda req: self._dto(int(params(req)["id"]))
        r[("POST", DTO + "workItem2")] = self._salvar
        r[("GET", S + "/ccm/process/project-areas/_PA1/timelines")] = xml(
            '<jp:timelines xmlns:jp="http://jazz.net/xmlns/prod/jazz/process/1.0/"><jp:timeline>'
            f'<jp:iterations-url>{S}/ccm/tl/its</jp:iterations-url></jp:timeline></jp:timelines>')
        r[("GET", S + "/ccm/tl/its")] = xml(
            '<jp:iterations xmlns:jp="http://jazz.net/xmlns/prod/jazz/process/1.0/"><jp:iteration><jp:label>2026</jp:label>'
            f'<jp:url>{S}/ccm/tl/its/_Y26</jp:url><jp:childern-iterations-url>{S}/ccm/tl/its/_Y26/children'
            '</jp:childern-iterations-url></jp:iteration></jp:iterations>')
        r[("GET", S + "/ccm/tl/its/_Y26/children")] = lambda req: xml(
            '<jp:iterations xmlns:jp="http://jazz.net/xmlns/prod/jazz/process/1.0/">' + "".join(
                f'<jp:iteration><jp:label>{n}</jp:label><jp:url>{S}/ccm/tl/its/{i}</jp:url></jp:iteration>'
                for n, i in self.iteracoes) + "</jp:iterations>")
        r[("GET", S + "/ccm/rpt/repository/apt")] = lambda req: xml("<apt>" + "".join(
            f"<iterationPlanRecord><name>{n}</name><itemId>{i}</itemId><archived>false</archived>"
            f"<planType>{t}</planType><owner><itemId>_TA</itemId></owner><iteration><itemId>{it}</itemId></iteration>"
            "</iterationPlanRecord>" for n, i, t, it in self.planos) + "</apt>")
        apt = S + "/ccm/service/com.ibm.team.apt.internal.service.rest."
        r[("POST", apt + "IPlanProcessRestService/createIteration")] = self._criar_iteracao
        r[("POST", apt + "IPlanRestService/putItems")] = self._criar_plano

    def _shape(self):
        prop = lambda title, pred, extra="": (  # noqa: E731
            f'<oslc:property><oslc:Property><dcterms:title>{title}</dcterms:title>'
            f'<oslc:propertyDefinition rdf:resource="{pred}"/>{extra}</oslc:Property></oslc:property>')
        one = '<oslc:occurs rdf:resource="http://open-services.net/ns/core#Exactly-one"/>'
        return (f'<oslc:ResourceShape rdf:about="{S}/ccm/oslc/shapes/task"><dcterms:title>Tarefa</dcterms:title>'
                + prop("Resumo", "http://purl.org/dc/terms/title", one)
                + prop("Atendido por", RTC_CM + "filedAgainst", one)
                + prop("Estado", RTC_CM + "state", one + "<oslc:readOnly>true</oslc:readOnly>")
                + prop("Planejado para", RTC_CM + "plannedFor")
                + prop("Prioridade", "http://open-services.net/ns/cm-x#priority",
                       one + f'<oslc:range rdf:resource="{S}/ccm/oslc/enumerations/_PA1/priority"/>'
                       + f'<oslc:defaultValue rdf:resource="{S}/ccm/oslc/enumerations/_PA1/priority/priority.literal.1"/>')
                + prop("Classificação", "http://jazz.net/xmlns/prod/jazz/rtc/ext/1.0/classifica%C3%A7%C3%A3o",
                       f'<oslc:range rdf:resource="{S}/ccm/oslc/enumerations/_PA1/classif"/>')
                + prop("Estimativa", RTC_CM + "estimate",
                       '<oslc:valueType rdf:resource="http://www.w3.org/2001/XMLSchema#long"/>')
                + prop("Pai", RTC_CM + "com.ibm.team.workitem.linktype.parentworkitem.parent")
                + self.shape_extra + "</oslc:ResourceShape>")

    def wi(self, i: int, titulo: str, estado="s1", dono=None, pai=None, horas=(), anexos=(), **extra):
        self.wis[i] = {"titulo": titulo, "estado": estado, "dono": dono, "pai": pai, "horas": list(horas),
                       "anexos": list(anexos), "estimate": -1, "comentarios": [], **extra}
        self.r[("GET", WI.format(i))] = lambda req: rdf(self._wi_rdf(i))
        self.r[("PUT", WI.format(i))] = lambda req: self._put_wi(i, req)
        self.r[("GET", WI.format(i) + "/rtc_cm:comments")] = lambda req: rdf(
            f'<rdf:Description rdf:about="{WI.format(i)}/rtc_cm:comments">' + "".join(
                f'<rdfs:member><rdf:Description><dcterms:description>{t}</dcterms:description>'
                f'<dcterms:created>2026-01-0{n + 1}T12:00:00Z</dcterms:created>'
                f'<dcterms:creator rdf:resource="{S}/jts/users/maria"/></rdf:Description></rdfs:member>'
                for n, t in enumerate(self.wis[i]["comentarios"])) + "</rdf:Description>")
        self.r[("POST", WI.format(i) + "/rtc_cm:comments/oslc:comment")] = lambda req: (
            self.wis[i]["comentarios"].append("novo") or (201, {"Location": WI.format(i) + "/c/9"}, b""))
        return self.wis[i]

    def _wi_rdf(self, i: int) -> str:
        w, url = self.wis[i], WI.format(i)
        filhos = [j for j, x in self.wis.items() if x["pai"] == i]
        return (f'<oslc_cm:ChangeRequest rdf:about="{url}"><dcterms:identifier>{i}</dcterms:identifier>'
                f'<dcterms:title>{w["titulo"]}</dcterms:title><dcterms:type>Tarefa</dcterms:type>'
                f'<rtc_cm:type rdf:resource="{S}/ccm/oslc/types/_PA1/task"/>'
                f'<rtc_cm:state rdf:resource="{WF}/{w["estado"]}"/>'
                # o EWM manda inprogress=true em todo estado não fechado, até em "Novo"
                f'<oslc_cm:closed>{str(ESTADOS[w["estado"]][1] == "closed").lower()}</oslc_cm:closed>'
                f'<oslc_cm:inprogress>{str(ESTADOS[w["estado"]][1] != "closed").lower()}</oslc_cm:inprogress>'
                f'<dcterms:contributor rdf:resource="{S}/jts/users/{w["dono"] or "unassigned"}"/>'
                f'<rtc_cm:plannedFor rdf:resource="{S}/ccm/oslc/iterations/_I1"/>'
                f'<rtc_cm:estimate rdf:datatype="http://www.w3.org/2001/XMLSchema#long">{w["estimate"]}</rtc_cm:estimate>'
                f'<oslc:discussedBy rdf:resource="{url}/rtc_cm:comments"/>'
                f'<dcterms:description rdf:parseType="Literal">Falha ao <b>logar</b></dcterms:description>'
                + (f'<{PARENT} rdf:resource="{WI.format(w["pai"])}"/>' if w["pai"] else "")
                + "".join(f'<{CHILDREN} rdf:resource="{WI.format(j)}"/>' for j in filhos)
                + "".join(f'<oslc_cm:implementsRequirement rdf:resource="{S}/rm/resources/_R{q}"/>'
                          for q in w.get("reqs", []))
                + "</oslc_cm:ChangeRequest>"
                f'<rdf:Description rdf:about="{WF}/{w["estado"]}"><dcterms:title>{ESTADOS[w["estado"]][0]}'
                f'</dcterms:title></rdf:Description><rdf:Description rdf:about="{S}/ccm/oslc/iterations/_I1">'
                '<dcterms:title>Sprint 1</dcterms:title></rdf:Description>')

    def _put_wi(self, i: int, req):
        w = self.wis[i]
        if (action := params(req).get("_action")) and ACOES[action][1] == w["estado"]:
            w["estado"] = ACOES[action][2]
        g = Graph().parse(data=req.body, format="xml")
        s = URIRef(WI.format(i))
        w.setdefault("puts", []).append({str(p) for p in g.predicates(s)})
        if dono := g.value(s, URIRef("http://purl.org/dc/terms/contributor")):
            w["dono"] = None if str(dono).endswith("unassigned") else unquote(str(dono).rsplit("/", 1)[-1])
        if (est := g.value(s, URIRef(RTC_CM + "estimate"))) is not None:
            w["estimate"] = int(est)
        if (t := g.value(s, DCTERMS.title)) is not None:
            w["titulo"] = str(t)
        reqs = [str(o).rsplit("_R", 1)[-1] for o in g.objects(s, URIRef("http://open-services.net/ns/cm#implementsRequirement"))]
        w["reqs"] = [int(x) for x in reqs]
        return 200, {}, b""

    def _criar_wi(self, req):
        i = max(self.wis, default=99) + 1
        g = Graph().parse(data=req.body, format="xml")
        self.wi(i, str(g.value(URIRef(""), DCTERMS.title) or g.value(None, DCTERMS.title)))
        self.criado = req.body.decode()
        return 201, {"Location": WI.format(i)}, b""

    def _dto(self, i: int):
        w = self.wis[i]
        horas = [{"itemId": f"_T{k}", "target": {"startDate": f"{d}T12:00:00.000Z", "timeSpent": int(h * 3_600_000),
                                                 "creator": {"userId": p}}} for k, (d, h, p) in enumerate(w["horas"])]
        anexos = [{"url": f"{S}/ccm/att/{n}", "target": {"id": k, "itemId": f"_A{k}", "name": n, "contentLength": 3}}
                  for k, n in enumerate(w["anexos"], 1)]
        return envelope({"workItem": {
            "itemId": f"_W{i}", "stateId": w["estado"], "projectAreaItemId": "_PA1",
            "attributes": [{"key": "workItemType", "value": {"id": "task"}},
                           {"key": "timeSpent", "value": {"id": str(int(sum(h for _, h, _ in w["horas"]) * 3_600_000))}}],
            "linkTypes": [{"id": "com.ibm.team.workitem.linktype.timeSheetEntry", "linkDTOs": horas},
                          {"endpointId": "attachment", "linkDTOs": anexos}]}})

    def _salvar(self, req):
        form = parse_qs(req.body if isinstance(req.body, str) else req.body.decode())
        self.salvos.append(form)
        remover = {json.loads(u)["itemId"] for u in form.get("updateLinks", []) if json.loads(u)["cmd"] == "removeLink"}
        if remover:  # o itemId do work item é "_W<id>"; o do lançamento, "_T<posição>"
            w = self.wis[int(form["itemId"][0][2:])]
            w["horas"] = [x for k, x in enumerate(w["horas"]) if f"_T{k}" not in remover]
        return envelope({"severity": 0, "workItem": {}})

    def _criar_iteracao(self, req):
        payload = json.loads(parse_qs(req.body)["jsonObject"][0])
        self.iteracoes.append((payload["name"], "_INOVA"))
        return js({})

    def _criar_plano(self, req):
        record = json.loads(parse_qs(req.body)["json"][0])
        self.planos.append((record["label"], f"_PN{len(self.planos)}", record["planType"].split("/", 1)[1],
                            record["iteration"].split(";")[1]))
        return js({})

    # --- DOORS Next

    def _rm(self):
        r = self.r
        r[("GET", S + "/rm/rootservices")] = rdf(
            f'<rdf:Description rdf:about="{S}/rm/rootservices"><oslc_rm1:rmServiceProviders '
            f'xmlns:oslc_rm1="http://open-services.net/xmlns/rm/1.0/" rdf:resource="{S}/rm/catalog"/></rdf:Description>')
        r[("GET", S + "/rm/catalog")] = rdf(
            f'<oslc:ServiceProviderCatalog rdf:about="{S}/rm/catalog"><oslc:serviceProvider>'
            f'<oslc:ServiceProvider rdf:about="{S}/rm/sp/_RPA"><dcterms:title>Requisitos Alfa</dcterms:title>'
            f'<oslc:details rdf:resource="{S}/rm/process/project-areas/_RPA"/></oslc:ServiceProvider>'
            '</oslc:serviceProvider></oslc:ServiceProviderCatalog>')
        r[("GET", S + "/rm/sp/_RPA")] = rdf(
            f'<oslc:ServiceProvider rdf:about="{S}/rm/sp/_RPA"><oslc:service><oslc:Service>'
            f'<oslc:queryCapability><oslc:QueryCapability><oslc:queryBase rdf:resource="{REQ_BASE}"/>'
            '<oslc:resourceType rdf:resource="http://open-services.net/ns/rm#Requirement"/>'
            '</oslc:QueryCapability></oslc:queryCapability><oslc:creationFactory><oslc:CreationFactory>'
            f'<oslc:creation rdf:resource="{S}/rm/factory"/>'
            '<oslc:resourceType rdf:resource="http://open-services.net/ns/rm#Requirement"/>'
            f'<oslc:resourceShape rdf:resource="{S}/rm/types/OT_REG"/></oslc:CreationFactory></oslc:creationFactory>'
            '</oslc:Service></oslc:service></oslc:ServiceProvider>')
        r[("GET", REQ_BASE)] = self._req_query
        r[("GET", S + "/rm/types/OT_REG")] = rdf(
            f'<oslc:ResourceShape rdf:about="{S}/rm/types/OT_REG"><dcterms:title>Regra</dcterms:title>'
            '</oslc:ResourceShape>')
        r[("GET", S + "/rm/folders/FR_REG")] = rdf(
            f'<rdf:Description rdf:about="{S}/rm/folders/FR_REG"><dcterms:title>03-Regras</dcterms:title>'
            f'<nav:parent rdf:resource="{S}/rm/folders/FR_ROOT"/></rdf:Description>')
        r[("GET", S + "/rm/folders/FR_ROOT")] = rdf(
            f'<rdf:Description rdf:about="{S}/rm/folders/FR_ROOT"><dcterms:title>root</dcterms:title></rdf:Description>')
        r[("POST", S + "/rm/factory")] = self._criar_req

    def req(self, i: int, titulo: str, texto: str, modificado="2026-01-01T10:00:00Z"):
        self.reqs[i] = {"titulo": titulo, "texto": texto, "modificado": modificado}
        url = f"{S}/rm/resources/_R{i}"
        self.r[("GET", url)] = lambda req: rdf(self._req_rdf(i))
        self.r[("PUT", url)] = lambda req: self._put_req(i, req)

    def _req_rdf(self, i: int) -> str:
        q = self.reqs[i]
        return (f'<oslc_rm:Requirement rdf:about="{S}/rm/resources/_R{i}"><dcterms:identifier>{i}</dcterms:identifier>'
                f'<dcterms:title>{q["titulo"]}</dcterms:title><dcterms:modified>{q["modificado"]}</dcterms:modified>'
                f'<oslc:instanceShape rdf:resource="{S}/rm/types/OT_REG"/>'
                f'<nav:parent rdf:resource="{S}/rm/folders/FR_REG"/>'
                f'<jazz_rm:primaryText rdf:parseType="Literal"><div xmlns="http://www.w3.org/1999/xhtml">{q["texto"]}'
                '</div></jazz_rm:primaryText></oslc_rm:Requirement>')

    def _req_query(self, req):
        p = params(req)
        where, ids = p.get("oslc.where", ""), list(self.reqs)
        if m := re.search(r"dcterms:identifier=(\d+)", where):
            ids = [int(m.group(1))] if int(m.group(1)) in self.reqs else []
        if term := p.get("oslc.searchTerms"):
            ids = [i for i in ids if term.strip('"').lower() in self.reqs[i]["titulo"].lower()]
        return rdf(f'<rdf:Description rdf:about="{REQ_BASE}">' + "".join(
            f'<rdfs:member rdf:resource="{S}/rm/resources/_R{i}"/>' for i in ids) + "</rdf:Description>"
                   + "".join(self._req_rdf(i) for i in ids)
                   + f'<oslc:ResponseInfo rdf:about="{escape(req.url)}"><oslc:totalCount>{len(ids)}</oslc:totalCount>'
                   "</oslc:ResponseInfo>")

    def _put_req(self, i: int, req):
        g = Graph().parse(data=req.body, format="xml")
        s = URIRef(f"{S}/rm/resources/_R{i}")
        texto = str(g.value(s, URIRef("http://jazz.net/ns/rm#primaryText")))
        self.reqs[i].update(titulo=str(g.value(s, DCTERMS.title)), modificado="2026-02-01T10:00:00Z",
                            texto=re.sub(r"^<div[^>]*>|</div>$", "", texto.strip()))
        return 200, {}, b""

    def _criar_req(self, req):
        i = max(self.reqs, default=0) + 1
        g = Graph().parse(data=req.body, format="xml")
        self.req(i, str(next(g.objects(None, DCTERMS.title))), "novo")
        self.req_criado = req.body.decode()
        return 201, {"Location": f"{S}/rm/resources/_R{i}"}, b""

    # --- ETM

    def _qm(self):
        r = self.r
        r[("GET", S + "/qm/rootservices")] = rdf(
            f'<rdf:Description rdf:about="{S}/qm/rootservices"><oslc_qm1:qmServiceProviders '
            f'xmlns:oslc_qm1="http://open-services.net/xmlns/qm/1.0/" rdf:resource="{S}/qm/catalog"/></rdf:Description>')
        r[("GET", S + "/qm/catalog")] = rdf(
            f'<oslc:ServiceProviderCatalog rdf:about="{S}/qm/catalog"><oslc:serviceProvider>'
            f'<oslc:ServiceProvider rdf:about="{S}/qm/sp/_QPA"><dcterms:title>Testes Alfa</dcterms:title>'
            f'<oslc:details rdf:resource="{S}/qm/process/project-areas/_QPA"/></oslc:ServiceProvider>'
            '</oslc:serviceProvider></oslc:ServiceProviderCatalog>')
        r[("GET", S + "/qm/sp/_QPA")] = rdf(
            f'<oslc:ServiceProvider rdf:about="{S}/qm/sp/_QPA"><oslc:service><oslc:Service>'
            f'<oslc:queryCapability><oslc:QueryCapability><oslc:queryBase rdf:resource="{CT_BASE}"/>'
            '<oslc:resourceType rdf:resource="http://open-services.net/ns/qm#TestCaseQuery"/>'
            '</oslc:QueryCapability></oslc:queryCapability><oslc:creationFactory><oslc:CreationFactory>'
            f'<oslc:creation rdf:resource="{S}/qm/factory"/>'
            '<oslc:resourceType rdf:resource="http://open-services.net/ns/qm#TestCase"/>'
            '</oslc:CreationFactory></oslc:creationFactory></oslc:Service></oslc:service></oslc:ServiceProvider>')
        r[("GET", CT_BASE)] = lambda req: rdf(
            f'<rdf:Description rdf:about="{CT_BASE}">' + "".join(
                f'<rdfs:member rdf:resource="{S}/qm/tc/{i}"/>' for i in self.cts
                if f"oslc:shortId={i}" in params(req).get("oslc.where", "")) + "</rdf:Description>")
        r[("POST", S + "/qm/factory")] = self._criar_ct

    def ct(self, i: int):
        self.cts[i] = {"valida": []}
        url = f"{S}/qm/tc/{i}"
        self.r[("GET", url)] = lambda req: rdf(
            f'<oslc_qm:TestCase rdf:about="{url}"><oslc:shortId>{i}</oslc:shortId><dcterms:title>CT</dcterms:title>'
            + "".join(f'<oslc_qm:validatesRequirement rdf:resource="{v}"/>' for v in self.cts[i]["valida"])
            + "</oslc_qm:TestCase>")

        def put(req):
            g = Graph().parse(data=req.body, format="xml")
            self.cts[i]["valida"] = [str(o) for o in g.objects(URIRef(url), URIRef(
                "http://open-services.net/ns/qm#validatesRequirement"))]
            return 200, {}, b""
        self.r[("PUT", url)] = put

    def _criar_ct(self, req):
        i = max(self.cts, default=500) + 1
        self.ct(i)
        g = Graph().parse(data=req.body, format="xml")
        self.cts[i]["valida"] = [str(o) for o in g.objects(None, URIRef("http://open-services.net/ns/qm#validatesRequirement"))]
        return 201, {"Location": f"{S}/qm/tc/{i}"}, b""
