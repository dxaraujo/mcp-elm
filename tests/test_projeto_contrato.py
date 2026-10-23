import json
import os
import shutil

import pytest
import requests

from conftest import FIXTURES
from mcp_elm import projeto as prj
from mcp_elm.contrato import Erro, operacao
from mcp_elm.http import ElmHttpError


def _erro(fn) -> dict:
    with pytest.raises(Erro) as exc:
        fn()
    return {"codigo": exc.value.codigo, "campo": exc.value.campo, "detalhe": exc.value.detalhe}


def test_carregar_um_pa_sem_nome(projeto):
    cfg = prj.carregar(projeto)
    assert cfg["ccm"]["id"] == "_PA1" and cfg["_projeto"] == projeto


def test_carregar_ausente_relativo_e_varios(tmp_path, projeto):
    assert _erro(lambda: prj.carregar(str(tmp_path / "vazio")))["campo"] == "projeto"
    (tmp_path / "vazio").mkdir()
    assert _erro(lambda: prj.carregar(str(tmp_path / "vazio")))["codigo"] == "config"
    assert _erro(lambda: prj.carregar("relativo/x"))["codigo"] == "entrada"
    shutil.copy(FIXTURES / "pa_alfa.json", os.path.join(projeto, ".kiro/config/sdd/pa_beta.json"))
    e = _erro(lambda: prj.carregar(projeto))
    assert e["codigo"] == "entrada" and e["campo"] == "pa" and "alfa, beta" in e["detalhe"]
    assert prj.carregar(projeto, "beta")["ccm"]["nome"] == "Projeto Alfa"
    assert _erro(lambda: prj.carregar(projeto, "gama"))["codigo"] == "config"


def test_carregar_invalido_diz_o_campo(projeto):
    arq = os.path.join(projeto, ".kiro/config/sdd/pa_alfa.json")
    dados = json.load(open(arq, encoding="utf-8"))
    del dados["ccm"]["horas"]["time-code"]
    json.dump(dados, open(arq, "w", encoding="utf-8"))
    e = _erro(lambda: prj.carregar(projeto))
    assert e == {"codigo": "config", "campo": "ccm.horas.time-code", "detalhe": e["detalhe"]}


def test_cache_por_mtime(projeto):
    arq = os.path.join(projeto, ".kiro/config/sdd/pa_alfa.json")
    assert prj.carregar(projeto)["ccm"]["nome"] == "Projeto Alfa"
    dados = json.load(open(arq, encoding="utf-8"))
    dados["ccm"]["nome"] = "Renomeado"
    json.dump(dados, open(arq, "w", encoding="utf-8"))
    st = os.stat(arq)
    os.utime(arq, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert prj.carregar(projeto)["ccm"]["nome"] == "Renomeado"
    prj.carregar(projeto)["ccm"]["nome"] = "mexido"  # cópia: o cache não muda
    assert prj.carregar(projeto)["ccm"]["nome"] == "Renomeado"


def _resp(status: int, body: str) -> requests.Response:
    resp = requests.Response()
    resp.status_code, resp._content = status, body.encode()
    return resp


def test_envelope_ok_e_erros():
    ok = operacao(lambda: {"a": "ç"})()
    assert ok == '{"ok":true,"dados":{"a":"ç"}}'
    assert operacao(lambda: "---\nid: 1\n---\n")() == "---\nid: 1\n---\n"  # Markdown passa direto
    html = "<html><head><style>x{}</style></head><body><h1>Erro</h1><p>" + "não pode " * 60 + "</p></body></html>"

    def falha(exc):
        def fn():
            raise exc
        return json.loads(operacao(fn)())["erro"]

    e = falha(ElmHttpError(_resp(400, html)))
    assert e["codigo"] == "entrada" and "<" not in e["detalhe"] and len(e["detalhe"]) <= 200
    oslc_err = ('<rdf:RDF xmlns:oslc="http://open-services.net/ns/core#"><oslc:Error><oslc:message>'
                "O atributo 'Estimativa' é obrigatório</oslc:message></oslc:Error></rdf:RDF>")
    assert falha(ElmHttpError(_resp(409, oslc_err))) == {
        "codigo": "precondicao", "detalhe": "O atributo 'Estimativa' é obrigatório", "campo": "Estimativa"}
    assert falha(ElmHttpError(_resp(403, '{"errorMessage": "sem permissão em \'Iteração\'"}')))["campo"] == "Iteração"
    ewm_403 = ('{"errorMessage": "\'Save Work Item\' failed. Preconditions have not been met: '
               'The \'Ambiente\' attribute needs to be set (work item )."}')
    assert {k: v for k, v in falha(ElmHttpError(_resp(403, ewm_403))).items() if k != "detalhe"} == {
        "codigo": "precondicao", "campo": "Ambiente"}
    assert falha(ElmHttpError(_resp(404, "x")))["codigo"] == "nao_encontrado"
    assert falha(requests.ConnectionError("recusado"))["codigo"] == "credencial"
    assert falha(Erro("conflito", "maria", "dono")) == {"codigo": "conflito", "detalhe": "maria", "campo": "dono"}
    with pytest.raises(ZeroDivisionError):  # bug não vira envelope
        operacao(lambda: 1 / 0)()
