---
title: "mcp-alm — Manual de Utilização e Guia de Construção de Skills"
subtitle: "Servidor MCP para IBM ELM (DOORS Next, EWM, ETM) via OSLC — versão 1.0.11"
author: "Daniel Xavier Araújo"
date: "30/09/2026"
lang: pt-BR
---

# 1. Visão geral

O **mcp-alm** é um servidor MCP (Model Context Protocol) em Python que dá a um assistente de IA (Claude Code, Kiro,
Claude Desktop ou qualquer cliente MCP) acesso ao **IBM Engineering Lifecycle Management (ELM)** por OSLC:

| App ELM | Produto | Context root | O que o MCP faz |
|---|---|---|---|
| **RM** | DOORS Next | `/rm` | ler, buscar, criar e atualizar requisitos; pastas, tipos, componentes, streams |
| **CCM** | EWM (antigo RTC) | `/ccm` | ler, buscar, criar e atualizar work items; estados, comentários, iterações, planos, times |
| **QM** | ETM (antigo RQM) | `/qm` | ler e buscar artefatos de teste (somente leitura) |
| **GC** | Global Configuration | `/gc` | ler e buscar configurações globais |
| **JTS** | Jazz Team Server | `/jts` | usuários |

O servidor tem **duas camadas de tools**:

1. **Tools IBM** (`mcp_alm/ibm/`): mesmos nomes e parâmetros do *IBM Engineering AI Hub MCP 1.3.0*. Trabalham com
   UUIDs e URLs e devolvem recursos OSLC genéricos. Servem para compatibilidade e para casos que as tools de skill
   não cobrem.
2. **Tools de skill** (`mcp_alm/ccm.py`, `mcp_alm/rm.py`, prefixos `ccm_` e `rm_`): tools enxutas, pensadas para
   serem chamadas por uma **skill**. Recebem os *identifiers* guardados no arquivo de projeto `alm/pa_*.json` e
   devolvem listas `[{name, identifier, ...}]` que a skill grava nesse arquivo.

A ideia central do projeto: **o MCP fornece capacidades; as skills fornecem conhecimento**. A skill ensina a IA quais
tools usar, com quais parâmetros, em que ordem, e onde estão os identifiers já descobertos, evitando chamadas
desnecessárias ao servidor.

```
 Usuário ──► IA (Claude Code / Kiro) ──► Skill (SKILL.md + alm/pa_*.json)
                                           │ decide quais tools chamar
                                           ▼
                                   MCP "alm" (mcp-alm, stdio)
                                           │ HTTP + OSLC (RDF/XML), Reportable REST, API de processo
                                           ▼
                            Servidor IBM ELM (/jts /ccm /rm /qm /gc)
```

# 2. Análise técnica do MCP

## 2.1 Estrutura do código

```
mcp_alm/
  server.py        instância MCPServer "alm" + decorador @tool (erros esperados -> ToolError)
  infra/
    config.py      lê alm.properties (server, user, password)
    auth.py        login Jazz por form (j_security_check) com fallback para Basic
    http.py        sessão HTTP única (thread-safe), re-login automático, header
                   Configuration-Context, leitura de XML e Reportable REST
    oslc.py        RDF (rdflib), qnames, descoberta (rootservices -> catálogo -> provider),
                   query OSLC com paginação, resource shapes, GET/PUT com If-Match
    document.py    leitura em Markdown + YAML: XHTML <-> Markdown (artefatos como [id título](alvo))
  ibm/             tools no padrão IBM Engineering AI Hub 1.3.0
    common.py      usuários, project areas, GC, links de rastreabilidade
    requirements.py DOORS Next
    workitems.py   EWM
    test.py        ETM
  ccm.py           tools de skill do EWM  (alm-setup, alm-ccm)
  rm.py            tools de skill do DOORS Next (alm-setup, alm-rm)
  qm.py            reservado para as tools da alm-qm (vazio; a alm-qm usa as tools IBM)
tests/             139 testes com um servidor Jazz falso (fixtures RDF/XML)
```

Dependências: `mcp[cli]>=2.2`, `markdown-it-py>=3`, `requests>=2.31`, `rdflib>=7.0`; Python ≥ 3.10.

## 2.2 Fluxo de uma chamada

1. O cliente MCP chama a tool (ex.: `ccm_list_workitems`).
2. A tool monta URLs relativas (`/ccm/oslc/...`) resolvidas contra `server` do `alm.properties`.
3. `AlmSession.request` envia com `OSLC-Core-Version: 2.0` e `Accept: application/rdf+xml`. Se o servidor pedir
   autenticação (header `X-com-ibm-team-repository-web-auth-msg`, 401 ou redirect para login), faz o login no
   context root daquela URL e repete a requisição.
4. A descoberta OSLC (`rootservices` → catálogo → service provider da project area → query capability/creation
   factory) é feita sob demanda e mantida em cache no processo.
5. O RDF é convertido em JSON: `{url, id, title, types, properties{}, links{}}` com chaves em qname
   (`dcterms:title`, `rtc_cm:state`...). Links vêm como `{url, title?}`.
6. Exceções esperadas (`RuntimeError`, `OSError`, `LookupError`, `ValueError`) viram `ToolError` **com a mensagem
   original**, para a IA (e a skill) poder reagir ao erro.

## 2.3 Pontos fortes

- **Compatibilidade com a IBM**: as tools IBM têm os mesmos nomes/parâmetros do AI Hub, então prompts e skills
  escritos para o produto oficial funcionam aqui.
- **Saída enxuta nas tools de skill**: `ccm_list_workitems` devolve só `{id, title, type, state, owner, iteration,
  url}`; poucos tokens por item.
- **Títulos resolvidos na mesma requisição**: `get_workitem`/`search_workitems` usam `oslc.properties`/`oslc.select`
  com `{dcterms:title}` e trazem o nome do estado, prioridade e iteração sem chamadas extras.
- **Mensagens de erro acionáveis**: ex. `Estado 'X' não existe no workflow. Estados: A, B, C.` ou
  `Valor 'Y' inválido para 'Prioridade'. Válidos: ...`. A skill pode instruir a IA a corrigir e repetir.
- **Validação antes da escrita**: `ccm_create_iteration_plan` valida `plan_type` localmente.
- **Transição de estado pelo nome**: `ccm_update_workitem(state="Pronto")` descobre a ação do workflow que leva ao
  estado e confirma que o estado mudou.
- **Testes offline**: servidor Jazz falso com fixtures reais; `uv run pytest` roda em menos de 1 s (139 testes).

## 2.4 Limitações e riscos identificados

