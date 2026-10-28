import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from conftest import SERVER, chamar
from elm_fake import Elm


@pytest.fixture
def elm(fake):
    return Elm(fake)


def ok(r):
    assert r["ok"], r
    return r["dados"]


def falha(r):
    assert not r["ok"], r
    return r["erro"]


def test_status(elm, projeto):
    assert ok(chamar("elm_status", projeto=projeto)) == {
        "login": "joao", "servidor": SERVER, "pas": ["alfa"], "valido": True, "faltando": []}
    os.remove(os.path.join(projeto, ".kiro/config/sdd/pa_alfa.json"))
    d = ok(chamar("elm_status", projeto=projeto))
    assert d["valido"] is False and d["faltando"] == ["pa"]


def test_ler_wi_markdown_com_campos_links_e_comentarios(elm, projeto):
    elm.wi(10, "[FT-001] IB", dono="maria")
    w = elm.wi(42, "[DEV] FT-001 Implementar", estado="s2", dono="joao", pai=10, reqs=[7])
    w["estimate"], w["comentarios"] = 4 * 3_600_000, ["um", "dois", "três"]
    md = chamar("elm_ler_wi", projeto=projeto, id=42, comentarios=2)
    head, body = md.split("\n---\n", 1)
    assert "tipo: tarefa" in head and "estado: Em Andamento" in head and "dono: joao" in head
    assert "pai: 10" in head and "iteracao: Sprint 1" in head and "estimativa_h: 4.0" in head
    assert "  Planejado para: Sprint 1" in head and "  implementa:\n    - " in head
    assert "Falha ao **logar**" in body and "dois" in body and "três" in body and "um" not in body
    assert "/oslc/enumerations/" not in md
    assert falha(chamar("elm_ler_wi", projeto=projeto, id=999))["codigo"] == "nao_encontrado"


def test_listar_wi_filtra_por_prefixo_e_estado(elm, projeto):
    elm.wi(10, "[FT-001] IB")
    elm.wi(42, "[DEV] FT-001 Implementar", pai=10, dono="joao")
    elm.wi(43, "[REV] FT-001 Revisar", pai=10, estado="s3")
    d = ok(chamar("elm_listar_wi", projeto=projeto, pai=10, titulo_prefixo="[DEV]"))
    assert d == {"itens": [{"id": 42, "tipo": "tarefa", "titulo": "[DEV] FT-001 Implementar", "estado": "Novo",
                            "dono": "joao"}], "truncado": False, "total": 1}
    d = ok(chamar("elm_listar_wi", projeto=projeto, iteracao="Sprint 1", estado="concluído", limite=1))
    assert [i["id"] for i in d["itens"]] == [43]
    assert falha(chamar("elm_listar_wi", projeto=projeto))["campo"] == "iteracao"


def test_criar_wi_aplica_padroes_prefixo_e_campos(elm, projeto):
    d = ok(chamar("elm_criar_wi", projeto=projeto, tipo="tarefa", titulo="[DEV] FT-001 Implementar", pai=10,
                  dono="eu", estimativa_h=2, iteracao="Sprint 1", campos={"Prioridade": "baixa"}))
    assert d["id"] == 100 and d["url"].endswith(".WorkItem/100")
    assert d["valores_usados"] == {"Prioridade": "baixa", "Classificação": "Desenvolvimento", "categoria": "Cat Alfa",
                                   "dono": "eu", "iteracao": "Sprint 1", "estimativa_h": 2}
    body = elm.criado
    assert "priority.literal.1" in body and "classif.literal.0" in body and "Category/_C1" in body
    assert "iterations/_I1" in body and "jts/users/joao" in body and "7200000" in body and "WorkItem/10" in body
    assert "tasks/" not in body and "types/_PA1/task" in body
    e = falha(chamar("elm_criar_wi", projeto=projeto, tipo="tarefa", titulo="x", campos={"Prioridade": "Urgente"}))
    assert e["codigo"] == "entrada" and e["campo"] == "Prioridade" and "Alta" in e["detalhe"]


