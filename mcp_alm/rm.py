"""Tools do DOORS Next para as skills (alm-setup e alm-rm): saída com as chaves do alm.json."""
from __future__ import annotations

import glob
from concurrent.futures import ThreadPoolExecutor
import os
import posixpath
import re
from datetime import datetime, timezone
from html import unescape
from importlib import metadata
from typing import Literal

from . import bundle
from .ibm import common, requirements
from .infra import oslc
from .infra.document import (artifact_ref, document, link_targets, one_line, read_document, resource_url, slug,
                              to_markdown, to_xhtml, utc_datetime)
from .infra.http import get_session
from .server import tool

PROJECT_AREA = "/rm/process/project-areas/{pa}"
COMPONENT = "/rm/cm/component/{id}"
# ponytail: configuration é sempre id de stream (o que rm_get_configuration devolve); aceitar baseline se precisar
STREAM = "/rm/cm/stream/{id}"
FOLDER = "/rm/folders/{id}"
TYPE = "/rm/types/{id}"
SEARCH_SELECT = "dcterms:identifier,dcterms:title,dcterms:modified,oslc:instanceShape,nav:parent"
MODIFIED_SELECT = "dcterms:identifier,dcterms:title,dcterms:modified"
# passadas extras só se a listagem ordenada ainda não alcançar o oslc:totalCount (índice do servidor instável)
LIST_PASSES = 3
# paginação estável na listagem de pasta
BY_ID = "+dcterms:identifier"
# propriedades que já têm lugar próprio na saída (não entram em links)
CORE = {"dcterms:title", "dcterms:identifier", "dcterms:description", "jazz_rm:primaryText", "nav:parent",
        "oslc:instanceShape"}
# preenchidos pelo servidor: não são campos que a skill grava
SYSTEM = CORE | {"dcterms:created", "dcterms:modified", "dcterms:creator", "dcterms:contributor",
                 "oslc_config:component", "process:projectArea"}
VALUE_KINDS = {"xsd:string": "text", "rdf:XMLLiteral": "text", "xsd:integer": "integer", "xsd:int": "integer",
               "xsd:long": "integer", "xsd:double": "number", "xsd:float": "number", "xsd:decimal": "number",
               "xsd:dateTime": "date", "xsd:date": "date", "xsd:boolean": "boolean"}
# valueType de link para outro recurso (vazio: o DOORS Next omite em alguns tipos de link)
RESOURCE_TYPES = {"oslc:Resource", "oslc:AnyResource", "oslc:LocalResource", ""}
PERSON = "http://xmlns.com/foaf/0.1/Person"
# link que o DOORS Next deriva dos embeds do texto: já aparece em `embedded`
EMBEDDING = "http://www.ibm.com/xmlns/rdm/types/Embedding"


def _generator() -> str:
    """Ator OKF `generated.by`: 'process:alm-mcp/<versão>' pela metadata da distribuição 'mcp-alm'.
    Sem a distribuição instalada (rodando do fonte) cai para 'process:alm-mcp' em vez de levantar."""
    try:
        return f"process:alm-mcp/{metadata.version('mcp-alm')}"
    except metadata.PackageNotFoundError:
        return "process:alm-mcp"


def _area_url(project_area_identifier: str) -> str:
    return get_session().url(PROJECT_AREA.format(pa=project_area_identifier))


def _url(template: str, identifier: str) -> str:
    return get_session().url(template.format(id=identifier))


@tool
def rm_get_configuration(project_area_identifier: str) -> dict:
    """Componente e stream padrão da project area do DOORS Next: {component, configuration} (identifiers do
    primeiro componente e da primeira stream). alm.json: rm.component e rm.configuration."""
    components = requirements.get_project_components(project_area_identifier)
    if not components:
        raise LookupError(f"Project area {project_area_identifier} não tem componentes no DOORS Next.")
    component = components[0]["url"]
    return {"component": oslc.item_id(component), "configuration": oslc.item_id(requirements.default_stream(component))}


@tool
def rm_list_members(project_area_identifier: str) -> list[dict]:
    """Membros da project area do DOORS Next: [{identifier (login), name}]. alm.json: rm.members {identifier: name}."""
    names = {c["userId"]: c["name"] for c in common.contributors()}
    members = [{"identifier": login, "name": names.get(login)}
               for login in dict.fromkeys(common.area_members(_area_url(project_area_identifier)))]
    return sorted(members, key=lambda m: (m["name"] or m["identifier"]).lower())