| # | Ponto | Impacto | Recomendação na skill |
|---|---|---|---|
| 1 | O README cita as pastas `skills/` e `alm/`, mas elas **não estão no repositório** | as skills precisam ser (re)criadas | usar os modelos da seção 7 |
| 2 | `qm.py` está vazio | não há tools `qm_*`; a alm-qm usa as tools IBM (UUIDs e tipos IBM) | documentar na alm-qm os `artifact_type` válidos |
| 3 | Buscas limitadas a 1000 itens (`oslc.MAX_LIMIT`), sem aviso de truncamento | listas grandes podem vir incompletas | sempre filtrar por iteração/time/owner |
| 4 | `ccm_create_iteration` e `ccm_create_iteration_plan` usam serviços **internos** da UI web | podem quebrar numa atualização do EWM; exigem permissões de processo | tratar `Permission Denied` como falta de permissão, não como payload errado |
| 5 | Token do guard (`PLAN_GUARD_TOKEN`) fixo no código | mitigado: o servidor devolve o token atual e a tool repete uma vez | nenhuma |
| 6 | Fuso fixo de Brasília (UTC−3) nas datas de iteração | datas deslocadas em servidores de outro fuso | informar datas `AAAA-MM-DD` |
| 7 | Lista de usuários (`contributors`) carregada uma vez por processo | usuário novo só aparece após reiniciar o MCP | orientar reinício do cliente |
| 8 | **Removido.** `rm_create_requirement`/`rm_update_requirement` não gravam mais atributos nem links (o ALM não os usa) | — | — |
| 9 | `rm_search_requirements`: DOORS Next responde 400 com `text` + `folder`/`requirement_type` | busca combinada falha | buscar só pelo texto e filtrar o resultado por `type`/`folder` |
| 10 | `rm_*` aceita só id de **stream** em `configuration` | não lê baselines pelas tools de skill | usar `get_requirement` com `configuration_url` de baseline |
| 11 | Team Area do work item vem da categoria (Filed Against) | não dá para escolher o time diretamente via OSLC | escolher a categoria ligada ao time |
| 12 | `search_workitems`: só AND (OR com uma expressão), sem `termExpressions` | filtros complexos falham | preferir `ccm_list_workitems` |
| 13 | Senha em texto puro em `alm.properties` | risco de vazamento | `chmod 600` no arquivo; nunca versionar |
| 14 | **Corrigido.** O EWM codifica acentos nos nomes de atributo do shape (`classifica%C3%A7%C3%A3o`), mas não nos dados do work item (`classificação`) | campos personalizados com acento não eram lidos nem gravados pelo nome | `oslc.qname` decodifica os nomes; testado lendo e gravando um campo com acento |
| 15 | Links entre requisitos ("Vincular A", "Elaborado por"...) são **só de leitura** (`links` do cabeçalho) | não há tool para gravá-los | — |
| 16 | **Removido** junto com `attributes` | — | — |
| 17 | **Corrigido.** `rm_get_requirement` devolvia os links de requisito como URL e os de work item como título | inconsistência para a skill | links saem como `id: título`; na gravação, URL ou id |
| 18 | **Corrigido.** `rm_get_requirement` removia todo o HTML do texto: perdia títulos, listas e **artefatos embutidos**, e juntava palavras quebradas pelo Word ("difer ente") | a IA não via o artefato embutido; regravar o texto apagava embeds e formatação | leitura e gravação em Markdown com `![[id: título]]`; estilos visuais do Word (fonte, cor) não voltam na regravação |
| 19 | **Testado e confirmado na UI.** Embeds do DOORS Next são relações no texto, não atributos. `![[id]]` é gravado como o editor do DOORS Next grava | — | — |
| 20 | **Limitação aceita.** Links criados na visão de módulo e links que chegam de outros artefatos não aparecem na leitura do artefato | a IA pode concluir que não há link quando ele existe na UI | decidido não ler links de módulo; conferir na UI quando necessário |
| 21 | **Limitação aceita.** `rm_*` lê sempre o stream padrão do alm.json | textos e links de outro stream ou change set não aparecem | decidido não ler outras configurações; usar as tools IBM (`get_requirement` com `configuration_url`) se precisar |
| 22 | **Corrigido.** `get_workitem(fetch_all=true)` devolve mais de 40 chaves técnicas e nomes do servidor em inglês/português misturados | muitos tokens, leitura difícil | `ccm_get_workitem` em Markdown + YAML, só com os campos e links do alm.json |
| 23 | A leitura em Markdown + YAML faz um GET a mais (o XHTML do texto) e um GET por artefato embutido ou ligado | latência em textos com muitos embeds | aceitável hoje; guardar o XHTML em `oslc.resource` se pesar |
| 24 | Na leitura do WI, pessoas vêm pelo **nome** e a estimativa como `4h`; na gravação, pessoa vai pelo **login** e estimativa em **ms** | a skill não pode copiar o valor lido para a gravação | login pelo `members` do alm.json; horas → ms |
| 25 | A descrição do WI no EWM só tem texto, `<br/>`, `<b>`, `<i>` e `<a>`: títulos viram negrito, listas viram linhas `• ` / `1. ` e sublistas saem planas | formatação mais rica se perde | escrever listas simples na descrição |
| 26 | **Corrigido.** Valores de enumeração do DOORS Next ficam em `rdfs:label` (não em `dcterms:title`) | gravar enumeração pelo nome falhava com "Válidos: None" | `oslc.title` lê `rdfs:label` e recebe o contexto de configuração |
| 27 | **Corrigido.** Listas "soltas" (`<li><p>`) perdiam a numeração na leitura, e sublistas ganhavam uma linha em branco que mudava a lista ao regravar | ler e regravar alterava o texto | leitura trata `<p>` dentro de `<li>`; ler → gravar → ler é idempotente (testado) |
| 29 | `rm_get_requirement` mostra só os links que o tipo (shape) declara | um link de tipo fora do shape não aparece | aceito: no DOORS Next todos os tipos de link estão no shape, e o resto do recurso é técnico (`accessControl`, `serviceProvider`...) |
| 30 | **Corrigido na revisão.** `<ul>` digitado sem fechamento travava a conversão da descrição; `<ol start>` não era convertido; texto com `<`/`&` soltos virava XML inválido; enumeração com vários valores e pessoa (login) falhavam na gravação do RM; atributo numérico era tratado como link; artefato ligado ilegível (403/404) derrubava a leitura; estimativa -1 aparecia como "-1h59" | — | testes em `tests/` para cada caso |
| 28 | O processo do EWM pode exigir campos para salvar (ex.: "o atributo X precisa ser preenchido") | `ccm_update_workitem` responde HTTP 403 com a mensagem do servidor | a skill mostra a mensagem e pede o valor do campo |

## 2.5 Pontos a verificar

- [x] ~~Conferir na UI que um artefato embutido por `![[id]]` aparece no texto (item 19).~~ Confirmado: a tool grava
      o embed como o editor do DOORS Next
      (`<span class="com-ibm-rdm-editor-EmbeddedResourceDecorator minimised"><a class="embedded" href="URL"> </a></span>`)
      e a UI o exibe.
- [x] ~~Verificar se links de módulo e de outras configurações precisam ser lidos (itens 20 e 21).~~ Decidido: não.

Decisões registradas:

- **Links de módulo e de outras configurações** (itens 20 e 21): não são lidos; as tools `rm_*` trabalham no stream
  padrão do alm.json e com os links do próprio artefato.

- **Nomes:** o MCP não traduz nada. No CCM, o cabeçalho mostra só os campos e links mapeados no alm.json, com o nome
  de lá. No RM, o cabeçalho mostra os links preenchidos (só leitura), com os nomes do próprio DOORS Next (o
  alm.json do RM não guarda campos nem links).
- **Embeds só no RM:** `[id título](alvo)` no corpo + o artefato na lista `embedded` do cabeçalho (no bundle da
  alm-sync, a regra fixa da seção 5.7 dispensa a lista). No EWM, citar outro WI é escrever
  "Tarefa 123" no texto; o EWM cria o link "Menções" sozinho.

# 3. Instalação e configuração

## 3.1 Credenciais

| SO | Arquivo |
|---|---|
| Linux/Mac | `~/.config/mcp-alm/alm.properties` |
| Windows | `%APPDATA%\mcp-alm\alm.properties` |
| Outro caminho | variável de ambiente `MCP_ALM_CONFIG` |

```ini
[DEFAULT]
server = https://alm.SEU-SERVIDOR
user = SEU_USUARIO
password = SUA_SENHA
```

- `server` sem barra final e sem context root (o MCP monta `/ccm`, `/rm`, `/qm`, `/gc`, `/jts`).
- A senha pode conter `%` (a interpolação do `configparser` está desligada).
- CA corporativa: `export REQUESTS_CA_BUNDLE=/caminho/ca.pem` antes de iniciar o cliente.
- Proteja o arquivo: `chmod 600 ~/.config/mcp-alm/alm.properties`.

## 3.2 Registro no cliente

```bash
cd /caminho/para/mcp-alm
uv sync

# Claude Code
claude mcp add alm -- uv run --directory /caminho/para/mcp-alm mcp-alm

# a partir do wheel publicado (dist/)
claude mcp add alm -- uvx --from /caminho/mcp_alm-1.0.11-py3-none-any.whl mcp-alm
```