def test_criar_wi_obrigatorio_sem_valor_e_precondicao(elm, projeto):
    elm.shape_extra = ('<oslc:property><oslc:Property><dcterms:title>OTR</dcterms:title><oslc:propertyDefinition '
                       'rdf:resource="http://jazz.net/xmlns/prod/jazz/rtc/ext/1.0/otr"/><oslc:occurs '
                       'rdf:resource="http://open-services.net/ns/core#Exactly-one"/></oslc:Property></oslc:property>')
    e = falha(chamar("elm_criar_wi", projeto=projeto, tipo="tarefa", titulo="x"))
    assert e == {"codigo": "precondicao", "detalhe": e["detalhe"], "campo": "OTR"}
    assert falha(chamar("elm_criar_wi", projeto=projeto, tipo="defeito", titulo="x"))["campo"] == "ccm.tipos.defeito"


def test_atualizar_wi_varios_ids_com_falha(elm, projeto):
    elm.wi(42, "a")
    elm.wi(43, "b")
    d = ok(chamar("elm_atualizar_wi", projeto=projeto, ids=[42, 999, 43], iteracao="Sprint 1", estimativa_h=1.5))
    assert d["atualizados"] == [42, 43] and d["falhas"][0]["id"] == 999
    assert d["falhas"][0]["codigo"] == "nao_encontrado" and elm.wis[43]["estimate"] == 5_400_000
    assert falha(chamar("elm_atualizar_wi", projeto=projeto, ids=[42]))["codigo"] == "entrada"


def test_transicao_assumir_concluir_reabrir(elm, projeto):
    elm.wi(10, "IB", dono="maria")
    elm.wi(42, "[DEV] x", pai=10)
    d = ok(chamar("elm_transicao", projeto=projeto, id=42, alvo="assumir", estimativa_h=3, iniciar_pai=True))
    assert d == {"id": 42, "estado": "Em Andamento", "dono": "joao", "ja_estava": False,
                 "pai": {"id": 10, "estado": "Em Andamento"}}
    assert elm.wis[42]["estimate"] == 3 * 3_600_000 and elm.wis[10]["dono"] == "maria"
    assert ok(chamar("elm_transicao", projeto=projeto, id=42, alvo="assumir"))["ja_estava"] is True
    elm.wi(44, "nova")
    assert ok(chamar("elm_transicao", projeto=projeto, id=44, alvo="concluir"))["estado"] == "Concluído"  # via andamento
    e = falha(chamar("elm_transicao", projeto=projeto, id=44, alvo="assumir"))
    assert e["codigo"] == "precondicao" and e["campo"] == "estado"
    assert ok(chamar("elm_transicao", projeto=projeto, id=44, alvo="reabrir"))["estado"] == "Novo"


def test_transicao_dono_outro_e_pai_sem_dono(elm, projeto):
    elm.wi(10, "IB")
    elm.wi(42, "x", dono="maria", pai=10)
    assert falha(chamar("elm_transicao", projeto=projeto, id=42, alvo="assumir")) == {
        "codigo": "conflito", "detalhe": "maria", "campo": "dono"}
    e = falha(chamar("elm_transicao", projeto=projeto, id=42, alvo="assumir", forcar=True, iniciar_pai=True))
    assert e["campo"] == "dono do pai" and elm.wis[42]["estado"] == "s1"  # nada mudou
    assert ok(chamar("elm_transicao", projeto=projeto, id=42, alvo="assumir", forcar=True))["dono"] == "joao"


def test_comentar(elm, projeto):
    elm.wi(42, "x")
    assert ok(chamar("elm_comentar", projeto=projeto, id=42, texto_md="feito")) == {"id": 42}
    assert "feito" in elm.fake.bodies("POST", "oslc:comment")[0]
    assert falha(chamar("elm_comentar", projeto=projeto, id=999, texto_md="x"))["codigo"] == "nao_encontrado"