@tool
def rm_list_folders(project_area_identifier: str, component: str, configuration: str) -> list[dict]:
    """Pastas do componente com o caminho como nome: [{name ('01-Requisitos'), identifier ('FR_...')}].
    alm.json: rm.folders {name: identifier}. O prefixo 'root/' é removido do nome."""
    folders = requirements.list_rm_component_folders(_url(COMPONENT, component), _url(STREAM, configuration))
    by_url = {f["url"]: f for f in folders}

    def path(folder: dict) -> str:
        parent = by_url.get(folder["parent"])
        return f"{path(parent)}/{folder['title']}" if parent else folder["title"]

    result = []
    for f in folders:
        name = path(f)
        # Remove o prefixo 'root/' ou 'root' se for a pasta raiz
        if name == "root":
            continue  # Não inclui a pasta raiz
        if name.startswith("root/"):
            name = name[5:]  # Remove 'root/'
        result.append({"name": name, "identifier": oslc.item_id(f["url"])})
    return result


@tool
def rm_list_requirement_types(project_area_identifier: str, component: str, configuration: str) -> list[dict]:
    """Tipos de requisito criáveis: [{name, identifier ('OT_...')}]. alm.json: rm.requirements-types
    {name: identifier}."""
    return [{"name": s["title"], "identifier": oslc.item_id(s["url"])}
            for s in requirements.get_rm_component_types(project_area_identifier, component,
                                                         _url(STREAM, configuration))]


# --- alm-rm

def _kind(prop: dict) -> str:
    if prop["allowed_values"]:
        return "enumeration"
    if PERSON in prop["range"]:
        return "member"
    if prop["value_type"] in VALUE_KINDS:
        return VALUE_KINDS[prop["value_type"]]
    return "link" if prop["value_type"] in RESOURCE_TYPES else "text"


def _link(resource: dict, predicate: str) -> dict | None:
    return next(iter(resource["links"].get(predicate, [])), None)


def _summary(resource: dict, configuration: str) -> dict:
    """Requisito enxuto: {id, title, type, folder, modified (ISO 8601 UTC), path, url}."""
    shape, folder = _link(resource, "oslc:instanceShape"), _link(resource, "nav:parent")
    return {"id": resource["id"], "title": resource["title"],
            "type": oslc.shape(shape["url"], configuration)["title"] if shape else None,
            "folder": (folder.get("title") or oslc.title(folder["url"], configuration)) if folder else None,
            "modified": utc_datetime(resource["properties"].get("dcterms:modified")),
            "path": _file_path(resource, configuration), "url": resource["url"]}


def _folder_path(url: str, configuration: str) -> str:
    """Pasta -> caminho como em rm_list_folders / rm.folders ('01-Req/Funcionais'), sem a pasta 'root'."""
    folder = oslc.cached(url, configuration)
    if folder["title"] == "root":  # a raiz pode ter nav:parent no servidor: o caminho começa abaixo dela
        return ""
    parent = _link(folder, "nav:parent")
    if parent is None:  # ponytail: pasta sem nav:parent no servidor cai só no próprio título
        return folder["title"] or ""
    return posixpath.join(_folder_path(parent["url"], configuration), folder["title"] or "")


def _file_path(resource: dict, configuration: str) -> str:
    """Arquivo do artefato no bundle baixado (alm-download): '<caminho da pasta>/<id>-<slug do título>.md'."""
    folder = _link(resource, "nav:parent")
    name = f"{resource['id']}-{slug(one_line(resource['title']) or '')}.md"
    return posixpath.join(_folder_path(folder["url"], configuration), name) if folder else name


def _artifact(url: str, configuration: str) -> dict | None:
    """Artefato pela URL; None se não puder ser lido (sem permissão, removido...)."""
    try:
        return oslc.get(url, configuration)
    except RuntimeError:  # AlmHttpError: um artefato ilegível não derruba a leitura do requisito
        return None


def _artifact_url(project_area_identifier: str, component: str, configuration: str, requirement_id: str) -> str:
    return requirements.get_requirement(project_area_identifier, component, requirement_id,
                                        configuration_url=configuration)["url"].split("?")[0]


def _xhtml(project_area_identifier: str, component: str, configuration: str, text: str,
           embedded: list[str] | None) -> str:
    """`text` em Markdown ([x](alvo) cita o artefato, ou embute se ele está em `embedded`; alvo = id, URL ou arquivo
    do bundle) ou XHTML."""
    if text.lstrip().startswith("<"):
        return text
    return to_xhtml(text, lambda ref: _artifact_url(project_area_identifier, component, configuration, ref)
                    if ref.isdigit() else get_session().url(ref), embedded or ())


