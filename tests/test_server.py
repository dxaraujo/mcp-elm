import asyncio
import json

from mcp_elm.server import mcp

TOOLS = {"elm_status", "elm_ler_wi", "elm_listar_wi", "elm_criar_wi", "elm_atualizar_wi", "elm_transicao",
         "elm_comentar", "elm_anexo", "elm_horas", "elm_lancar_horas", "elm_remover_horas", "elm_ligar", "elm_buscar_req", "elm_ler_req",
         "elm_publicar_req", "elm_criar_ct", "elm_iteracao", "elm_backlog", "elm_sync", "elm_setup_descobrir",
         "elm_buscar_req_local", "elm_ler_req_local", "elm_listar_req_local", "elm_indexar_local"}


def test_tools_list_enxuto_sem_output_schema():
    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == TOOLS
    assert sum(len(t.description or "") + len(json.dumps(t.input_schema)) for t in tools) <= 13_200  # medido: 13_096 com as 4 tools locais e a elm_backlog
    assert all(t.output_schema is None for t in tools)
    for t in tools:  # docstring termina com a forma da saída; projeto/pa em toda tool menos a descoberta
        assert "→" in t.description, t.name
        assert ("projeto" in t.input_schema["properties"]) == (t.name != "elm_setup_descobrir"), t.name