def test_anexo_listar_baixar_enviar(elm, projeto, fake):
    elm.wi(42, "x", anexos=["log.txt"])
    assert ok(chamar("elm_anexo", projeto=projeto, id=42, acao="listar")) == {"anexos": [{"nome": "log.txt", "tamanho": 3}]}
    fake.routes[("GET", SERVER + "/ccm/att/log.txt")] = (200, {}, b"abc")
    assert ok(chamar("elm_anexo", projeto=projeto, id=42, acao="baixar", caminho="tmp/log.txt")) == {"caminho": "tmp/log.txt"}
    assert open(os.path.join(projeto, "tmp/log.txt"), "rb").read() == b"abc"
    assert falha(chamar("elm_anexo", projeto=projeto, id=42, acao="baixar", caminho="../fora/log.txt"))["campo"] == "caminho"
    fake.routes[("POST", SERVER + "/ccm/service/com.ibm.team.workitem.service.internal.rest.IAttachmentRestService/")] = (
        200, {}, '<html><textarea>{"files":[{"uuid":"_N","name":"log.txt"}]}</textarea></html>')
    assert ok(chamar("elm_anexo", projeto=projeto, id=42, acao="enviar", caminho="tmp/log.txt")) == {"nome": "log.txt"}
    fora = os.path.join(os.path.dirname(projeto), "segredo.txt")
    open(fora, "w").write("x")
    for caminho in ("../segredo.txt", fora):
        assert falha(chamar("elm_anexo", projeto=projeto, id=42, acao="enviar", caminho=caminho))["campo"] == "caminho"
    links = [json.loads(link) for link in elm.salvos[-1]["updateLinks"]]
    assert [(link["cmd"], link["itemId"]) for link in links] == [("addLink", "_N"), ("removeLink", "_A1")]


def test_horas_do_wi_e_da_iteracao(elm, projeto):
    elm.wi(42, "[DEV] a", horas=[("2026-01-02", 2, "joao"), ("2026-01-01", 1.5, "maria")])
    elm.wi(43, "[REV] b", dono="maria")
    assert ok(chamar("elm_horas", projeto=projeto, id=42)) == {
        "lancamentos": [{"data": "2026-01-01", "horas": 1.5, "pessoa": "maria"},
                        {"data": "2026-01-02", "horas": 2.0, "pessoa": "joao"}], "total_h": 3.5}
    d = ok(chamar("elm_horas", projeto=projeto, iteracao="Sprint 1"))
    assert d["total_h"] == 3.5 and {"tarefa_id": 43, "prefixo": "[REV]", "pessoa": "maria", "horas": 0} in d["linhas"]
    assert falha(chamar("elm_horas", projeto=projeto))["codigo"] == "entrada"


def test_lancar_horas_valida_e_devolve_so_o_lancado(elm, projeto):
    elm.wi(42, "x", dono="joao", horas=[("2026-01-02", 10, "joao")])
    d = ok(chamar("elm_lancar_horas", projeto=projeto, id=42, horas=2, data="2026-01-02"))
    assert d == {"lancado": {"data": "2026-01-02", "horas": 2.0}, "total_h": 12.0}
    form = elm.salvos[-1]
    item = json.loads(form["updateLinks"][0])["item"]
    assert item["timeCode"] == "Horas Alfa" and item["workType"] == "Tarefa" and form["attributeValues"] == ["43200000"]
    amanha = (date.today() + timedelta(days=1)).isoformat()
    assert falha(chamar("elm_lancar_horas", projeto=projeto, id=42, horas=1, data=amanha))["campo"] == "data"
    assert falha(chamar("elm_lancar_horas", projeto=projeto, id=42, horas=7, data="2026-01-02"))["campo"] == "horas"
    assert falha(chamar("elm_lancar_horas", projeto=projeto, id=42, horas=17))["campo"] == "horas"
    elm.wis[42]["dono"] = "maria"
    assert falha(chamar("elm_lancar_horas", projeto=projeto, id=42, horas=1))["campo"] == "dono"


def test_iteracao_acha_e_cria_com_planos_e_grava_no_pa(elm, projeto):
    d = ok(chamar("elm_iteracao", projeto=projeto, nome="Sprint 1"))
    assert d["id"] == "_I1" and d["criados"] == []
    assert falha(chamar("elm_iteracao", projeto=projeto, nome="Sprint 2"))["campo"] == "inicio"
    d = ok(chamar("elm_iteracao", projeto=projeto, nome="Sprint 2", inicio="2026-02-01", fim="2026-02-14"))
    assert d["id"] == "_INOVA" and d["criados"] == ["Sprint 2", "Sprint 2 - Time A"]
    assert elm.iteracao_criada["parentIterationId"] == "_Y26"
    arq = os.path.join(projeto, ".kiro/config/sdd/pa_alfa.json")
    texto = open(arq, encoding="utf-8").read()
    assert json.loads(texto)["ccm"]["iteracoes"]["Sprint 2"]["id"] == "_INOVA"
    assert texto.startswith('{\n  "$schema"') and texto.endswith("}\n")  # indent=2, ordem mantida


