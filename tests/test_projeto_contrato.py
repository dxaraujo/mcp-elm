import json
import os
import shutil
from pathlib import Path

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
    shutil.copy(FIXTURES / "pa_alfa.json", os.path.join(projeto, ".sabia/config/elm/pa_beta.json"))
    e = _erro(lambda: prj.carregar(projeto))
    assert e["codigo"] == "entrada" and e["campo"] == "pa" and "alfa, beta" in e["detalhe"]
    assert prj.carregar(projeto, "beta")["ccm"]["nome"] == "Projeto Alfa"
    assert _erro(lambda: prj.carregar(projeto, "gama"))["codigo"] == "config"


def _pa_em(raiz, pasta: str, nome: str = "alfa", time: str | None = None) -> Path:
    arq = Path(raiz, pasta, f"pa_{nome}.json")
    arq.parent.mkdir(parents=True, exist_ok=True)
    dados = json.loads((FIXTURES / "pa_alfa.json").read_text("utf-8"))
    dados["ccm"]["nome"] = time or pasta
    arq.write_text(json.dumps(dados), "utf-8")
    return arq


@pytest.mark.parametrize("pasta", [".sabia/config/elm", ".kiro/config/elm", ".kiro/config/sdd"])
def test_pa_numa_pasta_so(tmp_path, pasta):
    _pa_em(tmp_path, pasta)
    cfg = prj.carregar(str(tmp_path))
    assert cfg["ccm"]["nome"] == pasta and cfg["_arquivo"] == str(tmp_path / pasta / "pa_alfa.json")
    assert prj.nomes(str(tmp_path)) == ["alfa"]


def test_pa_em_varias_pastas_vale_a_primeira(tmp_path):
    for pasta in (".kiro/config/sdd", ".kiro/config/elm", ".sabia/config/elm"):
        _pa_em(tmp_path, pasta)
    assert prj.carregar(str(tmp_path))["ccm"]["nome"] == ".sabia/config/elm"
    (tmp_path / ".sabia/config/elm/pa_alfa.json").unlink()
    assert prj.carregar(str(tmp_path))["ccm"]["nome"] == ".kiro/config/elm"
    _pa_em(tmp_path, ".kiro/config/sdd", "beta")
    e = _erro(lambda: prj.carregar(str(tmp_path)))
    assert e["codigo"] == "entrada" and "alfa, beta" in e["detalhe"] and "vale o da primeira" in e["detalhe"]


def test_pa_ausente_cita_pasta_nova_e_as_procuradas(tmp_path):
    e = _erro(lambda: prj.carregar(str(tmp_path)))
    assert e["codigo"] == "config" and "nenhum .sabia/config/elm/pa_" in e["detalhe"]
    assert ".kiro/config/elm" in e["detalhe"] and ".kiro/config/sdd" in e["detalhe"]
    _pa_em(tmp_path, ".kiro/config/elm")
    e = _erro(lambda: prj.carregar(str(tmp_path), "gama"))
    assert ".sabia/config/elm/pa_gama.json" in e["detalhe"] and ".kiro/config/sdd" in e["detalhe"]


def test_gravar_vai_sempre_para_a_pasta_nova(tmp_path):
    antigo = _pa_em(tmp_path, ".kiro/config/sdd")
    cfg = prj.carregar(str(tmp_path))
    prj.gravar_iteracao(cfg, "2026/Sprint 1", "_IT1", {"time": "_PL1"})
    novo = tmp_path / ".sabia/config/elm/pa_alfa.json"
    assert cfg["_arquivo"] == str(novo)
    assert json.loads(novo.read_text("utf-8"))["ccm"]["iteracoes"]["2026/Sprint 1"]["id"] == "_IT1"
    assert "2026/Sprint 1" not in json.loads(antigo.read_text("utf-8"))["ccm"].get("iteracoes", {})
    assert prj.carregar(str(tmp_path))["ccm"]["iteracoes"]["2026/Sprint 1"]["id"] == "_IT1"


def test_carregar_invalido_diz_o_campo(projeto):
    arq = os.path.join(projeto, ".sabia/config/elm/pa_alfa.json")
    dados = json.load(open(arq, encoding="utf-8"))
    del dados["ccm"]["horas"]["time-code"]
    json.dump(dados, open(arq, "w", encoding="utf-8"))
    e = _erro(lambda: prj.carregar(projeto))
    assert e == {"codigo": "config", "campo": "ccm.horas.time-code", "detalhe": e["detalhe"]}


def test_cache_por_mtime(projeto):
    arq = os.path.join(projeto, ".sabia/config/elm/pa_alfa.json")
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
    assert falha(requests.ConnectionError("recusado"))["codigo"] == "indisponivel"
    assert falha(requests.Timeout("lento"))["codigo"] == "indisponivel"
    assert falha(Erro("conflito", "maria", "dono")) == {"codigo": "conflito", "detalhe": "maria", "campo": "dono"}
    with pytest.raises(ZeroDivisionError):  # bug não vira envelope
        operacao(lambda: 1 / 0)()


def test_serialize_grava_data_como_o_ewm():
    """O rdflib relê 2026-10-23T14:17:54.000Z e o regrava +00:00, que o EWM recusa em atributo de data customizado."""
    from rdflib import Graph, Literal, URIRef
    from rdflib.namespace import XSD
    from mcp_elm import oslc
    g = Graph()
    g.add((URIRef("http://x/1"), URIRef("http://x/d"), Literal("2026-10-23T14:17:54+00:00", datatype=XSD.dateTime)))
    out = oslc.serialize(g).decode()
    assert "2026-10-23T14:17:54.000Z" in out and "+00:00" not in out


def test_schema_igual_ao_publicado():
    publicado = Path(__file__).resolve().parents[2] / "mcps/elm/pa.schema.json"
    if not publicado.exists():
        pytest.skip("repositório mcps não clonado ao lado")
    assert (Path(prj.__file__).with_name("pa.schema.json")).read_bytes() == publicado.read_bytes()


def test_mensagem_le_o_erro_da_api_openai():
    from mcp_elm.http import mensagem
    corpo = '{"error":{"message":"This model\'s maximum context length is 8192 tokens.","type":"invalid_request_error"}}'
    assert mensagem(corpo) == "This model's maximum context length is 8192 tokens."
