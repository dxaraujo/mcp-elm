"""Servidor MCP do IBM ELM (DOORS Next, EWM, ETM).

As tools são os serviços de mcp_alm/ibm/ (nomes e parâmetros do IBM Engineering AI Hub 1.3.0),
registrados com `@tool`; o encanamento HTTP/OSLC fica em mcp_alm/infra/.
"""
from __future__ import annotations

import functools

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer(
    "alm",
    instructions=(
        "Acesso ao IBM ELM com as tools do IBM Engineering AI Hub (mesmos nomes e parâmetros). "
        "Descubra UUIDs com list_project_areas(app_type) e get_project_area; componentes com "
        "get_project_components (rm) e get_qm_component (qm). Recursos OSLC vêm como "
        "{url, id, title, types, properties{}, links{}} com chaves em qname (ex.: dcterms:title)."
    ),
)

# RuntimeError: AlmHttpError/AuthError; OSError: arquivo de config e falhas de rede do requests
EXPECTED = (RuntimeError, OSError, LookupError, ValueError)


def tool(fn):
    """Registra `fn` como tool e devolve `fn` intacta (chamadas internas veem as exceções originais).

    O MCP SDK 2.x esconde o texto de exceções que não são ToolError; as skills precisam dele.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except EXPECTED as exc:
            raise ToolError(str(exc)) from exc

    mcp.tool()(wrapper)
    return fn


# importados depois de `tool` existir: cada módulo registra suas tools ao ser carregado
from . import ccm, qm, rm  # noqa: E402,F401
from .ibm import common, requirements, test, workitems  # noqa: E402,F401


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