def test_iteracao_por_caminho_cria_o_pai_irmao_e_desambigua(elm, projeto):
    d = ok(chamar("elm_iteracao", projeto=projeto, nome="2026/Sprint 1"))
    assert d["id"] == "_I1" and d["pai"] == "2026" and d["criados"] == []
    d = ok(chamar("elm_iteracao", projeto=projeto, nome="2027/Sprint 1", inicio="2027-01-04", fim="2027-01-15"))
    assert d["criados"] == ["2027", "Sprint 1", "Sprint 1 - Time A"] and d["pai"] == "2027"
    assert elm.topo[-1] == ("2027", "_INOVA") and elm.filhos["_INOVA"] == [("Sprint 1", "_INOVA2")]
    assert elm.iteracao_criada["parentIterationId"] == "_INOVA" and d["id"] == "_INOVA2"
    assert falha(chamar("elm_iteracao", projeto=projeto, nome="Sprint 1"))["campo"] == "nome"  # ambígua no servidor
    ok(chamar("elm_listar_wi", projeto=projeto, iteracao="2027/Sprint 1"))
    pa = json.loads(open(os.path.join(projeto, ".kiro/config/sdd/pa_alfa.json"), encoding="utf-8").read())
    assert pa["ccm"]["iteracoes"]["2027/Sprint 1"]["id"] == "_INOVA2"


def test_iteracao_primeira_sprint_cria_o_pai_no_topo(elm, projeto):
    arq = os.path.join(projeto, ".kiro/config/sdd/pa_alfa.json")
    pa = json.loads(open(arq, encoding="utf-8").read())
    pa["ccm"]["iteracoes"] = {}
    open(arq, "w", encoding="utf-8").write(json.dumps(pa))
    d = ok(chamar("elm_iteracao", projeto=projeto, nome="2027/Sprint 1", inicio="2027-01-04", fim="2027-01-15"))
    assert d["criados"][:2] == ["2027", "Sprint 1"] and d["pai"] == "2027"
    assert elm.topo[-1] == ("2027", "_INOVA") and elm.iteracao_criada["parentIterationId"] == "_INOVA"


def test_iteracao_primeira_sprint_com_varias_timelines_nasce_na_base_do_backlog(elm, projeto):
    from conftest import SERVER as S, xml
    arq = os.path.join(projeto, ".kiro/config/sdd/pa_alfa.json")
    pa = json.loads(open(arq, encoding="utf-8").read())
    pa["ccm"]["iteracoes"] = {}
    open(arq, "w", encoding="utf-8").write(json.dumps(pa))
    elm.r[("GET", S + "/ccm/process/project-areas/_PA1/timelines")] = xml(
        '<jp:timelines xmlns:jp="http://jazz.net/xmlns/prod/jazz/process/1.0/">'
        f'<jp:timeline><jp:url>{S}/ccm/process/project-areas/_PA1/timelines/_TL1</jp:url><jp:iterations-url>{S}/ccm/tl/its</jp:iterations-url></jp:timeline>'
        f'<jp:timeline><jp:url>{S}/ccm/process/project-areas/_PA1/timelines/_TL2</jp:url><jp:iterations-url>{S}/ccm/tl/its2</jp:iterations-url></jp:timeline>'
        '</jp:timelines>')
    elm.r[("GET", S + "/ccm/tl/its2")] = lambda req: elm._its([("Outra", "_O1")])
    assert falha(chamar("elm_iteracao", projeto=projeto, nome="2027/Sprint 1", inicio="2027-01-04"))["campo"] == "nome"
    pa["ccm"]["backlog"] = {"nome": "Sprint 1", "id": "_I1", "plano": {"nome": "Backlog Time A", "id": "_PB"}}
    open(arq, "w", encoding="utf-8").write(json.dumps(pa))
    ok(chamar("elm_iteracao", projeto=projeto, nome="2027/Sprint 1", inicio="2027-01-04"))
    assert elm.filhos["_I1"][0][0] == "2027"  # com backlog no pa, o pai nasce dentro dele (a linha de base)



BACKLOG = "com.ibm.team.apt.plantype.product.backlog"


