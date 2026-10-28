"""Servidor MCP do IBM ELM para o power sdd: operações; a configuração vem do pa do projeto, não da LLM."""
from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from . import ewm, local, projeto as prj, qm as etm, rm as dng, sync
from .contrato import Erro, operacao
from .http import get_session

mcp = MCPServer("elm", instructions=(
    "IBM ELM (EWM, DOORS Next, ETM) pelo pa do projeto. Toda tool (menos elm_setup_descobrir) recebe `projeto` = "
    "caminho absoluto do workspace e `pa` = nome do .kiro/config/sdd/pa_<nome>.json (omita se houver só um). Passe só "
    "ids e conteúdo: campos, URLs e ids internos vêm do pa. Resposta: {\"ok\":true,\"dados\":{...}} ou "
    "{\"ok\":false,\"erro\":{\"codigo\",\"detalhe\",\"campo\"}}; elm_ler_wi e elm_ler_req devolvem Markdown. "
    "Requisitos: consulte primeiro o bundle local (elm_*_local); erro nao_encontrado campo bundle = nada baixado, "
    "use elm_buscar_req/elm_ler_req."))

Projeto = Annotated[str, Field(description="caminho absoluto do workspace")]
Pa = Annotated[str | None, Field(description="nome do pa (só se houver vários)")]
Id = Annotated[int, Field(description="id do work item")]
Tipo = Literal["ib", "defeito", "tarefa"]