@tool
def rm_search_requirements(
    project_area_identifier: str, component: str, configuration: str, text: str | None = None,
    folder: str | None = None, requirement_type: str | None = None,
) -> list[dict]:
    """Requisitos (até 1000): [{id, title, type, folder, modified, url}], modified em ISO 8601 UTC. Filtros
    combinam com 'e': texto no título/corpo, pasta (identifier de rm.folders) e tipo (identifier de
    rm.requirements-types). Exige ao menos um filtro."""
    if not (text or folder or requirement_type):
        raise ValueError("Informe ao menos um filtro: text, folder ou requirement_type.")
    configuration = _url(STREAM, configuration)
    clauses = [oslc.where_eq("nav:parent", _url(FOLDER, folder))] if folder else []
    if requirement_type:
        clauses.append(oslc.where_eq("oslc:instanceShape", _url(TYPE, requirement_type)))
    found = oslc.query(requirements.requirements_base(project_area_identifier, configuration),
                       where=" and ".join(clauses) or None, search_terms=text, select=SEARCH_SELECT,
                       limit=oslc.MAX_LIMIT, configuration=configuration)
    return [_summary(r, configuration) for r in found]


@tool
def rm_list_modified(project_area_identifier: str, component: str, configuration: str,
                     requirement_ids: list[str]) -> list[dict]:
    """Última modificação de um ou vários requisitos pelo id numérico, sem ler o conteúdo: [{id, title, modified}],
    modified em ISO 8601 UTC (mesmo formato do generated.at do OKF). Ids não encontrados não voltam."""
    configuration = _url(STREAM, configuration)
    base = requirements.requirements_base(project_area_identifier, configuration)
    ids = list(dict.fromkeys(str(i) for i in requirement_ids))
    found = []
    for start in range(0, len(ids), 100):  # ponytail: bloco fixo de 100 ids por limite de URL
        found += oslc.query(base, where=oslc.where_in("dcterms:identifier", ids[start:start + 100]),
                            select=MODIFIED_SELECT, limit=oslc.MAX_LIMIT, configuration=configuration)
    return [{"id": r["id"], "title": one_line(r["title"]),
             "modified": utc_datetime(r["properties"].get("dcterms:modified"))} for r in found]


def _folder_query(project_area_identifier: str, configuration: str, folder: str) -> tuple[str, str]:
    """(query base, oslc.where) dos requisitos diretamente na pasta (nav:parent; subpastas ficam de fora)."""
    return (requirements.requirements_base(project_area_identifier, configuration),
            oslc.where_eq("nav:parent", _url(FOLDER, folder)))


@tool
def rm_count_folder(project_area_identifier: str, component: str, configuration: str, folder: str) -> dict:
    """Quantos requisitos estão diretamente na pasta (identifier de rm.folders; subpastas não entram, chame uma
    vez por pasta de rm_list_folders): {folder, count}. O número vem do servidor (oslc:totalCount), sem teto.
    Serve para conferir download/sync: count != len(rm_list_folder) indica listagem inconsistente no servidor."""
    configuration = _url(STREAM, configuration)
    base, where = _folder_query(project_area_identifier, configuration, folder)
    return {"folder": folder, "count": oslc.count(base, where=where, order_by=BY_ID, configuration=configuration)}


@tool
def rm_list_folder(project_area_identifier: str, component: str, configuration: str, folder: str) -> list[dict]:
    """Todos os requisitos diretamente na pasta (identifier de rm.folders; subpastas não entram), sem teto:
    [{id, title, modified}] ordenado por id, modified em ISO 8601 UTC (como rm_list_modified). Inventário para
    download/sync: compare com rm_count_folder e com o generated.at dos arquivos baixados.
    Pagina ordenado por dcterms:identifier (sem ordem, itens pulam de página); se ainda faltar, repete a listagem
    unindo os ids até alcançar o oslc:totalCount (máx. LIST_PASSES vezes)."""
    configuration = _url(STREAM, configuration)
    base, where = _folder_query(project_area_identifier, configuration, folder)
    merged: dict[str, dict] = {}
    for _ in range(LIST_PASSES):
        for r in oslc.query(base, where=where, select=MODIFIED_SELECT, order_by=BY_ID, limit=None,
                            configuration=configuration):
            merged[r["id"]] = r
        if len(merged) >= oslc.count(base, where=where, order_by=BY_ID, configuration=configuration):
            break
    return sorted(({"id": r["id"], "title": one_line(r["title"]),
                    "modified": utc_datetime(r["properties"].get("dcterms:modified"))} for r in merged.values()),
                  key=lambda r: int(r["id"]))