def test_backlog_lista_e_grava_o_plano_existente(elm, projeto):
    e = falha(chamar("elm_criar_wi", projeto=projeto, tipo="tarefa", titulo="x", iteracao="backlog"))
    assert e["campo"] == "ccm.backlog"  # nunca chuta a iteração do backlog
    elm.iteracoes.append(("Backlog", "_IB"))
    elm.planos.append(("Backlog Time A", "_PB", BACKLOG, "_IB"))
    d = ok(chamar("elm_backlog", projeto=projeto))
    assert d["atual"] is None and d["candidatos"] == [{"plano": "Backlog Time A", "id": "_PB", "iteracao": "Backlog"}]
    link = "https://elm.exemplo/ccm/web/projects/X#action=com.ibm.team.apt.viewPlan&id=_PB&planMode=x"
    d = ok(chamar("elm_backlog", projeto=projeto, plano=link))
    assert d == {"nome": "Backlog", "id": "_IB", "plano": {"nome": "Backlog Time A", "id": "_PB"}, "criados": []}
    pa = json.loads(open(os.path.join(projeto, ".kiro/config/sdd/pa_alfa.json"), encoding="utf-8").read())
    assert pa["ccm"]["backlog"]["id"] == "_IB" and "Sprint 1" in pa["ccm"]["iteracoes"]
    ok(chamar("elm_criar_wi", projeto=projeto, tipo="tarefa", titulo="x", iteracao="backlog"))
    assert "iterations/_IB" in elm.criado
    assert falha(chamar("elm_backlog", projeto=projeto, plano="_NADA"))["campo"] == "plano"


def test_backlog_cria_iteracao_com_pai_ou_no_topo_e_o_plano(elm, projeto):
    d = ok(chamar("elm_backlog", projeto=projeto, nome="Backlog", pai="Sprint 1"))
    assert d["id"] == "_INOVA" and d["criados"] == ["Backlog", "Backlog Time A"]
    assert elm.iteracao_criada["parentIterationId"] == "_I1" and not elm.iteracao_criada["hasDeliverable"]
    assert elm.planos[-1][2:] == (BACKLOG, "_INOVA")
    elm.iteracoes.pop()
    elm.planos.pop()
    ok(chamar("elm_backlog", projeto=projeto, nome="Backlog"))
    assert elm.iteracao_criada["developmentLineId"] == "_TL1" and "parentIterationId" not in elm.iteracao_criada


def test_iteracao_com_linha_de_base_resolve_e_cria_dentro_dela(elm, projeto):
    base = ok(chamar("elm_backlog", projeto=projeto, nome="Desenvolvimento"))["id"]  # topo: a linha de base
    d = ok(chamar("elm_iteracao", projeto=projeto, nome="2026/Sprint 1", inicio="2026-10-26", fim="2026-11-06"))
    assert d["criados"] == ["2026", "Sprint 1", "Sprint 1 - Time A"] and d["pai"] == "2026"
    assert elm.filhos[base][0][0] == "2026" and d["id"] != "_I1"  # não usa a 2026/Sprint 1 de fora da base
    ok(chamar("elm_iteracao", projeto=projeto, nome="2026/Sprint 2", inicio="2026-11-09"))
    assert ok(chamar("elm_iteracao", projeto=projeto, nome="2026/Sprint 1"))["criados"] == []
    ok(chamar("elm_listar_wi", projeto=projeto, iteracao="2026/Sprint 2"))  # a 2026 do topo não deixa ambíguo
    assert len([n for n, _ in elm.filhos[base]]) == 1  # a 2026 nasceu uma vez só


def test_backlog_em_iteracao_existente_cria_so_o_plano_e_nao_escolhe_entre_dois(elm, projeto):
    d = ok(chamar("elm_backlog", projeto=projeto, nome="Sprint 1"))
    assert d["id"] == "_I1" and d["criados"] == ["Backlog Time A"]  # a iteração já existe: só o plano
    d = ok(chamar("elm_backlog", projeto=projeto, nome="Sprint 1"))
    assert d["criados"] == []
    elm.planos.append(("Backlog Teste", "_PB2", BACKLOG, "_I1"))
    e = falha(chamar("elm_backlog", projeto=projeto, nome="Sprint 1"))
    assert e["campo"] == "plano" and "Backlog Teste" in e["detalhe"]
    assert ok(chamar("elm_backlog", projeto=projeto, plano="_PB2"))["plano"]["id"] == "_PB2"