def _enxuto(schema):
    """Tira do inputSchema o que só repete o nome (title) e o `anyOf [X, null]`/`default null` dos opcionais (o que
    não está em `required` já é opcional): o schema vai em toda sessão."""
    if isinstance(schema, list):
        return [_enxuto(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    s = {k: _enxuto(v) for k, v in schema.items() if k != "title" and not (k == "default" and v is None)}
    if (alt := s.get("anyOf")) and {"type": "null"} in alt and len(alt) == 2:
        s.pop("anyOf")
        s.update(next(a for a in alt if a != {"type": "null"}))
    return s


def tool(fn):
    """Registra a tool com o envelope do contrato; sem structuredContent nem outputSchema (só um TextContent)."""
    mcp.tool(structured_output=False)(operacao(fn))
    registrada = mcp._tool_manager._tools[fn.__name__]
    registrada.parameters = _enxuto(registrada.parameters)
    return fn


@tool
def elm_status(projeto: Projeto, pa: Pa = None) -> str:
    """Confere a conexão e o pa. → dados: {login, servidor, pas[], valido, faltando[]}"""
    try:
        faltando = prj.faltando(prj.ler(prj.arquivo(projeto, pa)))
    except Erro as e:
        if e.codigo == "entrada" and e.campo == "projeto":
            raise
        faltando = [e.campo or "pa"]
    return {"login": ewm.whoami(), "servidor": get_session().cfg.server, "pas": prj.nomes(projeto),
            "valido": not faltando, "faltando": faltando}


@tool
def elm_ler_wi(projeto: Projeto, id: Id,
               comentarios: Annotated[int, Field(description="últimos N; 0 nenhum, -1 todos")] = 3,
               pa: Pa = None) -> str:
    """Lê um work item. → Markdown: YAML {id, tipo, titulo, estado, dono, pai, iteracao, estimativa_h, campos,
    links, url} + descrição + comentários"""
    return ewm.ler_wi(prj.carregar(projeto, pa), id, comentarios)


@tool
def elm_listar_wi(projeto: Projeto,
                  iteracao: Annotated[str | None, Field(description="nome da iteração ou caminho <pai>/<sprint>")] = None,
                  dono: Annotated[str | None, Field(description="login ou 'eu'")] = None,
                  tipo: Tipo | None = None, pai: Annotated[int | None, Field(description="id do pai")] = None,
                  titulo_prefixo: str | None = None, estado: str | None = None, limite: int = 50,
                  pa: Pa = None) -> str:
    """Lista work items (exige iteracao, dono ou pai). → dados: {itens[{id, tipo, titulo, estado, dono}], truncado,
    total}"""
    return ewm.listar_wi(prj.carregar(projeto, pa), iteracao, dono, tipo, pai, titulo_prefixo, estado, limite)


@tool
def elm_criar_wi(projeto: Projeto, tipo: Tipo, titulo: str, pai: int | None = None,
                 dono: Annotated[str | None, Field(description="login ou 'eu'")] = None,
                 estimativa_h: float | None = None, iteracao: str | None = None, descricao_md: str | None = None,
                 campos: Annotated[dict[str, str] | None, Field(description="{campo do pa: valor/título}")] = None,
                 pa: Pa = None) -> str:
    """Cria um work item com os padrões do pa (padroes, por-prefixo do título, categoria do time).
    → dados: {id, url, valores_usados}"""
    return ewm.criar_wi(prj.carregar(projeto, pa), tipo, titulo, pai, dono, estimativa_h, iteracao, descricao_md,
                        campos)


@tool
def elm_atualizar_wi(projeto: Projeto, ids: list[int], titulo: str | None = None, descricao_md: str | None = None,
                     pai: int | None = None, iteracao: str | None = None, dono: str | None = None,
                     estimativa_h: float | None = None,
                     campos: Annotated[dict[str, str] | None, Field(description="{campo do pa: valor/título}")] = None,
                     pa: Pa = None) -> str:
    """Atualiza um ou vários work items (ex.: só iteracao na virada). → dados: {atualizados[], falhas[{id, codigo,
    detalhe}]}"""
    return ewm.atualizar_wi(prj.carregar(projeto, pa), ids, titulo, descricao_md, pai, iteracao, dono, estimativa_h,
                            campos)


@tool
def elm_transicao(projeto: Projeto, id: Id, alvo: Literal["assumir", "concluir", "reabrir"],
                  estimativa_h: float | None = None,
                  forcar: Annotated[bool, Field(description="assumir de outro dono")] = False,
                  iniciar_pai: Annotated[bool, Field(description="leva o pai novo a em andamento")] = False,
                  pa: Pa = None) -> str:
    """Leva o work item pelo workflow (assumir = meu e em andamento). → dados: {id, estado, dono, ja_estava,
    pai?{id, estado}}"""
    return ewm.transicao(prj.carregar(projeto, pa), id, alvo, estimativa_h, forcar, iniciar_pai)


@tool
def elm_comentar(projeto: Projeto, id: Id, texto_md: str, pa: Pa = None) -> str:
    """Comenta num work item. → dados: {id}"""
    return ewm.comentar(prj.carregar(projeto, pa), id, texto_md)


@tool
def elm_anexo(projeto: Projeto, id: Id, acao: Literal["listar", "enviar", "baixar"],
              caminho: Annotated[str | None, Field(
                  description="enviar: arquivo; baixar: <pasta>/<nome do anexo>; relativo ao projeto")] = None,
              pa: Pa = None) -> str:
    """Anexos do work item. → dados: listar {anexos[{nome, tamanho}]}; enviar {nome}; baixar {caminho}"""
    return ewm.anexo(prj.carregar(projeto, pa), id, acao, caminho)


@tool
def elm_horas(projeto: Projeto, id: int | None = None, iteracao: str | None = None, pa: Pa = None) -> str:
    """Horas lançadas num WI ou nas tarefas da iteração. → dados: id {lancamentos[{data, horas, pessoa}], total_h};
    iteracao {linhas[{tarefa_id, prefixo, pessoa, horas}], total_h}"""
    return ewm.horas(prj.carregar(projeto, pa), id, iteracao)


@tool
def elm_lancar_horas(projeto: Projeto, id: Id, horas: float,
                     data: Annotated[str | None, Field(description="AAAA-MM-DD; padrão hoje")] = None,
                     pa: Pa = None) -> str:
    """Lança horas (só o dono; até 16h/dia; sem data futura). → dados: {lancado{data, horas}, total_h}"""
    return ewm.lancar_horas(prj.carregar(projeto, pa), id, horas, data)


@tool
def elm_remover_horas(projeto: Projeto, id: Id,
                      data: Annotated[str | None, Field(description="AAAA-MM-DD; padrão hoje")] = None,
                      pa: Pa = None) -> str:
    """Remove os seus lançamentos de horas numa data. → dados: {removidos[{data, horas}], total_h}"""
    return ewm.remover_horas(prj.carregar(projeto, pa), id, data)


@tool
def elm_ligar(projeto: Projeto, wi: int | None = None, req: int | None = None, ct: int | None = None,
              wi_destino: int | None = None,
              tipo: Literal["implementa", "rastreia", "afeta", "testado_por", "valida", "depende", "relacionado"]
              | None = None, pa: Pa = None) -> str:
    """Liga wi+req (implementa), wi+ct (testado_por), ct+req (valida) ou wi+wi_destino (depende). → dados:
    {ja_existia}"""
    return etm.ligar(prj.carregar(projeto, pa), wi, req, ct, wi_destino, tipo)


@tool
def elm_buscar_req(projeto: Projeto, texto: str, limite: int = 20, pa: Pa = None) -> str:
    """Busca requisitos por texto. → dados: {itens[{id, titulo, tipo, modificado}], truncado}"""
    return dng.buscar(prj.carregar(projeto, pa), texto, limite)


@tool
def elm_ler_req(projeto: Projeto, id: Annotated[int, Field(description="id do requisito")], pa: Pa = None) -> str:
    """Lê um requisito. → Markdown: YAML {id, titulo, tipo, pasta, modificado, url, links} + corpo"""
    return dng.ler(prj.carregar(projeto, pa), id)


@tool
def elm_publicar_req(projeto: Projeto, sigla: Annotated[str, Field(description="chave de rm.artefatos")],
                     titulo: str,
                     texto_md: Annotated[str, Field(description="[x](<id>) cita outro requisito")],
                     id: int | None = None,
                     modificado_em: Annotated[str | None, Field(description="ISO da leitura; ELM mais novo = conflito")]
                     = None,
                     embutir: Annotated[list[int] | None, Field(description="ids citados que viram embed")] = None,
                     pa: Pa = None) -> str:
    """Cria (sem id) ou atualiza um requisito no tipo e pasta da sigla. → dados: {id, url, acao}"""
    return dng.publicar(prj.carregar(projeto, pa), sigla, titulo, texto_md, id, modificado_em,
                       [str(i) for i in embutir] if embutir is not None else None)


@tool
def elm_criar_ct(projeto: Projeto, titulo: str, cenario_md: str, reqs: list[int], wi: int | None = None,
                 pa: Pa = None) -> str:
    """Cria caso de teste no ETM já ligado: valida reqs; wi testado por ele. → dados: {id, url}"""
    return etm.criar_ct(prj.carregar(projeto, pa), titulo, cenario_md, reqs, wi)


@tool
def elm_iteracao(projeto: Projeto, nome: str, inicio: str | None = None, fim: str | None = None,
                 plano: str | None = None,
                 pa: Pa = None) -> str:
    """Acha ou cria a iteração da sprint e o plano (`plano`, só ao criar; padrão: o nome dela); grava no pa. `nome`:
    "<pai>/<sprint>" na base (pai da iteração do backlog). → dados: {id, pai, planos, criados[]}"""
    return ewm.iteracao(prj.carregar(projeto, pa), nome, inicio, fim, plano)


@tool
def elm_backlog(projeto: Projeto,
                plano: Annotated[str | None, Field(description="itemId ou link do plano existente")] = None,
                nome: Annotated[str | None, Field(description="iteração a achar ou criar")] = None,
                pai: Annotated[str | None, Field(description="procura e cria só dentro dele (sem: topo)")] = None,
                pa: Pa = None) -> str:
    """Backlog onde os itens nascem (iteracao "backlog"; padrão <base>/Backlog). Sem plano/nome: lista;
    senão usa ou cria a iteração e o plano de backlog e grava no pa. → dados: {atual, candidatos[]} | {nome, id, plano, criados[]}"""
    return ewm.backlog(prj.carregar(projeto, pa), plano, nome, pai)


@tool
def elm_sync(projeto: Projeto, direcao: Literal["plano", "baixar", "enviar"], ids: list[int] | None = None,
             pa: Pa = None) -> str:
    """Bundle do DOORS Next em rm.download (indexar é à parte: elm_indexar_local). → dados: plano {baixar[],
    enviar[], conflitos[]}; baixar/enviar {feitos[], conflitos[], erros?}"""
    return sync.sync(prj.carregar(projeto, pa), direcao, ids)


Pasta = Annotated[str | None, Field(description="1ª pasta do path")]
TipoReq = Annotated[str | None, Field(description="type do frontmatter")]


@tool
def elm_buscar_req_local(projeto: Projeto, texto: str, limite: int = 8, pasta: Pasta = None, tipo: TipoReq = None,
                         pa: Pa = None) -> str:
    """Busca híbrida (sentido + palavra) no bundle baixado; o que mudou indexa em segundo plano. → dados: {itens[{path, id,
    titulo, tipo, pasta, score, trechos[{secao, texto}]}], indexando?, restantes?}"""
    return local.buscar(prj.carregar(projeto, pa), texto, limite, pasta, tipo)


@tool
def elm_ler_req_local(projeto: Projeto, id: Annotated[int | str, Field(description="id do requisito ou path")],
                      pa: Pa = None) -> str:
    """Lê um documento do bundle baixado. → dados: {path, markdown, links{saida[], entrada[]: {rel, path, id, titulo}}}"""
    return local.ler(prj.carregar(projeto, pa), id)


@tool
def elm_listar_req_local(projeto: Projeto, pasta: Pasta = None, tipo: TipoReq = None, pa: Pa = None) -> str:
    """Inventário do bundle baixado (sem corpo). → dados: {itens[{path, id, titulo, tipo, pasta, descricao, indexado}]}"""
    return local.listar(prj.carregar(projeto, pa), pasta, tipo)


@tool
def elm_indexar_local(projeto: Projeto, forcar: Annotated[bool, Field(description="reindexa tudo")] = False,
                      pa: Pa = None) -> str:
    """Indexa o bundle baixado em segundo plano e responde na hora; chame de novo para ver o progresso.
    → dados: {documentos, restantes, indexando, erro_indice?}"""
    return local.indexar(prj.carregar(projeto, pa), forcar)


@tool
def elm_setup_descobrir(ccm: Annotated[str, Field(description="trecho do nome da PA do EWM")],
                        rm: Annotated[str | None, Field(description="trecho da PA do DOORS Next")] = None,
                        qm: Annotated[str | None, Field(description="trecho da PA do ETM")] = None,
                        valores_de: Annotated[list[str] | None, Field(
                            description="campos não obrigatórios cujos valores de enumeração também vêm")] = None) -> str:
    """Dados para montar o pa (sem projeto): tipos do fluxo, valores só dos obrigatórios (e de valores_de), 6 iterações
    recentes. → dados: {ccm{pa, times, tipos, outros_tipos, links, iteracoes, iteracoes_total}, rm?, qm?}"""
    out = {"ccm": ewm.descobrir(ccm, valores_de)}
    if rm:
        out["rm"] = dng.descobrir(rm)
    if qm:
        out["qm"] = etm.descobrir(qm)
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