@tool
def rm_get_requirement(project_area_identifier: str, component: str, configuration: str, requirement_id: str,
                       links: Literal["alm", "bundle"] = "alm") -> str:
    """Requisito pelo id numérico em Markdown + YAML, conforme Google OKF v0.2. Cabeçalho, nesta ordem:
    campos OKF (type, title, description?, resource, tags) + provenance (sources) + trust (generated) + extensões RM
    (id, created, links, embedded).
    - type, title: tipo e título do requisito; description: resumo (dcterms:description), só se preenchido.
    - resource: URI canônico do artefato; tags: [pasta].
    - sources (OKF §5.1): [{id: doors-next, resource, author, last_modified, last_modified_by}]: author =
      'human:<login de quem criou>', last_modified = última modificação no DOORS Next, last_modified_by =
      'human:<login de quem modificou por último>'.
    - generated {by, at} (OKF §5.2): by = 'process:alm-mcp/<versão>'; at = quando este documento foi gerado.
      Timestamps em ISO 8601 UTC ('...Z'): last_modified > generated.at => o documento está desatualizado.
    - verified/status/stale_after: só com sinal real do artefato (o DOORS Next não os define); nunca inventados.
    - id; created (UTC); links {nome: ['id: título', ...]} (só leitura: relacionamentos OSLC, nomes do DOORS Next);
      embedded ['id: título', ...] = artefatos embutidos no texto (só com links='alm').
    Corpo: o texto em Markdown; todo artefato citado ou embutido é [id título](alvo). Artefato ilegível fica
    [texto](URL do ALM).
    links='alm': alvo = URL do artefato no ALM; é embed se está em `embedded` (mande-o em rm_update_requirement).
    links='bundle' (bundle da alm-sync): artefato do bundle = embed, alvo = caminho relativo do arquivo
    ('../03-Regras/2001-rn-validar-cpf.md', mesmo `path` de rm_search_requirements); o resto (outra PA, fora das
    pastas) = link, alvo = URL do ALM. Sem `embedded` no cabeçalho: a regra já diz o que é embed."""
    return _requirement(project_area_identifier, component, configuration, requirement_id, links)[0]


