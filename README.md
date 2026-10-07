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
| alm-setup (ccm) | whoami, list_project_areas("CCM"), ccm_list_team_areas, ccm_list_members, ccm_list_workitem_types, ccm_list_workitem_fields, ccm_list_link_types, ccm_list_iterations, ccm_list_iteration_plans, ccm_create_iteration, ccm_create_iteration_plan |
| alm-setup (rm) | list_project_areas("RM"), get_project_area(include_associations), rm_get_configuration, rm_list_members, rm_list_folders, rm_list_requirement_types |
| alm-ccm | ccm_list_workitems, ccm_get_workitem, ccm_list_field_values, ccm_create_workitem, ccm_update_workitem, ccm_list_workitem_states (+ add_comment_to_workitem, link_*) |
| alm-rm | rm_search_requirements, rm_count_folder, rm_list_folder, rm_list_modified, rm_sync_plan, rm_download_requirements, rm_upload_requirements, rm_get_requirement, rm_create_requirement, rm_update_requirement (+ link_workitem_and_requirement) |
| alm-qm | search_testartifact, get_testartifact, get_testartifact_schema, get_qm_component, get_qm_component_configuration |
| alm-gc | whoami, get_user, list_project_areas, get_project_area, get_global_configuration, search_global_configuration, list_linked_*, link_* |

### Leitura em Markdown + YAML

`ccm_get_workitem` e `rm_get_requirement` devolvem um documento Markdown com cabeçalho YAML. O MCP não traduz
nomes:

- **Work item:** o cabeçalho traz só os campos e links mapeados no alm.json, com o nome de lá. A skill passa
  `fields=workitem-types[tipo].fields` e `link_types=ccm.link-types` e, para gravar, procura a chave no mesmo mapa
  (`fields["Estimativa"]` → `rtc_cm:estimate`).
- **Requisito:** o cabeçalho traz os links preenchidos, com os nomes do DOORS Next (só leitura: não são gravados), e
  `embedded` (artefatos embutidos no texto). A gravação é só de título e texto. O alm.json do RM guarda só tipos e pastas.

