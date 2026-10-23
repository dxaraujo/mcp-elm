"""Bundle local (elm_*_local): leitura do disco, índice incremental, busca híbrida e o caso sem nada baixado."""
import os

import pytest

from conftest import chamar
from mcp_elm import local

UC = "03-Casos de Uso/2010-uc-cadastrar-cliente.md"
RN = "03-Regras/2001-rn-validar-cpf-do-cliente.md"
LOGIN = "01-Requisitos/10-login-do-cliente.md"
URL = "https://elm.test/rm/resources"
# bundle no formato do elm_sync: {caminho relativo: conteúdo}
BUNDLE = {
    LOGIN: f'---\ntype: Requisito\ntitle: Login do cliente\ndescription: "Acesso ao portal com CPF e senha."\nresource: "{URL}/TX_10"\nsources:\n  - id: doors-next\n    last_modified: "2026-01-10T13:05:00Z"\nid: 10\n---\nO cliente acessa o portal informando CPF e senha. O CPF é validado conforme [2001 RN - Validar CPF do cliente](../03-Regras/2001-rn-validar-cpf-do-cliente.md).\n',
    UC: f'---\ntype: Caso de Uso\ntitle: UC - Cadastrar cliente\nresource: "{URL}/TX_2010"\nid: 2010\nlinks:\n  Vincular A:\n    - "10: Login do cliente"\n    - "9999: Fora do bundle"\n---\n## Fluxo Básico\n\n1. O usuário informa os dados do cliente.\n2. O sistema valida o documento: [2001 RN - Validar CPF do cliente]({URL}/TX_2001)\n3. Ver também [manual](https://example.com/manual.pdf) e [fora](../../segredo.md).\n',
    RN: f'---\ntype: Regra de Negócio\ntitle: RN - Validar CPF do cliente\nresource: "{URL}/TX_2001"\nid: 2001\n---\n## Regra\n\nO CPF deve ter 11 dígitos e dígitos verificadores válidos (módulo 11). CPFs com todos os dígitos iguais são rejeitados.\n\n## Mensagem\n\nExibir "CPF inválido" e bloquear o envio do formulário.\n',
    "index.md": '---\nokf_version: "0.2"\n---\n\n# 01-Requisitos\n\n* [10 — Login do cliente](</01-Requisitos/10-login-do-cliente.md>)\n',
    "03-Regras/index.md": "# Regra de Negócio\n\n* [2001 — RN - Validar CPF do cliente](2001-rn-validar-cpf-do-cliente.md)\n",
    "log.md": "# Log\n",
    "sync.md": "| Artefato | Pasta | Última atualização ELM | Hash | Status |\n|---|---|---|---|---|\n",
}


@pytest.fixture
def raiz(projeto):
    dest = os.path.join(projeto, "docs/alm")
    for rel, text in BUNDLE.items():
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        with open(os.path.join(dest, rel), "w", encoding="utf-8") as f:
            f.write(text)
    return dest


def dados(r):
    assert r["ok"], r
    return r["dados"]


def call(tool, projeto, **args):
    return dados(chamar(tool, projeto=projeto, **args))


def test_sem_bundle_indica_o_remoto(projeto):
    for tool, args in (("elm_buscar_req_local", {"texto": "CPF"}), ("elm_ler_req_local", {"id": 2001}),
                       ("elm_listar_req_local", {}), ("elm_indexar_local", {})):
        r = chamar(tool, projeto=projeto, **args)
        assert r["ok"] is False and r["erro"]["codigo"] == "nao_encontrado" and r["erro"]["campo"] == "bundle", tool
        assert "elm_sync" in r["erro"]["detalhe"] and "elm_buscar_req" in r["erro"]["detalhe"]
    os.makedirs(os.path.join(projeto, "docs/alm/01-Requisitos"))  # pasta existe, sem .md: o mesmo erro
    assert chamar("elm_listar_req_local", projeto=projeto)["erro"]["campo"] == "bundle"


def test_scan_le_frontmatter_aninhado_e_pula_arquivos_do_bundle(raiz):
    docs = local.scan(raiz)
    assert set(docs) == {UC, RN, LOGIN}
    assert (docs[UC]["id"], docs[UC]["type"], docs[UC]["folder"]) == ("2010", "Caso de Uso", "03-Casos de Uso")
    ids = local.by_id(docs)
    assert local.links(docs[UC], docs, ids) == [{"rel": "Vincular A", "path": LOGIN}, {"rel": "cita", "path": RN}]
    assert local.resolve(UC, "../../segredo.md", {}, docs) is None