def test_setup_descobrir_ccm(elm, fake):
    fake.routes[("GET", SERVER + "/ccm/process/project-areas/_PA1/team-areas")] = (
        200, {}, '<jp:team-areas xmlns:jp="http://jazz.net/xmlns/prod/jazz/process/0.6/"><jp:team-area jp:name="Time A">'
                 f'<jp:url>{SERVER}/ccm/process/project-areas/_PA1/team-areas/_TA</jp:url></jp:team-area></jp:team-areas>')
    fake.routes[("GET", SERVER + "/ccm/rpt/repository/workitem")] = (
        200, {}, "<workitem><category><itemId>_C1</itemId><name>Cat Alfa</name><archived>false</archived>"
                 "<defaultTeamArea><itemId>_TA</itemId></defaultTeamArea></category></workitem>")
    d = ok(chamar("elm_setup_descobrir", ccm="alfa", qm="alfa"))
    ccm = d["ccm"]
    assert ccm["pa"] == {"nome": "Projeto Alfa", "id": "_PA1"}
    assert ccm["times"] == [{"nome": "Time A", "id": "_TA", "categorias": [{"nome": "Cat Alfa", "id": "_C1"}]}]
    tarefa = ccm["tipos"][0]
    assert tarefa["id"] == "task" and tarefa["obrigatorios"] == ["Atendido por"]
    assert "Classificação" in tarefa["enumeracoes"] and "Classificação" not in tarefa["valores"] and ccm["links"]["Pai"]  # não obrigatório: só com valores_de
    extra = ok(chamar("elm_setup_descobrir", ccm="alfa", valores_de=["Classificação"]))["ccm"]["tipos"][0]
    assert extra["valores"]["Classificação"] == ["Desenvolvimento (Transacional / IA)", "Outra"]
    assert ccm["iteracoes"] == {"Sprint 1": {"id": "_I1", "planos": {"Sprint 1 - Time A": "_P1"}}}
    assert d["qm"] == {"nome": "Testes Alfa", "id": "_QPA"}
    assert ok(chamar("elm_setup_descobrir", ccm="zzz"))["ccm"] == {"pas": []}


def test_obrigatorios_do_pa_travam_a_criacao(elm, projeto):
    """Campo que a PA exige por processo (sem o tipo marcar) é exigido já na criação: padrão do pa, campos ou erro."""
    arq = next(Path(projeto, ".kiro", "config", "sdd").glob("pa_*.json"))
    pa = json.loads(arq.read_text())
    pa["ccm"]["tipos"]["tarefa"]["obrigatorios"] = ["Planejado para", "Estimativa", "Prioridade", "OTR"]
    pa["ccm"]["tipos"]["tarefa"]["campos"]["OTR"] = "rtc_ext:otr"
    pa["ccm"]["tipos"]["tarefa"]["campos"]["Planejado para"] = "rtc_cm:plannedFor"
    arq.write_text(json.dumps(pa))
    os.utime(arq, (time.time() + 5, time.time() + 5))  # o cache do pa é por mtime
    e = falha(chamar("elm_criar_wi", projeto=projeto, tipo="tarefa", titulo="x"))
    assert e["codigo"] == "precondicao" and e["campo"] == "Planejado para"
    e = falha(chamar("elm_criar_wi", projeto=projeto, tipo="tarefa", titulo="x", iteracao="Sprint 1"))
    assert e["codigo"] == "precondicao" and e["campo"] == "Estimativa" and not getattr(elm, "criado", None)
    e = falha(chamar("elm_criar_wi", projeto=projeto, tipo="tarefa", titulo="x", iteracao="Sprint 1", estimativa_h=1))
    assert e["campo"] == "OTR"  # Prioridade tem padrão no pa; OTR não


def test_remover_horas_so_as_minhas_na_data(elm, projeto):
    elm.wi(42, "x", dono="joao", horas=[("2026-01-02", 2, "joao"), ("2026-01-02", 1, "maria"), ("2026-01-03", 3, "joao")])
    d = ok(chamar("elm_remover_horas", projeto=projeto, id=42, data="2026-01-02"))
    assert d == {"removidos": [{"data": "2026-01-02", "horas": 2.0}], "total_h": 4.0}
    assert elm.wis[42]["horas"] == [("2026-01-02", 1, "maria"), ("2026-01-03", 3, "joao")]
    assert falha(chamar("elm_remover_horas", projeto=projeto, id=42, data="2026-01-02"))["codigo"] == "nao_encontrado"