O cabeçalho do requisito segue o [Google OKF v0.2](https://okf.md/): primeiro os campos OKF padrão (`type`,
`title`, `description?`, `resource`, `tags` = [pasta]), depois `sources` (a fonte no DOORS Next: `author` = quem
criou, `last_modified` = última modificação no ALM e `last_modified_by` = quem modificou por último) e `generated` (quem gerou e **quando o documento foi gerado**), ambos em ISO 8601 UTC, e, por
fim, as extensões RM. `sources[0].last_modified` > `generated.at` indica cópia desatualizada; `rm_list_modified`
devolve essa data para um ou vários ids sem ler o conteúdo. Para download/sync de uma pasta, use
`rm_count_folder` (total pelo servidor) e `rm_list_folder` (todos os `{id, title, modified}`, sem teto) como
inventário: só a pasta, sem subpastas; `count` diferente do tamanho da lista indica listagem inconsistente. Para o bundle da alm-sync, o MCP cuida de tudo sem passar listas nem conteúdo pela conversa
(ver [Sincronismo do bundle](#sincronismo-do-bundle-alm-sync)). Extensões RM: `id`, `created` (UTC), `links` e `embedded`, só quando preenchidos. `verified`, `status` e
`stale_after` só aparecem quando o artefato traz um sinal real (requisitos do DOORS Next não os definem).

Exemplo (dados fictícios):

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
embedded:
  - "2001: RN - Validar CPF do cliente"
---
## Fluxo Básico

1. O usuário informa os dados do cliente.
2. O sistema valida o documento: [2001 RN - Validar CPF do cliente](https://alm.example.com/rm/resources/TX_exemplo2001)
```

O corpo é Markdown nos dois sentidos: `text` (RM) e `description` (WI) aceitam Markdown na gravação. No RM, a
leitura sai sempre no mesmo padrão: todo artefato, embutido ou hyperlink, é `[<id> <título>](<alvo>)`, com o alvo =
URL do ALM; o que é embed está na lista `embedded` do cabeçalho (artefato ilegível sai como link e fica fora da
lista). Na gravação, o link cujo artefato está em `embedded` (parâmetro de
`rm_create_requirement`/`rm_update_requirement`) vira embed e o resto, hyperlink; alvo por URL (inclusive link da UI
web com `artifactURI`), id (`2001`) ou arquivo cujo nome começa pelo id. No EWM a descrição só tem texto, `<br/>`, `<b>`, `<i>` e `<a>` (listas viram `• ` / `1. `); para citar outro
WI, escreva "Tarefa 1002" no texto e o EWM cria o link "Menções". `[texto](url)` é hyperlink comum.

### Sincronismo do bundle (alm-sync)

O bundle é uma pasta de md, um por requisito (`<pasta>/<id>-<slug>.md`), com `sync.md` (situação de cada artefato) e
`index.md`. O sincronismo baixa primeiro e depois sobe o que foi editado no md:

1. `rm_sync_plan(folders, dest)`: lista as pastas, regrava `sync.md`, apaga os removidos e devolve um resumo com
   `a_baixar`, `a_subir` e `conflitos`;
2. `rm_download_requirements(dest)` até `restantes = 0`;
3. para cada item de `conflitos`, a skill **pergunta ao usuário qual versão fica**. Se for o ALM, chama
   `rm_download_requirements(dest, requirement_ids=[id])`; se for o md, chama `rm_upload_requirements(dest, requirement_ids=[id])`;
4. `rm_upload_requirements(dest)` até `restantes = 0` (sobe título e corpo e rebaixa o requisito).

| Estado | Quando | Ação |
|---|---|---|
| `novo` | id ainda não baixado | download |
| `sincronizado` | md == ALM | — |
| `desatualizado` | `modified` do ALM ≠ `Última atualização ALM` | download |
| `atualizado` | md alterado (sha256 ≠ `Hash`) e ALM sem mudança | upload |
| `normalizado` | md **não** alterado (hash igual), mas o ALM foge da regra embed/link do bundle (diferença invisível no md) | upload |
| `conflito` | md alterado **e** ALM mudou | perguntar ao usuário: md ou ALM |
| `erro: <msg>` | falha no download | download de novo |

Mudar de pasta é tratado como remoção na antiga e criação na nova: o arquivo é apagado e a linha volta a `novo`
(com edição local no md, vira `conflito` e o arquivo fica).

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
| 125 — Sessão | 01-Requisitos |  |  | novo |
```

`Última atualização ALM` é o `modified` do ALM no último download/upload, e `Hash` é o sha256 completo do arquivo
gravado (abreviado no exemplo).

No corpo do md do bundle vale uma regra fixa: **artefato do bundle (id no `sync.md`) é sempre embed; o resto é
sempre link**. Por isso o md do bundle não tem `embedded`, e o `links:` do cabeçalho é só de leitura. O download
grava o artefato do bundle como `[id título](../03-Regras/2001-x.md)` e o de fora como `[id título](URL do ALM)`.
Se o ALM estiver diferente da regra (um embed de fora ou um hyperlink para o bundle), a linha fica `normalizado` e o
upload corrige. Formatos sugeridos para escrever no md:

| Referência | Formato |
|---|---|
| embed (artefato do bundle) | `[<id>](<id>)` |
| link para artefato de outra PA | `[<id>](<URL do ALM>)` |
| link externo | `[<texto descritivo>](<url>)` |

Um plano do alm.json vira filtro de `ccm_list_workitems` com `iteration=iterations[it].identifier` e
`team_areas=[iterations[it].plans[nome].team-area]` (plano sem `team-area`: omita `team_areas`).

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
- `rm_update_requirement`: `text` substitui o texto inteiro; atributos e links não são gravados. `ccm_update_workitem`: `description` substitui a descrição inteira.
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
  infra/      config, auth (login Jazz), http (sessão, XML, Reportable), oslc (RDF, query, descoberta),
              document (leitura em Markdown + YAML)
  ibm/        um módulo por conjunto de tools da doc: common, requirements, workitems, test
  ccm.py rm.py qm.py   tools das skills (prefixos ccm_/rm_); compõem ibm/ e infra/
skills/       SKILL.md de alm-setup, alm-ccm, alm-rm, alm-qm e alm-gc
alm/          configuração do projeto (pa_<project area>.json), gravada pela alm-setup
```

Testes: `uv run pytest` (servidor Jazz falso em `tests/conftest.py`).
