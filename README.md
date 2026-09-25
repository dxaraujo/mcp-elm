# mcp-alm

Servidor MCP para o IBM ELM via OSLC: **rm** (DOORS Next), **ccm** (EWM) e **qm** (ETM).
Ele fornece as capacidades que as skills vão consumir. As tools seguem os nomes e parâmetros do
[IBM Engineering AI Hub MCP 1.3.0](https://www.ibm.com/docs/en/engineering-lifecycle-management-suite/engineering-ai-hub/1.3.0?topic=tools-mcp-engineering-ai-hub).

## Credenciais

| SO | Arquivo |
|---|---|
| Linux/Mac | `~/.config/mcp-alm/alm.properties` |
| Windows | `%APPDATA%\mcp-alm\alm.properties` |

Para usar outro caminho, defina a variável de ambiente `MCP_ALM_CONFIG`.

```ini
[DEFAULT]
server = https://alm.SEU-SERVIDOR
user = SEU_USUARIO
password = SUA_SENHA
```

Certificado com CA próprio: `export REQUESTS_CA_BUNDLE=/caminho/ca.pem`.

A autenticação usa o form Jazz (`j_security_check`). Se o servidor responder 401 pedindo Basic, o Basic é usado automaticamente.

## Instalação

```bash
uv sync
claude mcp add alm -- uv run --directory /caminho/para/mcp-alm mcp-alm
# depuração com o MCP Inspector
uv run mcp dev mcp_alm/server.py
```

## Contrato de saída

- Recursos OSLC: `{url, id, title, types, properties{}, links{}}`, com chaves em qname (`dcterms:title`).
  Cada link é `{url, title?}`. `get_workitem` e `search_workitems` trazem o `title` dos recursos ligados (estado,
  prioridade, iteração...) na mesma requisição (`oslc.properties`/`oslc.select` com `{dcterms:title}`) e completam
  o nome dos usuários do JTS pela lista em cache do Reportable REST.
- Dados da API de processo e do Reportable REST usam os nomes de campo da doc IBM
  (`get_user` → `userUUID, userId, name, emailAddress, archived`; `get_project_area` → `name, project_area_uuid,
  summary, description, cm_enabled, team_areas, timelines, associations`).
- Entradas por UUID/URL, como na doc IBM. Descubra-os com `list_project_areas`, `get_project_area`,
  `get_project_components` (rm) e `get_qm_component` (qm).
- `configuration`/`configuration_url`/`global_configuration_url`/`gc_uri`/`gc_context` vão no header
  `Configuration-Context`. No DOORS Next, sem configuração, é usada a primeira stream do componente.

## Tools (IBM Engineering AI Hub 1.3.0)

| Conjunto | Tools |
|---|---|
| Common | get_user, get_project_area, get_global_configuration, search_global_configuration, list_project_areas, list_linked_requirements, list_linked_workitems, list_linked_testartifacts, link_workitem_and_requirement, link_workitem_and_testartifact, link_testartifact_and_requirement |
| Requirements | get_project_components, get_rm_component_types, list_rm_component_folders, get_rm_component_configuration, get_requirement, search_requirement, create_requirement |
| Work Items | create_workitem, get_workitem, get_workitem_schema, list_workitem_categories, list_workitem_releases, search_workitems, add_comment_to_workitem |
| Test | get_testartifact, get_testartifact_schema, search_testartifact, get_qm_component, get_qm_component_configuration |

Aliases da doc (`get_rm_component`, `get_project_component_types`...) não são registrados: vale o primeiro nome.

## Skills

As skills ficam em `skills/` e documentam, para a IA, as tools, suas entradas e saídas, os fluxos e os erros, para
evitar consultas desnecessárias ao servidor.

| Skill | Assunto |
|---|---|
| alm-setup | Cria/atualiza a configuração do projeto em `alm/pa_<project area em snake_case>.json` (um arquivo por project area do CCM) |
| alm-ccm | Work items: item de trabalho (WI), tarefa, defeito, item de backlog (IB)... |
| alm-rm | Requisitos: RF, RNF, HU, UC, regra, mensagem, especificação... |
| alm-qm | Testes (só leitura): caso de teste (CT), plano (PT), execução (TER)... |
| alm-gc | Comum: usuários, project areas, associações, configuração global e links de rastreabilidade |

Instalação por projeto (links relativos, sem cópia):

```bash
mkdir -p .claude/skills .kiro/skills
for s in alm-setup alm-ccm alm-rm alm-qm alm-gc; do
  ln -sfn ../../skills/$s .claude/skills/$s   # Claude Code
  ln -sfn ../../skills/$s .kiro/skills/$s     # Kiro
done
```

### Tools das skills

Saída com as chaves do alm.json (`alm/pa_*.json`; a skill grava o arquivo, o MCP não o lê). Listas voltam como
`[{name, identifier, ...}]` e a skill grava o mapa `{name: identifier}`.

| Skill | Tools |
|---|---|
| alm-setup (ccm) | whoami, list_project_areas("CCM"), ccm_list_team_areas, ccm_list_members, ccm_list_workitem_types, ccm_list_workitem_fields, ccm_list_iterations, ccm_list_iteration_plans, ccm_create_iteration, ccm_create_iteration_plan |
| alm-setup (rm) | list_project_areas("RM"), get_project_area(include_associations), rm_get_configuration, rm_list_members, rm_list_folders, rm_list_requirement_types |
| alm-ccm | ccm_list_workitems, ccm_list_field_values, ccm_create_workitem, ccm_update_workitem, ccm_list_workitem_states (+ get_workitem, add_comment_to_workitem, link_*) |
| alm-rm | rm_search_requirements, rm_get_requirement, rm_create_requirement, rm_update_requirement (+ link_workitem_and_requirement) |
| alm-qm | search_testartifact, get_testartifact, get_testartifact_schema, get_qm_component, get_qm_component_configuration |
| alm-gc | whoami, get_user, list_project_areas, get_project_area, get_global_configuration, search_global_configuration, list_linked_*, link_* |

Um plano do alm.json vira filtro de `ccm_list_workitems` com `iteration=plans[nome].iteration` e
`team_areas=[plans[nome].owner]`.

## Limites em relação à doc IBM

- `ccm_create_iteration` e `ccm_create_iteration_plan` usam os serviços internos da UI web
  (`IPlanProcessRestService/createIteration` e `IPlanRestService/putItems`), pois o EWM não tem API pública para
  criá-los. Exigem as permissões de processo correspondentes (ex.: "Modify structures of iterations").

- Fora do escopo: Models e Source control; `create_requirement_change_set` e `deliver_requirement_change_set`
  (removidos a pedido); `add_comment_to_testartifact` (o ETM não expõe comentários de revisão
  formal numa API pública).
- `search_workitems`: `filter` só com AND (OR com uma expressão; outras chaves ou lista vazia dão erro), operadores is/equals, is not, in, before/after e
  contains (summary/description). `termExpressions`/`similarityExpressions` não são suportados. Estado e tipo são
  filtrados pelo id (`com.ibm.team.workitem.taskWorkflow.state.s2`, `task`).
- `get_workitem_schema`: `include=approvals` não é suportado.
- `search_testartifact`: `customAttributeFilters`, `categoryFilters` e `linkFilters` não são suportados.
- `rm_search_requirements`: exige ao menos um filtro (`text`, `folder` ou `requirement_type`). O DOORS Next
  responde HTTP 400 quando `text` vem junto com `folder`/`requirement_type`: busque só pelo texto e filtre o
  resultado pelo `type`/`folder`.
- `get_rm_component_types`: `filter_text_only` é redundante. `list_rm_component_folders`: `include_private` é ignorado.
- `get_qm_component`/`get_qm_component_configuration`: dependem da query de componentes do provider de configuração do
  ETM, que pode exigir permissão de administrador (403) e só faz sentido em project areas opt-in.

## Arquitetura

```
mcp_alm/
  server.py   instância MCP + @tool (erros esperados viram ToolError com a mensagem)
  infra/      config, auth (login Jazz), http (sessão, XML, Reportable), oslc (RDF, query, descoberta)
  ibm/        um módulo por conjunto de tools da doc: common, requirements, workitems, test
  ccm.py rm.py qm.py   tools das skills (prefixos ccm_/rm_); compõem ibm/ e infra/
skills/       SKILL.md de alm-setup, alm-ccm, alm-rm, alm-qm e alm-gc
alm/          configuração do projeto (pa_<project area>.json), gravada pela alm-setup
```

Testes: `uv run pytest` (servidor Jazz falso em `tests/conftest.py`).