def _requirement(project_area_identifier: str, component: str, configuration: str, requirement_id: str,
                 links: str, bundle_ids: set[str] | None = None) -> tuple[str, dict]:
    """(documento de rm_get_requirement, {path, title, last_modified, generated_at, modified}) para gravar no bundle.
    `bundle_ids`: ids do inventário do bundle (None = todo artefato legível); no bundle, artefato dele vira o
    arquivo (embed) e o resto, a URL (link). modified = algum link foi normalizado (link da UI web, embed fora do
    bundle ou hyperlink para artefato do bundle): o arquivo difere do texto no ALM e precisa subir."""
    configuration = _url(STREAM, configuration)
    resource = requirements.get_requirement(project_area_identifier, component, requirement_id,
                                            configuration_url=configuration)
    props, res_links = resource["properties"], resource["links"]
    shape = _link(resource, "oslc:instanceShape")
    # um GET por artefato, mesmo se ligado e embutido; no bundle, vale para todo o download (os mesmos
    # artefatos são citados por muitos requisitos)
    artifacts: dict[str, dict | None] = _BUNDLE_ARTIFACTS if links == "bundle" else {}
    own_dir = posixpath.dirname(_file_path(resource, configuration)) if links == "bundle" else ""
    normalized = False

    def artifact(url: str) -> dict | None:
        url = get_session().url(url.split("?")[0])
        if url not in artifacts:
            artifacts[url] = _artifact(url, configuration)
        return artifacts[url]

    html = oslc.markup(resource["url"], "jazz_rm:primaryText", configuration) or ""
    embeds = [unescape(m.group(1)) for tag in re.findall(r"<a\s[^>]*>", html) if 'class="embedded"' in tag
              for m in [re.search(r'href="([^"]+)"', tag)] if m]

    def ref(href: str) -> tuple[str, str] | None:
        """Embed/hyperlink no corpo -> ('id título', alvo); None se não for artefato legível do RM."""
        nonlocal normalized
        url = resource_url(href)
        found = artifact(url) if "/rm/resources/" in url else None
        if found is None:
            return None
        normalized |= url != href
        in_bundle = links == "bundle" and (bundle_ids is None or found["id"] in bundle_ids)
        if links == "bundle":
            normalized |= in_bundle != (href in embeds)  # artefato do bundle = embed; o resto = link
        target = (posixpath.relpath(_file_path(found, configuration), own_dir or ".") if in_bundle
                  else found["url"].split("?")[0])
        return f"{found['id']} {one_line(found['title'])}", target

    def label(link: dict) -> str:
        url = link["url"]
        if "/rm/resources/" in url:
            found = artifact(url)
            return f"{found['id']}: {one_line(found['title'])}" if found else url.split("?")[0]
        if "/jts/users/" in url:
            return url.rsplit("/", 1)[-1]
        return one_line(link.get("title")) or oslc.title(url, configuration) or url

    related = {}
    for prop in oslc.shape(shape["url"], configuration)["properties"] if shape else []:
        key = prop["predicate"]
        if prop["title"] and key not in SYSTEM and key != EMBEDDING and key in res_links and _kind(prop) == "link":
            related[prop["title"]] = [label(link) for link in res_links[key]]
    summary = _summary(resource, configuration)
    body = to_markdown(html, ref)
    # no bundle a regra (artefato do bundle = embed) dispensa a lista; ilegível virou link e não entra
    embedded = [] if links == "bundle" else list(dict.fromkeys(label({"url": u}) for u in embeds if ref(u)))
    login = lambda predicate: next((link["url"].rsplit("/", 1)[-1] for link in res_links.get(predicate, [])), None)
    actor = lambda predicate: f"human:{user}" if (user := login(predicate)) else None
    url = resource["url"].split("?")[0]  # URI canônico do artefato
    # OKF §5.1/§5.2: a última modificação no ALM é da fonte (sources.last_modified); generated.at é quando o
    # documento foi escrito, para comparar e saber se a cópia baixada está desatualizada
    source = {"id": "doors-next", "resource": url, "author": actor("dcterms:creator"),
              "last_modified": utc_datetime(props.get("dcterms:modified")),
              "last_modified_by": actor("dcterms:contributor")}
    generated = {"by": _generator(), "at": utc_datetime(datetime.now(timezone.utc))}
    # verified/status/stale_after: OMITIDOS por padrão — requisitos do DOORS Next não têm sinal de
    # aprovação/revisão, estado de workflow nem atributo de validade/expiração definidos pelo servidor (ver
    # .agents/tasks/okf-rm-output/plan.md). Emitir só se um sinal real do servidor for encontrado; não inventar.
    head = {
        # OKF
        "type": summary["type"], "title": one_line(resource["title"]),
        "description": one_line(props.get("dcterms:description")) or None,
        "resource": url, "tags": [summary["folder"]] if summary["folder"] else None,
        "sources": [{k: v for k, v in source.items() if v}], "generated": generated,
        # extensões RM
        "id": int(resource["id"]), "created": utc_datetime(props.get("dcterms:created")),
        "links": related or None, "embedded": embedded or None}
    return document(head, body), {"path": _file_path(resource, configuration), "title": head["title"],
                                  "last_modified": source["last_modified"], "generated_at": generated["at"],
                                  "modified": normalized}


# artefatos citados já lidos no download do bundle; rm_sync_plan limpa (novo inventário = dados novos)
_BUNDLE_ARTIFACTS: dict[str, dict | None] = {}
# downloads em paralelo: o tempo é latência do servidor (~0,2 s por GET), não CPU
# ponytail: número fixo; ajustar se o servidor reclamar de carga
WORKERS = 8


def _root(dest: str) -> str:
    """Raiz do bundle: `dest` tem de ser absoluto (o cwd do MCP não é o repositório)."""
    if not os.path.isabs(dest):
        raise ValueError(f"dest precisa ser caminho absoluto: {dest}")
    return os.path.realpath(dest)


def _write_requirement(pa: str, component: str, configuration: str, rid: str, root: str,
                       bundle_ids: set[str] | None) -> dict:
    """Grava o requisito em '<root>/<pasta>/<id>-<slug>.md' e apaga outros '<id>-*.md' (título/pasta mudou).
    Devolve o meta de _requirement mais o `hash` do arquivo gravado."""
    doc, meta = _requirement(pa, component, configuration, rid, "bundle", bundle_ids)
    target = os.path.realpath(os.path.join(root, meta["path"]))
    if not target.startswith(root + os.sep):
        raise ValueError(f"caminho fora de dest: {meta['path']}")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(doc)
    for old in glob.glob(os.path.join(glob.escape(root), "**", f"{rid}-*.md"), recursive=True):
        if os.path.realpath(old) != target:
            os.remove(old)
    return {**meta, "hash": bundle.file_hash(target)}