Kiro / Claude Desktop (`mcp.json` / `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "alm": {
      "command": "uv",
      "args": ["run", "--directory", "/caminho/para/mcp-alm", "mcp-alm"],
      "env": { "REQUESTS_CA_BUNDLE": "/caminho/ca.pem" }
    }
  }
}
```

> O nome do servidor (`alm`) define o nome completo das tools no cliente: no Claude Code, `ccm_list_workitems` vira
> `mcp__alm__ccm_list_workitems`. Use esse nome em `allowed-tools` das skills.

## 3.3 Verificação

1. `whoami` → deve devolver `{userUUID, userId, name, emailAddress, archived}` do usuário do `alm.properties`.
2. `list_project_areas(app_type="CCM")` → lista as project areas visíveis.
3. Depuração: `uv run mcp dev mcp_alm/server.py` abre o MCP Inspector.
4. Testes: `uv run pytest`.

# 4. Conceitos essenciais do ELM para quem escreve skills

| Conceito | O que é | Como aparece no MCP |
|---|---|---|
| Project area | "projeto" em cada app (CCM, RM, QM têm project areas distintas) | `project_area_uuid` / `project_area_identifier` (`_abc...`) |
| Team area | time dentro da project area (hierárquico) | `identifier` de `ccm_list_team_areas`, com `parent` |
| Timeline / iteração | calendário de releases e sprints | `identifier` de `ccm_list_iterations` (`name` único, vira caminho `2026/Sprint 01` se repetido) |
| Plano de iteração | backlog/quadro de um time numa iteração | `ccm_list_iteration_plans` → `{name, identifier, team-area, iteration}` |
| Categoria (Filed Against) | classificação do work item; define o time | valores de `ccm_list_field_values(..., "rtc_cm:filedAgainst")` |
| Tipo de work item | `task`, `defect`, `com.ibm.team.apt.workItemType.story`... | `identifier` de `ccm_list_workitem_types` |
| Atributo | campo do tipo, em qname | `attribute` de `ccm_list_workitem_fields` (`rtc_cm:filedAgainst`, `oslc_cmx:priority`) |
| Workflow | estados e ações do tipo | `ccm_list_workitem_states` |
| Componente / stream (RM) | configuração versionada dos requisitos | `rm_get_configuration` → `{component, configuration}` |
| Pasta (RM) | `FR_...` | `rm_list_folders` |
| Tipo de artefato (RM) | `OT_...` (RF, RNF, HU...) | `rm_list_requirement_types` |
| Configuração global (GC) | agrega streams/baselines de RM, QM, CCM | `get_global_configuration`, `search_global_configuration` |

# 5. Referência das tools

Todas as tools devolvem JSON. Erros chegam à IA como texto (`ToolError`) com a mensagem do MCP ou do servidor
(`HTTP 403 em POST https://...: <corpo>`).

## 5.1 Tools de skill — EWM (`ccm_*`)

| Tool | Parâmetros | Retorno | Uso |
|---|---|---|---|
| `ccm_list_team_areas` | `project_area_identifier` | `[{name, identifier, parent}]` | setup: `team-areas` |
| `ccm_list_members` | `project_area_identifier`, `team_area_identifiers[]` (≥1) | `[{identifier (login), name, team-areas[]}]` | setup: `members` |
| `ccm_list_workitem_types` | `project_area_identifier` | `[{identifier, name}]` | setup: `workitem-types` |
| `ccm_list_workitem_fields` | `project_area_identifier`, `workitem_type` | `[{name, attribute, required, kind}]` (sem os tipos de link) | setup: `fields` (inclua sempre os `required`) |
| `ccm_list_link_types` | `project_area_identifier`, `workitem_type` | `[{name, attribute}]` | setup: `link-types` (só os que o projeto usa; nome à escolha) |
| `ccm_list_iterations` | `project_area_identifier` | `[{name, identifier, start-date?, end-date?, parent?}]` | setup: `iterations` |
| `ccm_list_iteration_plans` | `project_area_identifier`, `iteration_identifiers[]?` | `[{name, identifier, team-area, iteration}]` | setup: `plans` |
| `ccm_create_iteration` | `project_area_identifier`, `parent`, `name`, `start_date`, `end_date?`, `iteration_id?`, `iteration_type?` | iteração criada | setup (permissão "Modify structures of iterations") |
| `ccm_create_iteration_plan` | `project_area_identifier`, `name`, `iteration`, `plan_type`, `team_area?` | plano criado | setup; `plan_type` ∈ {`com.ibm.team.apt.plantype.kanbanBoard`, `com.ibm.team.apt.plantype.product.backlog`} |
| `ccm_list_workitems` | `project_area_identifier`, ao menos um de `iteration`/`team_areas`/`owner`; `state?` (nome), `workitem_type?` | `[{id, title, type, state, owner, iteration, url}]` (≤1000) | consultas do dia a dia; `team_areas` inclui subtimes |
| `ccm_get_workitem` | `workitem_id`, `fields` (= `workitem-types[tipo].fields`), `link_types?` (= `link-types`) | documento Markdown + YAML (ver 5.7) | ler um work item |
| `ccm_list_field_values` | `project_area_identifier`, `workitem_type`, `attribute` | `{kind, required, values: [{identifier, name, default}]}` | valores válidos antes de criar/atualizar |
| `ccm_create_workitem` | `project_area_identifier`, `workitem_type`, `summary`, `description?` (Markdown), `fields{chave: valor}?`, `parent?` | resumo do work item | criar |
| `ccm_list_workitem_states` | `workitem_id` | `{state, actions: [{name, result-state}]}` | ver transições possíveis |
| `ccm_update_workitem` | `workitem_id`, `fields?`, `description?` (Markdown; substitui a inteira), `state?` (nome) | resumo do work item | editar e/ou mudar estado |

**Valores em `fields`** (`ccm_create_workitem`/`ccm_update_workitem`): use o `identifier` de
`ccm_list_field_values`. O MCP converte:

- URL (`https://...`) → usada como está;
- atributo de usuário (`dcterms:contributor`...) → login vira URL do JTS;
- UUID `_...` em categoria/iteração/time/release → URL do item;
- literal com `.literal.` (ex. `priority.literal.l1`) → URL da enumeração;
- qualquer outro texto/número/booleano → literal.

`kind` de um campo: `category`, `iteration`, `team-area`, `release`, `member`, `enumeration`, `text`, `integer`,
`date`, `boolean`, `resource`. Para `text`/`integer`/`date`/`member` a lista `values` vem vazia (membros estão no
alm.json).

## 5.2 Tools de skill — DOORS Next (`rm_*`)

Todas recebem `project_area_identifier`, `component` e `configuration` (id da **stream**) do alm.json, exceto as duas
primeiras.

| Tool | Parâmetros extras | Retorno |
|---|---|---|
| `rm_get_configuration` | — | `{component, configuration}` (primeiro componente, primeira stream) |
| `rm_list_members` | — | `[{identifier, name}]` |
| `rm_list_folders` | `component`, `configuration` | `[{name ('01-Requisitos/Funcionais'), identifier ('FR_...')}]` (sem a raiz) |
| `rm_list_requirement_types` | `component`, `configuration` | `[{name, identifier ('OT_...')}]` |
| `rm_search_requirements` | `text?`, `folder?`, `requirement_type?` (≥1) | `[{id, title, type, folder, modified, url}]` (≤1000; `modified` em ISO 8601 UTC) |
| `rm_count_folder` | `folder` | `{folder, count}` (só a pasta, sem subpastas; `oslc:totalCount` do servidor) |
| `rm_list_folder` | `folder` | `[{id, title, modified}]` (só a pasta, sem teto, ordenado por id; `modified` em ISO 8601 UTC) |
| `rm_list_modified` | `requirement_ids` (lista; um id = consulta individual) | `[{id, title, modified}]` (`modified` em ISO 8601 UTC; id inexistente não volta) |
| `rm_get_requirement` | `requirement_id` (numérico) | documento Markdown + YAML (ver 5.7) |
| `rm_sync_plan` | `folders` (rm.folders `{nome: FR_}`), `dest` (caminho absoluto da raiz do bundle) | lista as pastas, regrava `<dest>/sync.md` (estados na seção 5.7), apaga os removidos; `{pastas: {nome: {total, listados, <estado>: n}}, removidos, inconsistentes, nao_confirmados, a_baixar, a_subir, conflitos: [{id, title, folder, path}]}` |
| `rm_download_requirements` | `dest`, `requirement_ids?` (sem = próximos `novo`/`desatualizado`/`erro` do `sync.md`; com ids, também `conflito`), `limit?` (50) | grava `<dest>/<pasta>/<id>-<slug>.md` (documento de `rm_get_requirement(links="bundle")`), apaga `<id>-*.md` antigo, atualiza `sync.md` (`sincronizado`, ou `normalizado` se o md ficou diferente do ALM pela regra embed/link) e, com a fila vazia, `index.md`; `{baixados, erros, restantes}` |
| `rm_upload_requirements` | `dest`, `requirement_ids?` (sem = próximos `atualizado`/`normalizado`, só se o ALM não mudou desde o download; com ids, sobe sem checar), `limit?` (50) | sobe título e corpo do md (artefato do bundle = embed, o resto = link), rebaixa o requisito (`sincronizado`); ALM mudou = `conflito` e nada sobe; `{enviados, erros, conflitos, restantes}` |
| `rm_create_requirement` | `requirement_type`, `folder`, `title`, `text` (Markdown ou XHTML), `embedded?` | `{id, title, url}` |
| `rm_update_requirement` | `requirement_id`, `title?`, `text?` (substitui o texto inteiro), `embedded?` | `{id, title, url}` |

Em `text`, `[x](2001)` cita o artefato 2001 no ponto do texto: vira embed se `"2001"` está em `embedded`
(parâmetro, no formato do cabeçalho) e hyperlink se não está; o alvo também pode ser a URL (inclusive link da UI web
com `artifactURI`) ou o arquivo do bundle (`../03-Regras/2001-rn-x.md`).

## 5.3 Tools IBM — Common

| Tool | Parâmetros principais | Retorno |
|---|---|---|
| `whoami` | — | usuário autenticado |
| `get_user` | `user_uuid` **ou** `search_term` (≥3) | usuário, ou `{requires_selection: true, matching_users[≤5]}` |
| `list_project_areas` | `app_type` (CCM/RM/QM/GC), `search_name?`, `cm_enabled?` | `[{name, project_area_uuid, url, summary, description, cm_enabled}]` |
| `get_project_area` | `app_type`, `project_area_uuid` **ou** `name`, `include_team_areas?`, `include_timelines?`, `include_associations?` | project area ou `requires_selection` |
| `get_global_configuration` | `gc_config_id` (int) | recurso + `contributedConfigs`, `localConfigs` |
| `search_global_configuration` | `gc_project_area_uuid`, `search_term='*'`, `configuration_type` (Stream/Baseline/*) | `[{url, title, types}]` |
| `list_linked_requirements` / `list_linked_workitems` / `list_linked_testartifacts` | `source_url` | `[{link_type, url, title?}]` |
| `link_workitem_and_requirement` | `workitem_url`, `requirement_url`, `link_type` (implements, affects, tracks + formas *by) | work item atualizado |
| `link_workitem_and_testartifact` | `workitem_url`, `testartifact_url`, `link_type` (affects, blocks, related, tests + *by) | work item atualizado |
| `link_testartifact_and_requirement` | `testartifact_url`, `requirement_url`, `link_type` (validates/validatedby), `gc_context?` | artefato atualizado |

## 5.4 Tools IBM — Requirements (DOORS Next)

`get_project_components`, `get_rm_component_types`, `list_rm_component_folders`, `get_rm_component_configuration`,
`get_requirement`, `search_requirement` (até 100), `create_requirement`. Trabalham com URLs completas
(`component_url`, `configuration_url`, `artifact_type_url`, `folder_url`). Sem configuração, usam a primeira stream.

## 5.5 Tools IBM — Work Items (EWM)

| Tool | Destaques |
|---|---|
| `get_workitem` | por número ou URL; `fetch_all=true` traz todos os atributos, links e `comments` |
| `get_workitem_schema` | `include`: attributes, enumerations, workflows, linkTypes, createMetadata (exige `workitem_type`) |
| `list_workitem_categories` | `[{itemId, name, archived, defaultTeamArea?}]`, `limit` ≤ 500 |
| `list_workitem_releases` | `[{itemId, name, archived}]` |
| `search_workitems` | `filter` JSON da doc IBM (AND; is/equals, is not, in, before/after, contains) |
| `create_workitem` | `attributes` e `links` como **strings JSON** |
| `add_comment_to_workitem` | `workitem_id`, `comment`, `mentions[]` (logins viram `@login`) |

## 5.6 Tools IBM — Test (ETM, somente leitura)

`get_testartifact`, `get_testartifact_schema`, `search_testartifact` (`filters` por igualdade: title, id, owner,
creator, created, modified, description ou qname), `get_qm_component`, `get_qm_component_configuration`.

`artifact_type` ∈ `TestPlan`, `TestCase`, `TestSuite`, `TestScript`, `TestCaseExecutionRecord`, `TestCaseResult`,
`TestSuiteExecutionRecord`, `TestSuiteResult`.

## 5.7 Leitura em Markdown + YAML (`ccm_get_workitem`, `rm_get_requirement`)

As duas tools devolvem um documento: cabeçalho YAML + corpo Markdown. O MCP **não traduz nomes**. Exemplos com
dados fictícios:

**Work item** — `ccm_get_workitem("1001", fields=workitem-types["Tarefa"].fields, link_types=link-types)`:

```markdown
---
id: 1001
type: Tarefa
title: Implementar cadastro de clientes
state: Em Desenvolvimento
url: "https://alm.example.com/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/1001"
creator: Ana Souza
created: "2026-01-10 09:00"
modified: "2026-01-12 14:30"
attributes:
  Categoria: Backend
  Planejado para: Sprint 01
  Responsável: Bruno Lima
  Estimativa: "4h"
  Prioridade: Alta
links:
  Pai:
    - "1000: Épico de cadastro"
  Implementa requisito:
    - "2001: RN - Validar CPF do cliente"
  Menções:
    - "1002: Criar tela de cadastro"
---
**Contexto**\
Cadastrar clientes com validação de documento.

1. criar a API
2. integrar com a Tarefa 1002

## Comentários

**bruno.lima · 2026-01-12 14:30**

Iniciado.
```

**Requisito** — `rm_get_requirement(pa, componente, stream, "2010")`:

O cabeçalho do requisito segue o Google OKF v0.2: campos OKF padrão (`type`, `title`, `description?`, `resource`,
`tags` = [pasta]), depois `sources` (OKF §5.1: a fonte no DOORS Next; `author` = quem criou, `last_modified` =
última modificação no ALM, `last_modified_by` = quem modificou por último), `generated` (OKF §5.2: `at` = quando o
documento foi gerado) e, por fim, as extensões RM (`id`, `created`, `links`, `embedded`).
`verified`/`status`/`stale_after` só aparecem com um sinal real do artefato.

```markdown
---
type: Caso de Uso
title: UC - Cadastrar cliente
resource: "https://alm.example.com/rm/resources/TX_exemplo2010"
tags:
  - "03-Casos de Uso"
sources:
  - id: doors-next
    resource: "https://alm.example.com/rm/resources/TX_exemplo2010"
    author: "human:ana.souza"
    last_modified: "2026-01-10T13:05:00Z"
    last_modified_by: "human:bruno.lima"
generated:
  by: "process:alm-mcp/1.0.14"
  at: "2026-01-11T19:20:00Z"
id: 2010
created: "2026-01-05T13:00:00Z"
links:
  Vincular A:
    - "2002: RN - Cliente deve ser maior de idade"
  Implementado por:
    - "1001: Implementar cadastro de clientes"
embedded:
  - "2001: RN - Validar CPF do cliente"
---
## Pré-condição

O usuário está autenticado.

## Fluxo Básico

1. O usuário informa os dados do cliente.
2. O sistema valida o documento: [2001 RN - Validar CPF do cliente](https://alm.example.com/rm/resources/TX_exemplo2001)
3. O sistema grava o cliente.
```

| Parte | Work item | Requisito |
|---|---|---|
| Cabeçalho fixo | id, type, title, state, url, creator, created, modified, closed | OKF v0.2: type, title, description?, resource, tags [pasta], sources[{id, resource, author, last_modified, last_modified_by}], generated{by, at}; extensões: id, created |
| `attributes` | só os de `fields` (alm.json), com o nome de lá | — |
| `links` | só os de `link_types` (alm.json), como `id: título` | todos os preenchidos, com o nome do DOORS Next, como `id: título` (só leitura) |
| `embedded` | — | artefatos embutidos no texto, como `id: título` (fora do bundle) |
| Corpo | descrição + `## Comentários` | texto; todo artefato (embed ou hyperlink) vira `[id título](alvo)` |

Valores: no WI, datas em Brasília (`AAAA-MM-DD HH:MM`); no RM, todas as datas (`created`, `sources[].last_modified`, `generated.at`) em ISO 8601 UTC
(`...Z`); enumerações e iterações pelo nome; links como `id: título`; no WI, pessoas pelo nome e durações como
`4h`; no RM, pessoas pelo login. Campos vazios são omitidos (inclusive pessoa "unassigned").

**Corpo e gravação.** A gravação aceita o mesmo Markdown do corpo: `text` no RM e `description` no WI.

| | No servidor | Leitura | Gravação |
|---|---|---|---|
| RM | XHTML; embed = `<a class="embedded">` | Markdown; artefato vira `[2001 título](URL do ALM)` e, se embutido, entra em `embedded`; ilegível vira `[URL](URL)`. Com `links="bundle"`: artefato do bundle = embed com alvo no arquivo, o resto = link com alvo na URL, sem `embedded` | Markdown; `[x](alvo)` vira embed se o artefato está em `embedded`, senão hyperlink; alvo = URL, link da UI web, id ou arquivo `<id>-....md` |
| WI | texto com `<br/>`, `<b>`, `<i>`, `<a>`; "•" digitado | `<br/><br/>` = parágrafo, `<br/>` = `\` no fim da linha | títulos → negrito, listas → linhas `• ` / `1. ` |

`[texto](url)` é hyperlink comum nos dois. No WI não há embed: "Tarefa 1002" no texto é texto puro, e o EWM cria
sozinho o link "Menções" ao salvar. Ler → gravar → ler não muda o corpo (testado no servidor nos dois apps).

**Para gravar** no WI, a skill procura a chave no mapa do alm.json: `fields["Estimativa"]` → `rtc_cm:estimate`.
Pessoa vai pelo login (`members`) e duração em ms (`4h` = `14400000`). No RM, só título e texto são gravados.

**Bundle da alm-sync (`sync.md`).** Estados de cada artefato:

| Estado | Quando | Ação |
|---|---|---|
| `novo` | id ainda não baixado | download |
| `sincronizado` | md == ALM | — |
| `desatualizado` | `modified` do ALM ≠ `Última atualização ALM` | download |
| `atualizado` | md alterado (sha256 ≠ `Hash`) e ALM sem mudança | upload |
| `normalizado` | md **não** alterado (hash igual), mas o ALM foge da regra embed/link do bundle (diferença invisível no md) | upload |
| `conflito` | md alterado **e** ALM mudou | a skill pergunta ao usuário: md ou ALM |
| `erro: <msg>` | falha no download | download de novo |

Mudar de pasta = remoção na antiga e criação na nova: o arquivo é apagado e a linha volta a `novo` (com edição local,
vira `conflito` e o arquivo fica).

```markdown
---
type: Relatório de Sincronismo
title: Sincronismo ALM → OKF
description: Situação de cada artefato do DOORS Next baixado neste bundle.
generated:
  by: "process:alm-mcp/1.0.14"
  at: "2026-10-07T12:00:00Z"
---
| Artefato | Pasta | Última atualização ALM | Hash | Status |
|---|---|---|---|---|
| [123 — Login](</01-Requisitos/123-login.md>) | 01-Requisitos | 2026-10-01T13:45:10Z | 9f2c…e1 | sincronizado |
| [124 — Logout](</01-Requisitos/124-logout.md>) | 01-Requisitos | 2026-10-01T15:00:00Z | 77ab…04 | conflito |
| 125 — Sessão | 01-Requisitos |  |  | novo |
| 126 — Token | 01-Requisitos |  |  | erro: HTTP 404 |
```

`Última atualização ALM` = `modified` do ALM no último download/upload (o `rm_sync_plan` não a sobrescreve);
`Hash` = sha256 completo do arquivo gravado (abreviado no exemplo). Linhas por (pasta, id).

No corpo do md do bundle a regra é fixa: **artefato do bundle (id no `sync.md`) é sempre embed; o resto é sempre
link**. O download grava `[id título](../03-Regras/2001-x.md)` para artefato do bundle e `[id título](URL do ALM)`
para os de fora. Se o ALM estiver diferente da regra, a linha fica `normalizado` e o upload corrige. O md do bundle não
tem `embedded`, e o `links:` do cabeçalho não sobe. Formatos sugeridos ao escrever no md:

| Referência | Formato | Upload |
|---|---|---|
| embed (artefato do bundle) | `[<id>](<id>)` | embed |
| link para artefato de outra PA | `[<id>](<URL do ALM>)` | `<a href>` para o artefato |
| link externo | `[<texto descritivo>](<url>)` | `<a href>` como está |

No upload, `artifact_ref` resolve o alvo: URL com `/rm/resources/` (ou link da UI web com `artifactURI`) = artefato
pela URL; `http:`/`mailto:`/`#` = link externo; o resto, pelo id no início do nome (`2001`, `../03-Regras/2001-x.md`,
`2001 REG Nome`). Id é procurado na PA do bundle, então artefato de outra PA vai pela URL.

# 6. O arquivo de projeto `alm/pa_<project_area>.json`

É a "memória" das skills: guarda nomes e identifiers já descobertos para que a IA não precise consultar o servidor a
cada pedido. É **gravado pela skill `alm-setup`** (o MCP não lê nem escreve esse arquivo). Um arquivo por project area
do CCM, com o nome em snake_case: `alm/pa_meu_projeto.json`.

Estrutura (exemplo fictício, resumido):

```json
{
  "ccm": {
    "project-area": "Projeto Exemplo",
    "project-area-identifier": "_PA_CCM_EXEMPLO",
    "team-areas": {
      "Time Alfa": { "identifier": "_TA_ALFA", "categories": { "Backend": "_CAT_BACKEND" } }
    },
    "members": { "ana.souza": "Ana Souza", "bruno.lima": "Bruno Lima" },
    "workitem-types": {
      "Tarefa": {
        "identifier": "task",
        "fields": {
          "Categoria": "rtc_cm:filedAgainst",
          "Planejado para": "rtc_cm:plannedFor",
          "Responsável": "dcterms:contributor",
          "Estimativa": "rtc_cm:estimate",
          "Prioridade": "oslc_cmx:priority"
        }
      }
    },
    "iterations": {
      "Release 1": { "identifier": "_IT_R1", "plans": { "Sprint 01": { "identifier": "_PLAN_S01", "team-area": "_TA_ALFA" } } }
    },
    "link-types": {
      "Pai": "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.parent",
      "Filhos": "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.children",
      "Implementa requisito": "oslc_cm:implementsRequirement",
      "Menções": "rtc_cm:com.ibm.team.workitem.linktype.textualReference.textuallyReferenced"
    }
  },
  "rm": {
    "project-area": "Projeto Exemplo",
    "project-area-identifier": "_PA_RM_EXEMPLO",
    "component": "_COMPONENTE",
    "configuration": "_STREAM",
    "members": { "ana.souza": "Ana Souza" },
    "folders": { "03-Casos de Uso": "FR_CASOS_DE_USO", "04-Regras": "FR_REGRAS" },
    "requirements-types": { "Caso de Uso": "OT_CASO_DE_USO", "Regra de Negócio": "OT_REGRA" }
  }
}
```

| Chave | Origem (alm-setup) |
|---|---|
| `ccm.workitem-types[tipo].fields` | `ccm_list_workitem_fields` (nome à escolha → `attribute`) |
| `ccm.link-types` | `ccm_list_link_types` (nome à escolha → `attribute`); vale para todos os tipos |
| `rm.requirements-types` | `rm_list_requirement_types` (`{nome: identifier}`) |

No CCM, os nomes são do projeto: é com eles que `ccm_get_workitem` mostra os campos e links. No RM não há mapa:
`rm_get_requirement` mostra os nomes do DOORS Next, e a gravação usa os mesmos nomes.

Regras de uso:

- **Plano → filtro**: `ccm_list_workitems(iteration=iterations[it].identifier,
  team_areas=[iterations[it].plans[plano]["team-area"]])`; plano sem `team-area` → omita `team_areas`.
- Os identifiers do RM (`component`, `configuration`, `folders`, `requirements-types`) vão direto nas tools `rm_*`.
- **Leitura de WI**: passe o mapa do tipo (`fields`) e o `link-types` para `ccm_get_workitem`.
- **Gravação de WI**: procure a chave no mesmo mapa (`fields["Estimativa"]` → `rtc_cm:estimate`).
- Vincule a project area do RM pela associação: `get_project_area(app_type="CCM", project_area_uuid=...,
  include_associations=true)`.
- O arquivo pode ir para o git do projeto (não tem segredos), permitindo que todo o time use as mesmas skills.

# 7. Guia de construção de skills

## 7.1 O que é uma skill

Uma skill é uma pasta com um `SKILL.md` (e, opcionalmente, arquivos de apoio) que o agente carrega **quando a
descrição combina com o pedido do usuário**. No Claude Code ela fica em `.claude/skills/<nome>/SKILL.md` (projeto) ou
`~/.claude/skills/<nome>/SKILL.md` (usuário); no Kiro, em `.kiro/skills/<nome>/SKILL.md`.

```
skills/
  alm-ccm/
    SKILL.md            obrigatório: frontmatter + instruções
    reference.md        opcional: detalhes carregados só quando necessário
    examples.md         opcional: exemplos de pedidos e chamadas
```

Só o `name` e a `description` ficam sempre no contexto; o corpo do `SKILL.md` é lido quando a skill é acionada, e os
arquivos de apoio só quando o corpo mandar. Isso é o que torna as skills baratas: **escreva o essencial no
SKILL.md e mova tabelas longas para arquivos de apoio**.

## 7.2 Anatomia do SKILL.md

```markdown
---
name: alm-ccm
description: >-
  Work items do IBM EWM (RTC) via MCP alm: listar, criar, atualizar, mudar estado e comentar
  itens de trabalho (WI), tarefas, defeitos, itens de backlog (IB) e histórias. Use quando o
  usuário falar de sprint, plano, backlog, tarefa, defeito, work item ou número de WI.
allowed-tools: mcp__alm__ccm_list_workitems, mcp__alm__ccm_list_field_values,
  mcp__alm__ccm_create_workitem, mcp__alm__ccm_update_workitem, mcp__alm__ccm_list_workitem_states,
  mcp__alm__ccm_get_workitem, mcp__alm__add_comment_to_workitem, Read
---

# alm-ccm
(instruções — ver 7.4)
```

| Campo | Obrigatório | Dica |
|---|---|---|
| `name` | sim | minúsculas e hífens; igual ao nome da pasta |
| `description` | sim | **o que** faz + **quando** usar + sinônimos/siglas que o usuário usa (WI, IB, RF, HU, CT). É o único texto usado para decidir se a skill entra em ação |
| `allowed-tools` | não (Claude Code) | tools liberadas sem confirmação enquanto a skill está ativa; use o nome completo `mcp__alm__<tool>` |

## 7.3 Processo passo a passo

1. **Defina o escopo**: uma skill por domínio (setup, ccm, rm, qm, gc). Skills pequenas são acionadas com mais
   precisão e gastam menos contexto.
2. **Liste as intenções do usuário** (ex.: "listar minhas tarefas da sprint", "criar defeito", "mover para Pronto").
3. **Mapeie cada intenção para tools** usando a seção 5; prefira as tools de skill (`ccm_*`, `rm_*`) às IBM.
4. **Defina de onde vem cada parâmetro**: do alm.json (sem chamada), do usuário, ou de uma tool de descoberta.
5. **Documente o contrato de saída** de cada tool que a skill usa (só os campos relevantes).
6. **Documente os erros conhecidos** e a reação esperada (seção 2.4 e 7.6).
7. **Escreva a description** com sinônimos e siglas.
8. **Teste** com pedidos reais (seção 7.8) e ajuste.

## 7.4 Estrutura recomendada do corpo

```markdown
# <nome>

## Pré-requisitos
- Leia `alm/pa_*.json`. Se não existir ou faltar a chave X, rode a skill alm-setup.

## Glossário
| Termo do usuário | Significado | Onde está |

## Fluxos
### <intenção 1>
1. passo, tool, parâmetros e origem de cada um
2. ...
Saída ao usuário: formato (tabela, lista).

## Contrato das tools
| Tool | Entrada | Saída (campos usados) |

## Erros e reações
| Mensagem | Causa | O que fazer |

## Regras
- confirmações antes de escrita, limites, o que nunca fazer
```

## 7.5 Boas práticas específicas do mcp-alm

1. **alm.json primeiro, servidor depois.** Resolva nomes → identifiers pelo arquivo. Só chame tools de descoberta
   quando o nome não estiver lá (e sugira rodar a alm-setup para atualizar).
2. **Sempre filtre as listagens.** `ccm_list_workitems` exige `iteration`, `team_areas` ou `owner`; o limite é 1000.
3. **Valores de campo vêm de `ccm_list_field_values`.** Nunca invente UUIDs ou literais de enumeração; mostre as
   opções pelo `name` e envie o `identifier`. Use `default: true` como sugestão.
4. **Campos obrigatórios**: `ccm_list_workitem_fields` marca `required`; peça-os ao usuário antes de criar.
5. **Estado pelo nome**: `ccm_update_workitem(state="Em Desenvolvimento")`. Se falhar, chame
   `ccm_list_workitem_states` e mostre as ações possíveis a partir do estado atual.
6. **Confirme antes de escrever.** Toda criação/atualização/link deve mostrar um resumo e pedir "confirma?" —
   escritas no ELM são visíveis a todo o time e não há desfazer via MCP.
7. **Uma escrita por vez e verificação pelo retorno.** As tools de escrita devolvem o recurso atualizado; use-o para
   confirmar ao usuário (id, estado, link).
8. **Links de rastreabilidade usam URLs**: pegue `url` do retorno das tools (work item, requisito, teste).
9. **RM: não combine `text` com `folder`/`requirement_type`** em `rm_search_requirements`; busque pelo texto e filtre.
10. **Texto rico no RM**: `text` aceita XHTML (`<p>`, `<ul>`, `<b>`); texto simples vira parágrafo.
11. **Permissões**: `HTTP 403`/"Permission Denied" em criação de iteração/plano indica falta de permissão de processo
    do usuário, não erro de payload. Não fique tentando variações.
12. **Datas**: `AAAA-MM-DD`, interpretadas no fuso de Brasília.
13. **Saída ao usuário**: tabelas curtas com id, título, estado e dono; inclua a `url` quando útil.

## 7.6 Tabela de erros comuns

| Mensagem (trecho) | Causa | Reação na skill |
|---|---|---|
| `Arquivo de credenciais não encontrado` | falta `alm.properties` | orientar a criação (seção 3.1) |
| `Chave(s) ausente(s) em ...` | `server`/`user`/`password` vazios | idem |
| `Falha de login em ...` | usuário/senha errados | pedir para corrigir o arquivo; não repetir |
| `Informe ao menos um filtro` | listagem sem filtro | pedir iteração, time ou owner |
| `Tipo 'X' não existe em ...` | `workitem_type` errado | usar `identifier` do alm.json |
| `Atributo 'X' não existe no tipo` | atributo fora do tipo | revisar `fields` do alm.json |
| `Estado 'X' não existe no workflow. Estados: ...` | nome de estado errado | mostrar os estados listados |
| `O servidor não mudou o estado` | transição bloqueada (pré-condição) | mostrar `ccm_list_workitem_states` e campos obrigatórios |
| `Valor 'X' inválido para 'Y'. Válidos: ...` (RM) | enumeração inválida | oferecer os válidos |
| `HTTP 400` em busca RM | `text` + `folder`/`type` | buscar só por texto |
| `HTTP 403` / `Permission Denied` | falta de permissão no processo | informar; pedir ao admin |
| `HTTP 412` | conflito de edição concorrente | reler o item e repetir uma vez |
| `requires_selection: true` | termo ambíguo (usuário/project area) | mostrar as opções e perguntar |
| `O servidor aceitou, mas ... não apareceu` | criação sem efeito visível | verificar na UI; não repetir às cegas |

## 7.7 Modelos completos das skills do projeto

### 7.7.1 `alm-setup`

````markdown
---
name: alm-setup
description: >-
  Configura ou atualiza o arquivo alm/pa_<project area>.json usado pelas skills alm-ccm e alm-rm:
  project area, times, membros, tipos de work item e campos, iterações, planos, componente,
  stream, pastas e tipos de requisito do IBM ELM. Use ao iniciar um projeto, quando faltar um
  nome no alm.json, ou para criar iterações e planos.
allowed-tools: Read, Write, mcp__alm__whoami, mcp__alm__list_project_areas,
  mcp__alm__get_project_area, mcp__alm__ccm_list_team_areas, mcp__alm__ccm_list_members,
  mcp__alm__ccm_list_workitem_types, mcp__alm__ccm_list_workitem_fields,
  mcp__alm__ccm_list_link_types, mcp__alm__ccm_list_iterations, mcp__alm__ccm_list_iteration_plans,
  mcp__alm__rm_get_configuration, mcp__alm__rm_list_members, mcp__alm__rm_list_folders,
  mcp__alm__rm_list_requirement_types
---

# alm-setup

## Fluxo
1. `whoami` — testa a conexão. Erro de credencial: oriente sobre `~/.config/mcp-alm/alm.properties` e pare.
2. `list_project_areas(app_type="CCM", search_name=<trecho>)` — o usuário escolhe; arquivo:
   `alm/pa_<nome em snake_case>.json`. Se já existir, leia e atualize só o que foi pedido.
3. `ccm_list_team_areas` — mostre a árvore (via `parent`) e pergunte quais times o projeto usa.
4. `ccm_list_members(team_area_identifiers=<escolhidos>)` → `members {login: nome}`.
5. `ccm_list_workitem_types` — pergunte quais tipos usar; para cada um
   `ccm_list_workitem_fields` → `fields {nome: attribute}` (inclua sempre `required: true`). O nome é do
   projeto (pode traduzir "Filed Against" para "Categoria"): é com ele que `ccm_get_workitem` mostra o campo.
   `ccm_list_link_types` (de qualquer tipo) → pergunte quais links o projeto usa → `link-types {nome: attribute}`.
6. `ccm_list_iterations` — pergunte quais iterações (ex.: as ativas pelo intervalo de datas);
   `ccm_list_iteration_plans(iteration_identifiers=...)` → `iterations[it].plans`
   (omita `team-area` quando vier null).
7. RM: `get_project_area(app_type="CCM", project_area_uuid=..., include_associations=true)` para achar
   a project area do RM (ou `list_project_areas("RM")`); `rm_get_configuration`, `rm_list_members`,
   `rm_list_folders`, `rm_list_requirement_types` → bloco `rm` (`requirements-types {nome: identifier}`; o RM não
   guarda campos nem links: `rm_get_requirement` mostra os nomes do DOORS Next).
8. Grave o JSON (estrutura em "Formato") e mostre um resumo de contagens.

## Criar iteração/plano (só se pedido)
- `ccm_create_iteration(parent, name, start_date, end_date)` — datas AAAA-MM-DD.
- `ccm_create_iteration_plan(name, iteration, plan_type, team_area?)` — plan_type:
  `com.ibm.team.apt.plantype.kanbanBoard` ou `com.ibm.team.apt.plantype.product.backlog`.
- Confirme com o usuário antes. Permission Denied = falta de permissão; não tente variações.
- Depois, atualize `iterations` no alm.json.

## Formato
(cole aqui o JSON da seção 6 do manual)

## Regras
- Nunca grave senha ou dados do alm.properties no alm.json.
- Nomes são as chaves; identifiers são os valores. Não invente identifiers.
````

### 7.7.2 `alm-ccm`

````markdown
---
name: alm-ccm
description: >-
  Work items do IBM EWM/RTC via MCP alm: listar, criar, atualizar, mudar estado, comentar e
  ligar itens de trabalho (WI), tarefas, defeitos, histórias e itens de backlog (IB) de sprints,
  planos e times. Use para "minhas tarefas", "WI 1234", "criar defeito", "mover para Pronto".
allowed-tools: Read, mcp__alm__ccm_list_workitems, mcp__alm__ccm_get_workitem,
  mcp__alm__ccm_list_field_values, mcp__alm__ccm_create_workitem, mcp__alm__ccm_update_workitem,
  mcp__alm__ccm_list_workitem_states, mcp__alm__add_comment_to_workitem
---

# alm-ccm

## Pré-requisitos
Leia `alm/pa_*.json` (se houver mais de um, pergunte qual). Sem arquivo → skill alm-setup.

## Glossário
WI = work item · IB = item de backlog · "minhas" = owner do `whoami` · sprint = iteração · plano = iterations[it].plans

## Fluxos
### Listar
- Sprint/plano: `ccm_list_workitems(pa, iteration=iterations[it].identifier,
  team_areas=[plano.team-area] se existir)`.
- Minhas: `owner=<login>` (+ iteration se citada). Filtros extras: `state` (nome), `workitem_type`.
- Resposta: tabela id | título | tipo | estado | dono.
### Detalhar
- `ccm_get_workitem(id, fields=workitem-types[tipo].fields, link_types=link-types)`; o tipo vem do
  `ccm_list_workitems` ou, se não souber, use o mapa do tipo mais provável e confira `type` no retorno.
- O retorno é Markdown + YAML: mostre-o como está. Os nomes em `attributes`/`links` são os da PA.

### Nome da PA → chave (sempre que for gravar)
O cabeçalho usa o **nome** da PA; a gravação usa a **chave**. Procure no mesmo mapa:

| Cabeçalho lido | Mapa na PA | Gravação |
|---|---|---|
| `Estimativa: "4h"` | `workitem-types["Tarefa"].fields["Estimativa"]` = `rtc_cm:estimate` | `fields={"rtc_cm:estimate": 14400000}` (ms) |
| `Responsável: Bruno Lima` | `fields["Responsável"]` = `dcterms:contributor`; login em `members` | `fields={"dcterms:contributor": "bruno.lima"}` |
| `Prioridade: Alta` | `fields["Prioridade"]` = `oslc_cmx:priority` | `ccm_list_field_values(pa, "task", "oslc_cmx:priority")` → identifier do "Alta" |

Exemplo — "muda a estimativa do WI 1001 para 6h":
1. `workitem-types["Tarefa"].fields["Estimativa"]` → `rtc_cm:estimate`.
2. Confirme e chame `ccm_update_workitem("1001", fields={"rtc_cm:estimate": 21600000})`.

Nome que não está na PA → não invente a chave: rode a alm-setup para mapear o campo.
### Criar
1. Tipo: `workitem-types[nome].identifier`.
2. Para cada campo `required` e cada campo citado: se kind for category/iteration/team-area/
   release/enumeration, `ccm_list_field_values(pa, tipo, attribute)` e mostre os `name`
   (sugira `default: true`); member → `members` do alm.json; iteração → `iterations`.
3. Mostre o resumo e peça confirmação.
4. `ccm_create_workitem(pa, tipo, summary, description=<Markdown>, fields={chave da PA: identifier}, parent?)`.
5. Responda com id e url.
### Atualizar / mudar estado
- `ccm_update_workitem(id, fields?, description=<Markdown>?, state="<nome>")`, após confirmação.
- `description` substitui a descrição inteira: leia com `ccm_get_workitem`, altere o corpo (sem o cabeçalho e
  sem `## Comentários`) e mande tudo.
- Para citar outro WI na descrição, escreva "Tarefa 1002" (tipo + número): o EWM cria o link em "Menções".
- Erro "o atributo X precisa ser preenchido" (HTTP 403): o processo exige o campo para salvar; peça o valor e
  inclua-o em `fields`.
- Erro de estado → `ccm_list_workitem_states(id)` e ofereça os `result-state` possíveis.
### Comentar
- `add_comment_to_workitem(id, comment, mentions=[logins])`.

## Erros
(tabela 7.6 do manual, linhas de CCM)

## Regras
- Nunca invente identifiers; sempre confirme escritas; uma escrita por vez.
````

### 7.7.3 `alm-rm`

````markdown
---
name: alm-rm
description: >-
  Requisitos do IBM DOORS Next via MCP alm: buscar, ler, criar e atualizar requisitos funcionais
  (RF), não funcionais (RNF), histórias de usuário (HU), casos de uso (UC), regras, mensagens e
  especificações, embutir artefatos no texto e ligá-los entre si ou a work items. Use para
  "requisito", "RF-123", "criar HU", "regra do caso de uso".
allowed-tools: Read, mcp__alm__rm_search_requirements, mcp__alm__rm_count_folder, mcp__alm__rm_list_folder,
  mcp__alm__rm_list_modified, mcp__alm__rm_get_requirement, mcp__alm__rm_sync_plan, mcp__alm__rm_download_requirements,
  mcp__alm__rm_upload_requirements, mcp__alm__rm_create_requirement, mcp__alm__rm_update_requirement,
  mcp__alm__link_workitem_and_requirement
---

# alm-rm

## Pré-requisitos
`rm.project-area-identifier`, `rm.component`, `rm.configuration` do alm.json vão em TODAS as chamadas.

## Fluxos
### Buscar
- Por texto: `rm_search_requirements(text=...)`; filtre o resultado por `type`/`folder` localmente.
- Por pasta/tipo (sem texto): `folder=rm.folders[nome]`, `requirement_type=rm.requirements-types[nome]`.
### Baixar / sincronizar a documentação
1. `rm_sync_plan(folders=rm.folders, dest)` → resumo.
2. `rm_download_requirements(dest)` até `restantes = 0`.
3. Para cada item de `conflitos`, **pergunte ao usuário qual versão fica (md ou ALM)**. Se for ALM,
   `rm_download_requirements(dest, requirement_ids=[id])`; se for md, `rm_upload_requirements(dest, requirement_ids=[id])`.
4. `rm_upload_requirements(dest)` até `restantes = 0`.
- No md do bundle: embed `[<id>](<id>)`, link para artefato de outra PA `[<id>](<URL do ALM>)`, link externo
  `[<texto descritivo>](<url>)`.
- Inventário avulso de uma pasta: `rm_list_folder(folder=...)` e `rm_count_folder`.
### Ler
- `rm_get_requirement(requirement_id=<número>)`. Retorno em Markdown + YAML: mostre-o como está.
- `links` vem com os nomes do DOORS Next (só leitura); `embedded` lista os artefatos embutidos no texto.
- `[2001 RN - ...](...)` no texto = o artefato 2001 está **embutido** naquele passo se `2001` está em `embedded`; senão é hyperlink.
### Embutir um artefato no texto
1. Leia com `rm_get_requirement` e pegue o corpo (o que vem depois do cabeçalho YAML).
2. Acrescente `[2003](2003)` no passo desejado e `"2003"` à lista `embedded` do cabeçalho.
3. Confirme e mande o corpo inteiro e a lista em `rm_update_requirement(..., text=<corpo>, embedded=<lista>)`
   (substitui o texto; sem `embedded`, todo link vira hyperlink).
### Criar
1. Tipo e pasta pelo alm.json (pergunte se faltar). 2. Monte `text` em Markdown; `[x](id)` com o id em `embedded` embute um artefato.
3. Confirme. 4. `rm_create_requirement(...)`. Em erro após criar, busque pelo título antes de repetir.
### Rastrear com work item
- `link_workitem_and_requirement(workitem_url, requirement_url, link_type="implements")`
  (urls vêm do retorno de ccm_* e rm_*).

## Erros
- HTTP 400 na busca: não combine text com folder/requirement_type.
- `Atributo 'X' não existe no tipo. Válidos: ...` / `Valor 'X' inválido ... Válidos:` → ofereça os válidos.
````

### 7.7.4 `alm-qm`

````markdown
---
name: alm-qm
description: >-
  Consulta (somente leitura) de testes no IBM ETM/RQM via MCP alm: caso de teste (CT), plano de
  teste (PT), suíte, script, registro de execução (TER) e resultados. Use para "caso de teste",
  "CT 55", "plano de teste", "resultado de execução".
allowed-tools: Read, mcp__alm__search_testartifact, mcp__alm__get_testartifact,
  mcp__alm__get_testartifact_schema, mcp__alm__list_project_areas, mcp__alm__list_linked_requirements
---

# alm-qm
- Project area QM: `list_project_areas(app_type="QM")` (guarde o UUID no alm.json em `qm`).
- Sigla → artifact_type: CT=TestCase, PT=TestPlan, suíte=TestSuite, script=TestScript,
  TER=TestCaseExecutionRecord, resultado=TestCaseResult.
- Buscar: `search_testartifact(pa, tipo, filters={"title": ...}|{"owner": ...})` (igualdade).
- Ler: `get_testartifact(pa, tipo, id=..., fetch_all=true)`.
- Campos do tipo: `get_testartifact_schema`.
- Não há criação/edição/comentários de teste neste MCP: diga isso ao usuário.
````

### 7.7.5 `alm-gc`

````markdown
---
name: alm-gc
description: >-
  Recursos comuns do IBM ELM via MCP alm: quem sou eu, usuários, project areas e associações,
  configurações globais (GC) e links de rastreabilidade entre requisitos, work items e testes.
  Use para "quem é", "qual project area", "o que está ligado a", "ligar X a Y".
allowed-tools: mcp__alm__whoami, mcp__alm__get_user, mcp__alm__list_project_areas,
  mcp__alm__get_project_area, mcp__alm__get_global_configuration,
  mcp__alm__search_global_configuration, mcp__alm__list_linked_requirements,
  mcp__alm__list_linked_workitems, mcp__alm__list_linked_testartifacts,
  mcp__alm__link_workitem_and_requirement, mcp__alm__link_workitem_and_testartifact,
  mcp__alm__link_testartifact_and_requirement
---

# alm-gc
- Usuário por nome: `get_user(search_term=...)`; `requires_selection` → pergunte e repita com `user_uuid`.
- Rastreabilidade: `list_linked_*(source_url)`; ligar: `link_*` com as URLs (confirme antes).
  - WI ↔ requisito: implements | affects | tracks. WI ↔ teste: affects | blocks | related | tests.
  - Teste ↔ requisito: validates (informe `gc_context` se o ETM for opt-in).
- GC: `search_global_configuration(gc_pa_uuid, search_term, "Stream"|"Baseline")`,
  `get_global_configuration(id)` mostra as configurações locais que a compõem.
````

## 7.8 Instalação das skills no projeto

Crie as pastas `skills/<nome>/SKILL.md` no repositório do projeto (ou no próprio mcp-alm) e ligue-as sem copiar:

```bash
mkdir -p .claude/skills .kiro/skills
for s in alm-setup alm-ccm alm-rm alm-qm alm-gc; do
  ln -sfn ../../skills/$s .claude/skills/$s   # Claude Code
  ln -sfn ../../skills/$s .kiro/skills/$s     # Kiro
done
```

No Windows, use `mklink /D` ou copie as pastas.

## 7.9 Testando e evoluindo uma skill

1. **Acionamento**: faça pedidos com as palavras do usuário ("lista meus WIs da sprint 3") e confira se a skill
   certa entra. Se não entrar, enriqueça a `description` com os termos usados.
2. **Economia**: conte as chamadas de tool por pedido. Um pedido de listagem com alm.json completo deve fazer **uma**
   chamada (`ccm_list_workitems`). Chamadas de descoberta repetidas indicam que falta algo no alm.json ou na skill.
3. **Escritas**: teste criação/atualização numa project area de testes; confira na UI web.
4. **Erros**: provoque os erros da tabela 7.6 (estado inválido, filtro vazio) e veja se a reação da IA segue a skill.
5. **Versionamento**: mantenha as skills no git junto do alm.json; ao mudar tools do MCP, atualize os contratos nas
   skills no mesmo commit.

## 7.10 Checklist de qualidade de uma skill

- [ ] `name` igual à pasta; `description` com o quê, quando e sinônimos/siglas.
- [ ] `allowed-tools` só com as tools que a skill usa (`mcp__alm__...`).
- [ ] Pré-requisito do alm.json explícito e o que fazer se faltar.
- [ ] Cada fluxo diz a tool, a origem de cada parâmetro e o formato da resposta.
- [ ] Nenhum identifier inventado; valores vêm do alm.json ou de `ccm_list_field_values`.
- [ ] Confirmação antes de toda escrita.
- [ ] Tabela de erros com reação.
- [ ] Corpo curto (idealmente < 200 linhas); detalhes em arquivos de apoio.

# 8. Estendendo o MCP (novas tools para skills)

Quando uma skill precisa de algo que as tools não oferecem, crie uma tool de skill:

```python
# mcp_alm/ccm.py
@tool
def ccm_minha_tool(project_area_identifier: str, ...) -> list[dict]:
    """Uma frase do que faz: [{name, identifier, ...}]. alm.json: <chave onde a skill grava>."""
    ...
```

Diretrizes do projeto:

- Use `@tool` de `mcp_alm.server`; exceções `ValueError`/`LookupError`/`RuntimeError` viram mensagens para a IA —
  escreva mensagens que digam **o que fazer** (liste os valores válidos).
- A **docstring é a documentação que a IA lê**: parâmetros, formato de saída e a chave do alm.json.
- Reaproveite `ibm/` e `infra/` (`oslc.query`, `oslc.get`, `oslc.update`, `oslc.shape`, `common.team_areas`...).
- Saída enxuta: só os campos que a skill usa.
- Adicione um teste em `tests/` com fixture RDF/XML e rode `uv run pytest`.
- Atualize o README e o contrato na skill correspondente.

# 9. Exemplos de uso ponta a ponta

**"Quais tarefas do Time Alfa estão abertas na Sprint 01?"**

```text
alm.json → iterations["Sprint 01"].identifier = _IT, plano "Sprint 01 - Time Alfa".team-area = _TA
ccm_list_workitems(project_area_identifier="_PA", iteration="_IT", team_areas=["_TA"],
                   workitem_type="task")
→ filtrar state != "Concluído"; responder em tabela
```

**"Cria um defeito de prioridade alta para o login quebrado, na Sprint 01, para a Maria"**

```text
ccm_list_field_values("_PA", "defect", "oslc_cmx:priority") → {name: "Alta", identifier: "...literal.l11"}
(confirmação do usuário)
ccm_create_workitem("_PA", "defect", "Login quebrado", description="...",
    fields={"oslc_cmx:priority": "priority.literal.l11", "rtc_cm:plannedFor": "_IT",
            "dcterms:contributor": "maria.souza", "rtc_cm:filedAgainst": "_CAT"})
```

**"Move o WI 1234 para Em Desenvolvimento e comenta que comecei"**

```text
ccm_update_workitem("1234", state="Em Desenvolvimento")
add_comment_to_workitem("1234", "Iniciado o desenvolvimento.")
```

**"Cria o RF de exportação em PDF na pasta Funcionais e liga ao WI 1234"**

```text
rm_create_requirement("_PA_RM", "_COMP", "_STREAM",
    requirement_type=rm.requirements-types["Requisito"],
    folder=rm.folders["01-Requisitos/Requisitos Funcionais"], title="Exportar relatório em PDF",
    text="O sistema deve permitir exportar o relatório em PDF.\n\nRegra: [2001](2001)", embedded=["2001"])
get_workitem("1234") → url
link_workitem_and_requirement(workitem_url, requirement_url, "implements")
```

**"Mostra o WI 1001 e aumenta a estimativa para 6h"**

```text
ccm_get_workitem("1001", fields=workitem-types["Tarefa"].fields, link_types=link-types)
→ cabeçalho: Estimativa: "4h"
workitem-types["Tarefa"].fields["Estimativa"] → rtc_cm:estimate
(confirmação do usuário)
ccm_update_workitem("1001", fields={"rtc_cm:estimate": 21600000})
```

# 10. Resumo

- Configure `alm.properties`, registre o servidor como `alm` e valide com `whoami`.
- Rode a **alm-setup** uma vez por project area para gerar `alm/pa_*.json`.
- Use **alm-ccm**, **alm-rm**, **alm-qm** e **alm-gc** no dia a dia; elas resolvem nomes pelo alm.json e chamam o
  mínimo de tools.
- Ao escrever skills: description rica, fluxos com origem de cada parâmetro, contrato de saída, erros com reação,
  confirmação antes de escrever.
