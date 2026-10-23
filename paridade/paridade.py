"""Paridade 1.0.43 x 2.0: roda as operações equivalentes nos dois servidores contra o ELM real e compara.

    uv run python paridade/paridade.py <projeto> --pa <nome> --wi <id> --req <id> [--iteracao <nome>]
        [--pa-antigo <elm.json>] [--escrita --wi-teste <id>]

O 1.0.43 sai do git (commit a73e925) para um diretório temporário e roda em outro processo
(`uv run --project <tmp>/mcps/elm`); o 2.0 roda neste processo pelo MCP (como o cliente chama). O sync roda sobre
cópias temporárias do bundle: o do projeto não muda. Escritas só com --escrita, cada uma pedindo "sim", sempre no
--wi-teste. Não faz parte do pacote.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

COMMIT = "a73e925"
REPO = Path(__file__).resolve().parents[3]

# roda no processo do 1.0.43: lê [[nome, função, kwargs], ...] e devolve [{nome, resultado|erro}]
RUNNER = """
import json, sys
import mcp_elm.server  # registra os módulos na ordem do servidor antigo
from mcp_elm import ccm, rm
from mcp_elm.ibm import workitems
out = []
for nome, fn, kwargs in json.load(open(sys.argv[1])):
    mod, _, attr = fn.rpartition(".")
    try:
        out.append({"nome": nome, "resultado": getattr({"ccm": ccm, "rm": rm, "workitems": workitems}[mod], attr)(**kwargs)})
    except Exception as exc:
        out.append({"nome": nome, "erro": f"{type(exc).__name__}: {exc}"[:300]})
