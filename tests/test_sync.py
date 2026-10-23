import os

import pytest

from conftest import SERVER, chamar
from elm_fake import Elm
from mcp_elm import sync


@pytest.fixture
def elm(fake):
    e = Elm(fake)
    e.req(1, "REG-01 Validar CPF", f'Usa <span><a class="embedded" href="{SERVER}/rm/resources/_R2"> </a></span>.')
    e.req(2, "REG-02 CPF", "Texto antigo")
    return e


def dados(r):
    assert r["ok"], r
    return r["dados"]


def _editar(path: str, antigo: str, novo: str):
    texto = open(path, encoding="utf-8").read()
    assert antigo in texto
    open(path, "w", encoding="utf-8").write(texto.replace(antigo, novo))


def test_sync_duas_vias_com_conflito(elm, projeto):
    raiz = os.path.join(projeto, "docs/alm")
    assert dados(chamar("elm_sync", projeto=projeto, direcao="plano")) == {"baixar": [1, 2], "enviar": [], "conflitos": []}
    assert dados(chamar("elm_sync", projeto=projeto, direcao="baixar")) == {"feitos": [1, 2], "conflitos": []}
    um, dois = os.path.join(raiz, "03-Regras/1-reg-01-validar-cpf.md"), os.path.join(raiz, "03-Regras/2-reg-02-cpf.md")
    assert "Usa [2 REG-02 CPF](2-reg-02-cpf.md)." in open(um, encoding="utf-8").read()  # embed do bundle = arquivo
    assert os.path.exists(os.path.join(raiz, "index.md")) and os.path.exists(os.path.join(raiz, "log.md"))
    assert {r["status"] for r in sync.read_sync(raiz).values()} == {"sincronizado"}
    # o baixar já indexou o bundle local: nada pendente
    assert dados(chamar("elm_indexar_local", projeto=projeto)) == {"documentos": 2, "indexados": 0, "removidos": 0,
                                                                   "restantes": 0}

    _editar(dois, "Texto antigo", "Texto novo")  # só o md mudou -> enviar
    _editar(um, "Usa", "Usa sempre")  # md e ELM mudaram -> conflito
    elm.reqs[1]["modificado"] = "2026-01-05T00:00:00Z"
    d = dados(chamar("elm_sync", projeto=projeto, direcao="plano"))
    assert d["enviar"] == [2] and d["baixar"] == [] and [c["id"] for c in d["conflitos"]] == [1]

    assert dados(chamar("elm_sync", projeto=projeto, direcao="enviar")) == {"feitos": [2], "conflitos": []}
    assert elm.reqs[2]["texto"] == "<p>Texto novo</p>" and sync.read_sync(raiz)["2"]["status"] == "sincronizado"

    # o conflito: a pessoa escolhe a versão do ELM
    assert dados(chamar("elm_sync", projeto=projeto, direcao="baixar", ids=[1])) == {"feitos": [1], "conflitos": []}
    assert "Usa sempre" not in open(um, encoding="utf-8").read()
    assert dados(chamar("elm_sync", projeto=projeto, direcao="plano")) == {"baixar": [], "enviar": [], "conflitos": []}


def test_enviar_recusa_se_o_elm_mudou_depois_do_plano(elm, projeto):
    assert dados(chamar("elm_sync", projeto=projeto, direcao="baixar"))["feitos"] == []  # sem plano, fila vazia
    chamar("elm_sync", projeto=projeto, direcao="plano")
    chamar("elm_sync", projeto=projeto, direcao="baixar")
    _editar(os.path.join(projeto, "docs/alm/03-Regras/2-reg-02-cpf.md"), "Texto antigo", "Texto novo")
    assert dados(chamar("elm_sync", projeto=projeto, direcao="plano"))["enviar"] == [2]
    elm.reqs[2]["modificado"] = "2026-03-01T00:00:00Z"
    assert dados(chamar("elm_sync", projeto=projeto, direcao="enviar")) == {"feitos": [], "conflitos": [2]}
    assert elm.reqs[2]["texto"] == "Texto antigo"
    with pytest.raises(sync.Erro):  # pelo MCP o Literal já recusa antes
        sync.sync({}, "subir")


def test_baixar_no_limite_de_tempo_avisa_o_que_falta_indexar(elm, projeto, monkeypatch):
    from mcp_elm import local
    monkeypatch.setattr(local, "TIME_BUDGET", -1)
    chamar("elm_sync", projeto=projeto, direcao="plano")
    assert dados(chamar("elm_sync", projeto=projeto, direcao="baixar")) == {
        "feitos": [1, 2], "conflitos": [], "indexando": True, "restantes": 2}