def _downloaded(row: dict, meta: dict) -> None:
    """Linha do sync.md depois de gravar o arquivo: data do ALM e hash deste momento; 'normalizado' se o md ficou
    diferente do ALM (link normalizado) e precisa subir."""
    row.update(title=meta["title"], folder=row["folder"] or posixpath.dirname(meta["path"]), path=meta["path"],
               alm=meta["last_modified"] or "", hash=meta["hash"],
               status="normalizado" if meta["modified"] else "sincronizado")


def _message(exc: Exception) -> str:
    return " ".join(str(exc).replace("|", "/").split())[:200]


def _in_order(rows: dict[str, dict], statuses: tuple[str, ...]) -> list[str]:
    return [r["id"] for r in sorted(rows.values(), key=lambda r: (r["folder"], int(r["id"])))
            if r["status"].startswith(statuses)]


def _remove(root: str, path: str | None) -> None:
    if path and os.path.realpath(os.path.join(root, path)).startswith(root + os.sep):
        try:
            os.remove(os.path.join(root, path))
        except FileNotFoundError:
            pass


@tool
def rm_sync_plan(project_area_identifier: str, component: str, configuration: str, folders: dict[str, str],
                 dest: str) -> dict:
    """Inventário do bundle da alm-sync sem passar a lista pela conversa. `folders`: rm.folders inteiro
    {nome: identifier}; `dest`: caminho ABSOLUTO da raiz do bundle (rm.download.path). Lista cada pasta
    (rm_count_folder + rm_list_folder), compara com o sync.md de dest e o regrava. Estados:
    'novo' (não baixado), 'sincronizado' (md == ALM), 'desatualizado' (o ALM mudou desde o último download:
    modified != 'Última atualização ALM'), 'atualizado' (o md mudou: sha256 != Hash; sobe com
    rm_upload_requirements), 'normalizado' (md NÃO editado, hash igual: o ALM foge da regra embed/link do bundle,
    diferença que não aparece no md; não é falso positivo, sobe com rm_upload_requirements), 'conflito' (md e ALM
    mudaram), 'erro: ...'. Mudou de pasta = removido da antiga e
    novo na nova (o arquivo é apagado; se ele tem edição local, vira 'conflito' e fica). Artefato do sync.md que não
    está em nenhuma pasta = removido: o arquivo é APAGADO e a linha sai (não apaga nada se alguma pasta vier com
    count != listados: índice instável, ids em nao_confirmados).
    Fluxo da alm-sync: 1) rm_sync_plan; 2) rm_download_requirements até restantes = 0; 3) para cada item de
    `conflitos`, PERGUNTE AO USUÁRIO qual versão fica: ALM -> rm_download_requirements(requirement_ids=[id]);
    md -> rm_upload_requirements(requirement_ids=[id]); 4) rm_upload_requirements até restantes = 0.
    Retorna só o resumo: {pastas: {nome: {total, listados, <estado>: n}}, removidos: [{id, title, folder}],
    inconsistentes: [nomes], nao_confirmados: [ids], a_baixar, a_subir, conflitos: [{id, title, folder, path}]}."""
    root = _root(dest)
    _BUNDLE_ARTIFACTS.clear()
    rows = bundle.read_sync(root)
    listed: dict[str, dict] = {}
    summary, inconsistent = {}, []
    for name, identifier in folders.items():
        total = rm_count_folder(project_area_identifier, component, configuration, identifier)["count"]
        items = rm_list_folder(project_area_identifier, component, configuration, identifier)
        if total != len(items):
            inconsistent.append(name)
        for item in items:
            listed[item["id"]] = {**item, "folder": name}
        summary[name] = {"total": total, "listados": len(items)}
    for rid, item in listed.items():
        row = rows.get(rid)
        if row is None or not row["hash"]:
            rows[rid] = {"id": rid, "title": item["title"], "folder": item["folder"], "path": None, "alm": "",
                         "hash": "", "status": "novo"}
            continue
        local = (row["status"] in ("atualizado", "conflito")
                 or bool(row["path"]) and bundle.file_hash(os.path.join(root, row["path"])) != row["hash"])
        if row["folder"] != item["folder"] and not local:
            _remove(root, row["path"])
            row.update(path=None, alm="", hash="", status="novo")
        elif row["folder"] != item["folder"]:
            row["status"] = "conflito"
        else:
            alm = (item["modified"] or "") != row["alm"] or row["status"].startswith(("erro", "desatualizado"))
            row["status"] = ("conflito" if local and alm else "atualizado" if local
                             else "desatualizado" if alm else "normalizado" if row["status"] == "normalizado"
                             else "sincronizado")
        row.update(title=item["title"], folder=item["folder"])
    gone = [r for rid, r in rows.items() if rid not in listed]
    removed = [] if inconsistent else gone
    for r in removed:
        _remove(root, r["path"])
        del rows[r["id"]]
    bundle.write_sync(root, rows, _generator())
    if removed or not os.path.exists(os.path.join(root, bundle.INDEX)):
        bundle.write_index(root, rows)
    for r in rows.values():
        if r["folder"] in summary:
            counts = summary[r["folder"]]
            key = r["status"].split(" ")[0].rstrip(":")
            counts[key] = counts.get(key, 0) + 1
    return {"pastas": summary, "removidos": [{k: r[k] for k in ("id", "title", "folder")} for r in removed],
            "inconsistentes": inconsistent, "nao_confirmados": [] if removed else [r["id"] for r in gone],
            "a_baixar": len(_in_order(rows, bundle.QUEUE)), "a_subir": len(_in_order(rows, bundle.UPLOAD)),
            "conflitos": [{k: rows[rid][k] for k in ("id", "title", "folder", "path")}
                          for rid in _in_order(rows, ("conflito",))]}