print(json.dumps(out, default=str, ensure_ascii=False))
"""


def extrair_antigo(destino: Path) -> Path:
    archive = subprocess.run(["git", "-C", str(REPO), "archive", COMMIT, "mcps/elm"], check=True, capture_output=True)
    destino.mkdir(parents=True, exist_ok=True)
    subprocess.run(["tar", "-x", "-C", str(destino)], input=archive.stdout, check=True)
    return destino / "mcps" / "elm"


def rodar_antigo(projeto_antigo: Path, chamadas: list) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        entrada, runner = Path(tmp) / "chamadas.json", Path(tmp) / "runner.py"
        entrada.write_text(json.dumps(chamadas), "utf-8")
        runner.write_text(RUNNER, "utf-8")
        proc = subprocess.run(["uv", "run", "--quiet", "--project", str(projeto_antigo), "python", str(runner),
                               str(entrada)], capture_output=True, text=True)
    if proc.returncode:
        sys.exit(f"1.0.43 falhou:\n{proc.stderr[-2000:]}")
    return {r["nome"]: r for r in json.loads(proc.stdout.splitlines()[-1])}


def chamar_novo(tool: str, **args):
    from mcp_elm.server import mcp
    texto = asyncio.run(mcp.call_tool(tool, {k: v for k, v in args.items() if v is not None})).content[0].text
    return texto, (json.loads(texto) if texto.startswith("{") else texto)


def cabecalho(md: str) -> dict:
    """Chaves de topo do YAML de um documento Markdown (só escalares)."""
    head = md.split("\n---\n", 1)[0].removeprefix("---\n") if md.startswith("---\n") else ""
    return {k.strip(): v.strip().strip('"') for line in head.splitlines() if not line.startswith(" ")
            for k, _, v in [line.partition(":")] if v.strip()}


def tamanho(valor) -> int:
    return len(valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False, default=str))


def comparar(nome: str, antigo: dict, novo_texto: str, essencial) -> tuple:
    if "erro" in antigo:
        return nome, "erro no antigo", antigo["erro"][:80], tamanho(antigo["erro"]), len(novo_texto)
    try:
        a, n = essencial(antigo["resultado"], novo_texto)
    except Exception as exc:  # resposta nova em erro ou fora do formato esperado
        return nome, "diferente", f"{type(exc).__name__}: {exc}"[:80], tamanho(antigo["resultado"]), len(novo_texto)
    return nome, "igual" if a == n else "diferente", "" if a == n else f"{a} x {n}"[:120], \
        tamanho(antigo["resultado"]), len(novo_texto)


def dados(texto: str) -> dict:
    env = json.loads(texto)
    if not env["ok"]:
        raise RuntimeError(env["erro"])
    return env["dados"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projeto", help="caminho absoluto do workspace com .kiro/config/sdd/pa_<nome>.json")
    ap.add_argument("--pa", required=True)
    ap.add_argument("--wi", required=True, help="work item para as leituras")
    ap.add_argument("--req", required=True, help="requisito para as leituras")
    ap.add_argument("--iteracao", help="iteração para listar e para o relatório de horas")
    ap.add_argument("--pa-antigo", help="elm.json do 1.0.43 (fields e link-types do ccm_get_workitem)")
    ap.add_argument("--escrita", action="store_true", help="roda também as escritas (pergunta antes de cada uma)")
    ap.add_argument("--wi-teste", help="work item onde as escritas acontecem (obrigatório com --escrita)")
    args = ap.parse_args()
    if args.escrita and not args.wi_teste:
        ap.error("--escrita exige --wi-teste")

    from mcp_elm import projeto as prj
    cfg = prj.carregar(args.projeto, args.pa)
    ccm, rmc = cfg["ccm"], cfg.get("rm", {})
    antigo_cfg = json.loads(Path(args.pa_antigo).read_text("utf-8")) if args.pa_antigo else {}
    tipos_antigos = antigo_cfg.get("workitem-types") or antigo_cfg.get("ccm", {}).get("workitem-types") or {}
    campos = next((t.get("fields") for t in tipos_antigos.values() if t.get("fields")), None) or {
        n: a for t in ccm["tipos"].values() for n, a in t["campos"].items()}
    links = antigo_cfg.get("link-types") or antigo_cfg.get("ccm", {}).get("link-types") or {}
    it_id = ccm.get("iteracoes", {}).get(args.iteracao, {}).get("id") if args.iteracao else None

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        velho = extrair_antigo(tmp / "antigo")
        # sync sobre cópias: o bundle do projeto não muda
        bundle = Path(args.projeto) / rmc["download"] if rmc.get("download") else None
        novo_proj = tmp / "novo"
        (novo_proj / prj.PASTA).mkdir(parents=True)
        shutil.copy(cfg["_arquivo"], novo_proj / prj.PASTA)
        if bundle and bundle.is_dir():
            shutil.copytree(bundle, novo_proj / rmc["download"])
            shutil.copytree(bundle, tmp / "bundle-antigo")
        rm_args = {"project_area_identifier": rmc.get("id"), "component": rmc.get("componente"),
                   "configuration": rmc.get("configuracao")}
        chamadas = [["ler_wi", "ccm.ccm_get_workitem", {"workitem_id": args.wi, "fields": campos, "link_types": links}],
                    ["horas_wi", "ccm.ccm_list_timesheet", {"workitem_id": args.wi}]]
        if it_id:
            chamadas.append(["listar", "ccm.ccm_list_workitems", {"project_area_identifier": ccm["id"], "iteration": it_id}])
        if rmc:
            chamadas += [["ler_req", "rm.rm_get_requirement", {**rm_args, "requirement_id": args.req}]]
        chamadas.append(["setup", "ccm.ccm_list_workitem_types", {"project_area_identifier": ccm["id"]}])
        if rmc.get("download"):
            pastas = {a["pasta"]: a["pasta-id"] for a in rmc["artefatos"].values()}
            chamadas.append(["sync", "rm.rm_sync_plan", {**rm_args, "folders": pastas, "dest": str(tmp / "bundle-antigo")}])
        antigo = rodar_antigo(velho, chamadas)

        linhas = []
        texto, _ = chamar_novo("elm_ler_wi", projeto=args.projeto, pa=args.pa, id=int(args.wi))
        linhas.append(comparar("ler WI", antigo["ler_wi"], texto, lambda a, n: (
            [cabecalho(a).get(k) for k in ("id", "title", "state")],
            [cabecalho(n).get(k) for k in ("id", "titulo", "estado")])))
        texto, _ = chamar_novo("elm_horas", projeto=args.projeto, pa=args.pa, id=int(args.wi))
        linhas.append(comparar("horas do WI", antigo["horas_wi"], texto,
                               lambda a, n: (round(a["total_hours"], 2), dados(n)["total_h"])))
        if it_id:
            texto, _ = chamar_novo("elm_listar_wi", projeto=args.projeto, pa=args.pa, iteracao=args.iteracao,
                                   limite=1000)
            linhas.append(comparar("listar da iteração", antigo["listar"], texto, lambda a, n: (
                sorted(int(w["id"]) for w in a), sorted(w["id"] for w in dados(n)["itens"]))))
        if rmc:
            texto, _ = chamar_novo("elm_ler_req", projeto=args.projeto, pa=args.pa, id=int(args.req))
            linhas.append(comparar("ler requisito", antigo["ler_req"], texto, lambda a, n: (
                [cabecalho(a).get(k) for k in ("id", "title")], [cabecalho(n).get(k) for k in ("id", "titulo")])))
            titulo = cabecalho(texto).get("titulo") if texto.startswith("---") else None
            if titulo:  # busca pelo título do requisito lido: os dois precisam achá-lo
                busca = rodar_antigo(velho, [["buscar", "rm.rm_search_requirements", {**rm_args, "text": titulo}]])
                texto, _ = chamar_novo("elm_buscar_req", projeto=args.projeto, pa=args.pa, texto=titulo, limite=1000)
                linhas.append(comparar("buscar requisito", busca["buscar"], texto, lambda a, n: (
                    int(args.req) in [int(r["id"]) for r in a], int(args.req) in [r["id"] for r in dados(n)["itens"]])))
        texto, _ = chamar_novo("elm_setup_descobrir", ccm=ccm["nome"])
        linhas.append(comparar("setup descobrir", antigo["setup"], texto, lambda a, n: (
            sorted(t["identifier"] for t in a), sorted(t["id"] for t in dados(n)["ccm"].get("tipos", [])))))
        if rmc.get("download"):
            texto, _ = chamar_novo("elm_sync", projeto=str(novo_proj), pa=args.pa, direcao="plano")
            linhas.append(comparar("sync plano", antigo["sync"], texto, lambda a, n: (
                [a["a_baixar"], a["a_subir"], len(a["conflitos"])],
                [len(dados(n)["baixar"]), len(dados(n)["enviar"]), len(dados(n)["conflitos"])])))

        if args.escrita:
            linhas += escritas(args, velho)

    largura = max(len(linha[0]) for linha in linhas)
    print(f"{'operação'.ljust(largura)}  resultado         antigo  novo  detalhe")
    for nome, resultado, detalhe, a, n in linhas:
        print(f"{nome.ljust(largura)}  {resultado.ljust(16)} {a:>7} {n:>5}  {detalhe}")


def escritas(args, velho: Path) -> list:
    """Cada escrita roda nos dois (antigo e novo) no --wi-teste, depois de um "sim"."""
    linhas = []
    for nome, antiga, nova in (
        ("comentar", ["comentar", "workitems.add_comment_to_workitem",
                      {"workitem_id": args.wi_teste, "comment": "paridade mcp-elm (1.0.43)"}],
         ("elm_comentar", {"texto_md": "paridade mcp-elm (2.0)"})),
        ("atualizar descrição", ["descricao", "ccm.ccm_update_workitem",
                                 {"workitem_id": args.wi_teste, "description": "paridade mcp-elm (1.0.43)"}],
         ("elm_atualizar_wi", {"descricao_md": "paridade mcp-elm (2.0)"})),
    ):
        if input(f"Escrever '{nome}' no work item {args.wi_teste} (antigo e novo)? [sim/não] ").strip().lower() != "sim":
            continue
        a = rodar_antigo(velho, [antiga])[antiga[0]]
        tool, extra = nova
        alvo = {"ids": [int(args.wi_teste)]} if tool == "elm_atualizar_wi" else {"id": int(args.wi_teste)}
        texto, _ = chamar_novo(tool, projeto=args.projeto, pa=args.pa, **alvo, **extra)
        linhas.append(comparar(nome, a, texto, lambda x, n: (True, json.loads(n)["ok"])))
    return linhas


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
