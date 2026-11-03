"""Bundle local (elm_*_local): leitura do disco, índice incremental, busca híbrida e o caso sem nada baixado."""
import os

import pytest

from conftest import chamar
from mcp_elm import embedder, local

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


def _doc(body: str) -> dict:
    return {"body": body, "type": "Caso de Uso", "id": "1", "title": "T", "folder": "f", "description": None}


def test_chunks_quebram_paragrafo_longo_por_linha(raiz):
    # lista numerada sem linha em branco (o caso que virava 1 único chunk): vira vários pedaços coesos
    body = "\n".join(f"{i:02d}. Passo do fluxo com descrição longa o suficiente para ocupar o documento."
                     for i in range(1, 61))
    parts = local.chunks(_doc(body))
    assert len(parts) >= 3
    assert max(len(p["text"]) for p in parts) <= local.CHUNK_MAX + 100  # prefixo de contexto + a linha que estourou
    corpo = "\n".join(p["text"].split("\n", 1)[1] for p in parts)
    assert "01. " in corpo and "60. " in corpo  # nada se perdeu na quebra


def test_chunks_de_tabela_repetem_o_header(raiz):
    tabela = "| Coluna A | Coluna B |\n| --- | --- |\n" + \
        "\n".join(f"| valor {i} | descrição do valor {i:02d} com texto suficiente |" for i in range(40))
    parts = local.chunks(_doc(tabela))
    assert len(parts) > 1
    assert all(p["text"].split("\n", 1)[1].startswith("| Coluna A | Coluna B |\n| --- | --- |\n") for p in parts)


def test_indexacao_incremental(projeto, raiz, monkeypatch):
    assert call("elm_indexar_local", projeto) == {"documentos": 3, "restantes": 0, "indexando": False}
    assert call("elm_indexar_local", projeto)["restantes"] == 0
    with open(os.path.join(raiz, RN), "a", encoding="utf-8") as f:
        f.write("\nNovo parágrafo.\n")
    os.remove(os.path.join(raiz, LOGIN))
    assert call("elm_indexar_local", projeto) == {"documentos": 2, "restantes": 0, "indexando": False}
    with local.connect(raiz) as con:
        assert {p for (p,) in con.execute("select path from docs")} == {RN, UC}  # o apagado saiu
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


def test_troca_de_embedder_limpa_e_refaz_o_indice(projeto, raiz, monkeypatch):
    call("elm_indexar_local", projeto)
    with local.connect(raiz) as con:
        assert con.execute("select v from meta where k = 'embedder'").fetchone()[0] == f"local:{embedder.LOCAL_MODEL}"
        cid_max = con.execute("select max(cid) from chunks").fetchone()[0]
    # assinatura mudou (troca de modelo: dimensão dos vetores muda) -> índice inteiro renasce
    monkeypatch.setattr(embedder, "assinatura", lambda: "api:text-embedding-3-small@https://api.test/v1")
    assert call("elm_indexar_local", projeto) == {"documentos": 3, "restantes": 0, "indexando": False}
    with local.connect(raiz) as con:
        assert con.execute("select v from meta where k = 'embedder'").fetchone()[0] == \
            "api:text-embedding-3-small@https://api.test/v1"
        assert con.execute("select min(cid) from chunks").fetchone()[0] > cid_max  # nada do índice antigo
        assert {p for (p,) in con.execute("select path from docs")} == {UC, RN, LOGIN}
    assert call("elm_buscar_req_local", projeto, texto="CPF")["itens"]  # e a busca segue funcionando


def test_mudanca_de_chunking_limpa_e_refaz_o_indice(projeto, raiz, monkeypatch):
    call("elm_indexar_local", projeto)
    with local.connect(raiz) as con:
        assert con.execute("select v from meta where k = 'chunking'").fetchone()[0] == str(local.CHUNK_VER)
        cid_max = con.execute("select max(cid) from chunks").fetchone()[0]
    # versão do chunk mudou (formato do chunk: os vetores foram feitos sobre o texto antigo) -> renasce
    monkeypatch.setattr(local, "CHUNK_VER", local.CHUNK_VER + 1)
    assert call("elm_indexar_local", projeto) == {"documentos": 3, "restantes": 0, "indexando": False}
    with local.connect(raiz) as con:
        assert con.execute("select min(cid) from chunks").fetchone()[0] > cid_max  # nada do formato antigo
        assert con.execute("select v from meta where k = 'chunking'").fetchone()[0] == str(local.CHUNK_VER)