@tool
def rm_download_requirements(project_area_identifier: str, component: str, configuration: str, dest: str,
                             requirement_ids: list[str] | None = None, limit: int = 50) -> dict:
    """Baixa para o bundle da alm-sync sem passar o conteúdo pela conversa: o mesmo documento de
    rm_get_requirement(links='bundle') em '<dest>/<caminho da pasta>/<id>-<slug>.md' (apaga '<id>-*.md' antigo
    se o título ou a pasta mudou). `dest`: caminho ABSOLUTO da raiz do bundle. Sem `requirement_ids`, pega as
    próximas `limit` linhas novo/desatualizado/erro do sync.md (a fila de rm_sync_plan); com ids, baixa esses,
    inclusive 'conflito' (o usuário escolheu a versão do ALM: a edição do md é sobrescrita).
    Linha baixada = 'sincronizado', ou 'normalizado' se o md ficou diferente do ALM (link da UI web, embed de
    artefato fora do bundle ou hyperlink para artefato do bundle normalizados): sobe com rm_upload_requirements.
    No md embed e link para o bundle são iguais, então o diff do md não mostra essa diferença.
    Atualiza as linhas no sync.md a cada chamada (retomada) e regera o index.md quando a fila zera. Erro num id
    não para o lote. Retorna {baixados, erros: [{id, error}], restantes}: chame de novo até restantes = 0."""
    root = _root(dest)
    rows = bundle.read_sync(root)
    ids = requirement_ids or _in_order(rows, bundle.QUEUE)[:max(1, limit)]
    bundle_ids = set(rows) or None  # sem inventário (sync.md vazio): não filtra pelo bundle

    def fetch(rid: str):
        try:
            return _write_requirement(project_area_identifier, component, configuration, rid, root, bundle_ids)
        except Exception as exc:  # um id com erro não derruba o lote
            return exc

    done, errors = 0, []
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(fetch, ids))
    for rid, meta in zip(ids, results):
        row = rows.get(rid) or {"id": rid, "title": rid, "folder": "", "path": None, "alm": "", "hash": ""}
        if not isinstance(meta, Exception):
            _downloaded(row, meta)
            done += 1
        else:
            row["status"] = f"erro: {_message(meta)}"
            errors.append({"id": rid, "error": _message(meta)})
        rows[rid] = row
    bundle.write_sync(root, rows, _generator())
    remaining = len(_in_order(rows, bundle.QUEUE))
    if remaining == 0:
        bundle.write_index(root, rows)
    return {"baixados": done, "erros": errors, "restantes": remaining}


def _bundle_embeds(configuration: str, body: str, bundle_ids: set[str]) -> list[str]:
    """Ids dos artefatos do bundle citados no corpo (alvo id, arquivo ou URL): no upload viram embed; o resto, link."""
    ids = []
    for target in link_targets(body):
        ref = artifact_ref(target)
        if ref and not ref.isdigit():
            found = _artifact(get_session().url(ref), configuration)
            ref = found and found["id"]
        if ref in bundle_ids:
            ids.append(ref)
    return list(dict.fromkeys(ids))