def test_chunks_por_heading_e_paragrafo(raiz):
    parts = local.chunks(local.scan(raiz)[RN])
    assert [c["section"] for c in parts] == ["Regra", "Mensagem"]
    assert parts[0]["text"].startswith("Regra de Negócio 2001 — RN - Validar CPF do cliente | 03-Regras | Regra\n")
    doc = {"body": "\n\n".join("p" * 900 for _ in range(3)), "type": None, "id": None, "title": "T", "folder": "",
           "description": None}
    assert len(local.chunks(doc)) == 3


def test_indexacao_incremental(projeto, raiz, monkeypatch):
    assert call("elm_indexar_local", projeto) == {"documentos": 3, "indexados": 3, "removidos": 0, "restantes": 0}
    assert call("elm_indexar_local", projeto)["indexados"] == 0
    with open(os.path.join(raiz, RN), "a", encoding="utf-8") as f:
        f.write("\nNovo parágrafo.\n")
    os.remove(os.path.join(raiz, LOGIN))
    assert call("elm_indexar_local", projeto) == {"documentos": 2, "indexados": 1, "removidos": 1, "restantes": 0}
    assert call("elm_indexar_local", projeto, forcar=True)["indexados"] == 2
    monkeypatch.setattr(local, "TIME_BUDGET", -1)
    assert call("elm_indexar_local", projeto, forcar=True)["restantes"] == 2
    assert os.path.isfile(os.path.join(raiz, "sqlite3", "okf.db"))
    assert open(os.path.join(raiz, "sqlite3", ".gitignore")).read() == "*\n"


def test_busca_indexa_antes_e_filtra(projeto, raiz):
    d = call("elm_buscar_req_local", projeto, texto="CPF dígitos verificadores módulo 11")  # sem indexar antes
    hit = d["itens"][0]
    assert "indexando" not in d and hit["path"] == RN and hit["trechos"][0]["secao"] == "Regra"
    assert hit["trechos"][0]["texto"].startswith("## Regra")  # sem o prefixo de contexto
    assert {h["pasta"] for h in call("elm_buscar_req_local", projeto, texto="CPF", pasta="01-Requisitos")["itens"]} \
        == {"01-Requisitos"}
    assert call("elm_buscar_req_local", projeto, texto="CPF", tipo="Caso de Uso")["itens"][0]["path"] == UC
    assert call("elm_buscar_req_local", projeto, texto="verificador")["itens"][0]["path"] == RN  # prefixo, sem acento
    assert chamar("elm_buscar_req_local", projeto=projeto, texto='aspas " e OR')["ok"]  # FTS5 não vaza
    assert chamar("elm_buscar_req_local", projeto=projeto, texto="  ")["erro"]["codigo"] == "entrada"
    os.remove(os.path.join(raiz, LOGIN))  # apagado do disco sai do índice na próxima busca
    assert all(h["path"] != LOGIN for h in call("elm_buscar_req_local", projeto, texto="login portal senha")["itens"])


def test_busca_no_limite_de_tempo_usa_o_que_tem(projeto, raiz, monkeypatch):
    monkeypatch.setattr(local, "TIME_BUDGET", -1)
    assert call("elm_buscar_req_local", projeto, texto="CPF") == {"itens": [], "indexando": True, "restantes": 3}


def test_ler_por_id_e_por_path(projeto, raiz):
    doc = call("elm_ler_req_local", projeto, id=2001)
    assert doc["path"] == RN and doc["markdown"].startswith("---\ntype: Regra de Negócio")
    assert {(x["rel"], x["path"]) for x in doc["links"]["entrada"]} == {("cita", UC), ("cita", LOGIN)}
    assert doc["links"]["saida"] == []
    assert call("elm_ler_req_local", projeto, id=UC)["links"]["saida"][0] == {
        "rel": "Vincular A", "path": LOGIN, "id": "10", "titulo": "Login do cliente"}
    r = chamar("elm_ler_req_local", projeto=projeto, id="../../etc/passwd")
    assert r["erro"]["codigo"] == "nao_encontrado" and "elm_ler_req" in r["erro"]["detalhe"]


def test_listar_com_filtros(projeto, raiz):
    itens = call("elm_listar_req_local", projeto)["itens"]
    assert {i["path"] for i in itens} == {UC, RN, LOGIN} and not any(i["indexado"] for i in itens)
    call("elm_indexar_local", projeto)
    assert all(i["indexado"] for i in call("elm_listar_req_local", projeto)["itens"])
    assert [i["path"] for i in call("elm_listar_req_local", projeto, pasta="03-Regras")["itens"]] == [RN]
    assert [i["id"] for i in call("elm_listar_req_local", projeto, tipo="Requisito")["itens"]] == ["10"]