def test_type_boost_ajusta_o_ranking_e_o_score(projeto, raiz, monkeypatch):
    """3º estágio (pós-rerank): o pool inteiro multiplica pelo fator do type antes do slicing; score = ajustado."""
    with open(os.path.join(raiz, "03-Regras/2003-rn-cotacao-do-dia.md"), "w", encoding="utf-8") as f:
        f.write('---\ntype: Regra\ntitle: RN - Cotação do dia\nid: 2003\n---\nA cotação do dia é aplicada sobre o saldo.\n')
    REGRA = "03-Regras/2003-rn-cotacao-do-dia.md"
    call("elm_indexar_local", projeto)

    def falso_rerank(texto, hits):  # sem o boost, a Regra leva 0.01 de vantagem sobre a UC
        for h in hits:
            h["score"] = {"Caso de Uso": 0.92, "Regra": 0.93}.get(h["type"], 0.91)
        return sorted(hits, key=lambda h: -h["score"]), None
    monkeypatch.setattr(local, "rerank", falso_rerank)
    d = call("elm_buscar_req_local", projeto, texto="cotação saldo")
    itens = {i["path"]: i for i in d["itens"]}
    assert [i["path"] for i in d["itens"][:2]] == [UC, REGRA]  # 0.92×1.03 > 0.93×1.01: a UC sobe
    assert itens[UC]["score"] == round(0.92 * local.TYPE_BOOST["Caso de Uso"], 4)
    assert itens[REGRA]["score"] == round(0.93 * local.TYPE_BOOST["Regra"], 4)
    assert itens[RN]["score"] == 0.91  # "Regra de Negócio" não é "Regra": a chave é o type exato
    monkeypatch.setattr(local, "TYPE_BOOST", {})  # sem o boost, a Regra volta à frente
    assert call("elm_buscar_req_local", projeto, texto="cotação saldo")["itens"][0]["path"] == REGRA


def test_busca_com_rerank_reordena_o_top(projeto, raiz, monkeypatch):
    call("elm_indexar_local", projeto)
    assert call("elm_buscar_req_local", projeto, texto="módulo 11 dígitos verificadores")["itens"][0]["path"] == RN

    def falso_rerank(texto, hits):  # o documento com "portal" no texto (LOGIN) salta para a frente
        ordem = sorted(range(len(hits)), key=lambda i: 0 if "portal" in hits[i]["text"].lower() else 1)
        reordenados = [hits[i] for i in ordem]
        for pos, h in enumerate(reordenados):
            h["score"] = 1.0 - pos * 0.01
        return reordenados, None

    monkeypatch.setattr(local, "rerank", falso_rerank)
    d = call("elm_buscar_req_local", projeto, texto="módulo 11 dígitos verificadores")
    assert d["itens"][0]["path"] == LOGIN and d["itens"][0]["score"] == 1.0
    assert d["itens"] == sorted(d["itens"], key=lambda i: -i["score"])


def test_busca_sobrevive_a_falha_do_rerank(projeto, raiz, monkeypatch):
    """O rerank é refinamento: falhou na API, a busca segue na ordem RRF e avisa com erro_rerank."""
    from mcp_elm.contrato import Erro
    call("elm_indexar_local", projeto)

    def explode(texto, docs):
        raise Erro("indisponivel", "api de embeddings: boom")
    monkeypatch.setattr(embedder, "rerank", explode)
    d = call("elm_buscar_req_local", projeto, texto="módulo 11 dígitos verificadores")
    assert d["itens"] and d["itens"][0]["path"] == RN  # ordem RRF preservada
    assert "boom" in d["erro_rerank"]


def test_rerank_trunca_chunks_acima_do_limite(projeto, raiz, monkeypatch):
    """Chunk maior que RERANK_MAX (contexto de 8192 tokens do modelo) vai truncado para a API."""
    with open(os.path.join(raiz, "03-Regras/2002-rn-bloco-gigante.md"), "w", encoding="utf-8") as f:
        f.write(f'---\ntype: Regra de Negócio\ntitle: RN - Bloco gigante\nid: 2002\n---\nx * {local.RERANK_MAX + 500}\n')
    call("elm_indexar_local", projeto)
    vistos: list[str] = []

    def espiao(texto, docs):
        vistos.extend(docs)
        return [0.5] * len(docs)
    monkeypatch.setattr(embedder, "rerank", espiao)
    d = call("elm_buscar_req_local", projeto, texto="x")
    assert d["itens"] and vistos and all(len(t) <= local.RERANK_MAX for t in vistos)


def test_indexacao_em_segundo_plano_responde_na_hora(projeto, raiz, monkeypatch):
    """O 1º uso baixa o modelo (minutos): a chamada não espera; busca o que já há e avisa o que falta."""
    import threading
    from conftest import fake_embed
    solta = threading.Event()
    monkeypatch.setattr(local, "SINCRONO", False)
    monkeypatch.setattr(local, "embed", lambda t: (solta.wait(5), fake_embed(t))[1])
    assert call("elm_indexar_local", projeto) == {"documentos": 3, "restantes": 3, "indexando": True}
    assert call("elm_buscar_req_local", projeto, texto="CPF") == {"itens": [], "indexando": True, "restantes": 3}
    solta.set()
    local.aguardar(raiz)
    assert call("elm_indexar_local", projeto) == {"documentos": 3, "restantes": 0, "indexando": False}
    assert call("elm_buscar_req_local", projeto, texto="CPF")["itens"]


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