@tool
def rm_upload_requirements(project_area_identifier: str, component: str, configuration: str, dest: str,
                           requirement_ids: list[str] | None = None, limit: int = 50) -> dict:
    """Sobe para o ALM os md editados do bundle da alm-sync (rode depois de rm_download_requirements zerar).
    Sem `requirement_ids`, pega as próximas `limit` linhas 'atualizado'/'normalizado' do sync.md e só sobe se o ALM não mudou
    desde o download (senão a linha vira 'conflito' e nada sobe); com ids, sobe esses sem checar (o usuário
    escolheu a versão do md num conflito). Envia title (cabeçalho) e o corpo: artefato do bundle vira embed e o
    resto, link (`links` do cabeçalho não sobe). Formatos no corpo: embed [<id>](<id>); link para artefato de outra
    PA [<id>](<URL do ALM>); link externo [<texto descritivo>](<url>). Depois rebaixa o requisito (linha
    'sincronizado'). Erro num id não para o lote e mantém a linha como está.
    Retorna {enviados, erros: [{id, error}], conflitos: [ids], restantes}: chame de novo até restantes = 0."""
    pa, root = project_area_identifier, _root(dest)
    rows = bundle.read_sync(root)
    ids = requirement_ids or _in_order(rows, bundle.UPLOAD)[:max(1, limit)]
    stream = _url(STREAM, configuration)

    def push(rid: str):
        try:
            row = rows[rid]
            if not requirement_ids:
                current = requirements.get_requirement(pa, component, rid, configuration_url=stream)
                if utc_datetime(current["properties"].get("dcterms:modified")) != row["alm"]:
                    return "conflito"
            with open(os.path.join(root, row["path"]), encoding="utf-8") as f:
                head, body = read_document(f.read())
            rm_update_requirement(pa, component, configuration, rid, title=head.get("title"), text=body,
                                  embedded=_bundle_embeds(stream, body, set(rows)))
            return _write_requirement(pa, component, configuration, rid, root, set(rows))
        except Exception as exc:  # um id com erro não derruba o lote
            return exc

    done, errors, conflicts = 0, [], []
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(push, ids))
    for rid, result in zip(ids, results):
        if result == "conflito":
            rows[rid]["status"] = "conflito"
            conflicts.append(rid)
        elif isinstance(result, Exception):  # a linha fica como está: a edição do md não se perde
            errors.append({"id": rid, "error": _message(result)})
        else:
            _downloaded(rows[rid], result)
            done += 1
    bundle.write_sync(root, rows, _generator())
    return {"enviados": done, "erros": errors, "conflitos": conflicts,
            "restantes": len(_in_order(rows, bundle.UPLOAD))}


@tool
def rm_create_requirement(
    project_area_identifier: str, component: str, configuration: str, requirement_type: str, folder: str,
    title: str, text: str, embedded: list[str] | None = None,
) -> dict:
    """Cria um requisito na pasta e no tipo do alm.json (identifiers). `text`: Markdown ([x](alvo) cita o artefato;
    alvo = id, URL ou arquivo do bundle '<id>-....md') ou XHTML. `embedded`: artefatos ('id' ou 'id: título') cujos
    links no texto viram embed; os demais viram hyperlink. Retorna {id, title, url}."""
    pa = project_area_identifier
    configuration, requirement_type = _url(STREAM, configuration), _url(TYPE, requirement_type)
    created = requirements.create_requirement(_area_url(pa), _url(COMPONENT, component), requirement_type, title, "",
                                              _xhtml(pa, component, configuration, text, embedded),
                                              configuration_url=configuration, folder_url=_url(FOLDER, folder))
    return {"id": created["id"], "title": created["title"], "url": created["url"]}


@tool
def rm_update_requirement(
    project_area_identifier: str, component: str, configuration: str, requirement_id: str,
    title: str | None = None, text: str | None = None, embedded: list[str] | None = None,
) -> dict:
    """Atualiza título e/ou texto (Markdown como em rm_create_requirement ou XHTML; substitui o texto inteiro) do
    requisito pelo id numérico. Com `text`, mande o `embedded` do cabeçalho de rm_get_requirement para manter os
    embeds; sem ele todo link vira hyperlink. Retorna {id, title, url}."""
    if not (title or text is not None):
        raise ValueError("Informe title e/ou text.")
    pa, configuration = project_area_identifier, _url(STREAM, configuration)
    resource = requirements.get_requirement(pa, component, requirement_id, configuration_url=configuration)
    changes: dict = {}
    if title:
        changes["dcterms:title"] = title
    if text is not None:
        changes["jazz_rm:primaryText"] = requirements.xhtml(_xhtml(pa, component, configuration, text, embedded))
    updated = oslc.update(resource["url"], changes, configuration=configuration)
    return {"id": updated["id"], "title": updated["title"], "url": updated["url"]}
