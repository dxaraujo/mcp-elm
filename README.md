# mcp-elm

Servidor MCP do IBM ELM — **EWM** (work items), **DOORS Next** (requisitos) e **ETM** (casos de teste). Expõe **operações**: o servidor lê a configuração da project area no projeto (`pa_<nome>.json`) e a
LLM passa só ids e conteúdo. Manual: [docs/manual-mcp-elm.md](docs/manual-mcp-elm.md).

## Instalação

Publicado no [PyPI](https://pypi.org/project/mcp-elm/); requer o
[uv](https://docs.astral.sh/uv/). Em outro cliente MCP:

```json
{
  "mcpServers": {
    "elm": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-elm@latest"],
      "env": { "UV_SYSTEM_CERTS": "true" }
    }
  }
}
```

Desenvolvimento:

```bash
uv sync
uv run mcp dev mcp_elm/server.py   # depuração com o MCP Inspector
```

## Credenciais

`~/.config/mcp-elm/elm.properties` (Windows: `%APPDATA%\mcp-elm\elm.properties`; outro caminho: `MCP_ELM_CONFIG`).
CA próprio: `export REQUESTS_CA_BUNDLE=/caminho/ca.pem`. Login pelo form Jazz, com Basic se o servidor pedir.

```ini
[DEFAULT]
server = https://elm.SEU-SERVIDOR
user = SEU_USUARIO
password = SUA_SENHA
# busca semântica do bundle local por API OpenAI-compatível (a mesma base e key para os dois);
# provider_url/provider_api_key são obrigatórios para a busca semântica e a indexação do bundle local (não há modelo local)
# provider_url = https://sua-api/v1
# provider_api_key = SUA_KEY
# provider_embed_model = text-embedding-3-small   # trocar exige reindexar (elm_indexar_local)
# provider_rerank_model = SEU_MODELO_RERANK       # opcional: rerank (cross-encoder) na busca
```

## Configuração do projeto

`<projeto>/.sabia/config/elm/pa_<nome>.json`, um por project area, gravado pela skill `sa-setup`. Projetos anteriores
continuam lidos: o MCP procura em `.sabia/config/elm/`, depois `.kiro/config/elm/`, depois `.kiro/config/sdd/`; o mesmo
arquivo em mais de uma pasta vale o da primeira. Toda gravação (ex.: `elm_iteracao`) vai para `.sabia/config/elm/`. O schema é o do
Sabiá ([pa.schema.json](https://dxaraujo.github.io/mcps/elm/pa.schema.json)); o pacote leva uma cópia
(`mcp_elm/pa.schema.json`) — mantenha as duas iguais.

Toda tool (menos `elm_setup_descobrir`) recebe `projeto` (caminho **absoluto** do workspace: o Kiro não inicia o MCP
de um power no workspace) e `pa` (o `<nome>`; omitido só quando há um arquivo).

## Resposta

Um único TextContent, sem `structuredContent`:

```json
{"ok":true,"dados":{...}}
{"ok":false,"erro":{"codigo":"precondicao","detalhe":"...","campo":"Estimativa"}}
```

`elm_ler_wi` e `elm_ler_req` devolvem Markdown (YAML + corpo); erro nelas sai no envelope. Códigos: `config`,
`credencial`, `indisponivel` (rede ou timeout: tente de novo), `nao_encontrado`, `permissao`, `precondicao`, `conflito`, `entrada`.

## Tools

| Tool | Faz | `dados` |
|---|---|---|
| `elm_status` | confere conexão e pa | `login, servidor, pas[], valido, faltando[], embedder?, rerank?` |
| `elm_ler_wi` | lê um work item | Markdown |
| `elm_listar_wi` | lista por iteração, dono, tipo, pai, prefixo, estado | `itens[], truncado, total` |
| `elm_criar_wi` | cria com padrões do pa | `id, url, valores_usados` |
| `elm_atualizar_wi` | atualiza um ou vários | `atualizados[], falhas[]` |
| `elm_transicao` | assumir · concluir · reabrir · cancelar | `id, estado, dono, ja_estava, pai?` |
| `elm_comentar` | comenta | `id` |
| `elm_anexo` | listar · enviar · baixar | `anexos[]` · `nome` · `caminho` |
| `elm_horas` | horas do WI ou da iteração | `lancamentos[], total_h` · `linhas[], total_h` |
| `elm_lancar_horas` | lança horas (só o dono) | `lancado, total_h` |
| `elm_remover_horas` | remove os seus lançamentos numa data | `id, data?` |
| `elm_ligar` | implementa, rastreia, afeta, testado por, valida, depende, relacionado | `ja_existia` |
| `elm_buscar_req` | busca requisitos por texto | `itens[], truncado` |
| `elm_ler_req` | lê um requisito | Markdown |
| `elm_publicar_req` | cria ou atualiza requisito | `id, url, acao` |
| `elm_criar_ct` | cria caso de teste já ligado | `id, url` |
| `elm_iteracao` | acha ou cria a iteração da sprint e o plano dela (`plano`, só ao criar; padrão: o nome da sprint; caminho `<pai>/<sprint>` relativo à linha de base; o pai nasce se faltar) | `id, pai, planos, criados[]` |
| `elm_backlog` | lista, usa ou cria o backlog do produto (padrão: iteração `<linha de base>/Backlog` com o plano `Backlog`) e grava no pa | `atual, candidatos[]` ou `nome, id, plano, criados[]` |
| `elm_sync` | bundle do DOORS Next (plano, baixar, enviar) | `baixar[], enviar[], conflitos[]` · `feitos[], conflitos[]` |
| `elm_buscar_req_local` | busca no bundle baixado (sentido + palavra, rerank opcional, type-boost e piso de título no ranking) | `itens[], indexando?, restantes?, erro_rerank?` |
| `elm_ler_req_local` | lê documento do bundle (id ou path) com links | `path, markdown, links` |
| `elm_listar_req_local` | inventário do bundle | `itens[]` |
| `elm_indexar_local` | indexa o bundle em segundo plano; responde na hora com o progresso | `documentos, restantes, indexando` |
| `elm_explorar_local` | gera o `explorar.html` (grafo 3D do bundle) na pasta do bundle | `path, documentos, chunks, links, semanticos, gerado_em` |
| `elm_setup_descobrir` | dados para montar o pa | `ccm, rm?, qm?, valores_de?` |

**Estrutura padrão no EWM.** Cada sprint é uma iteração com o seu plano; o backlog é uma iteração irmã dos pais:

```
<linha de base>          (a iteração do projeto na timeline, sem datas)
├── Backlog              plano "Backlog" (product backlog) — ccm.backlog do pa
├── 2026                 iteração pai (nome livre)
│   ├── Sprint 01        plano "Sprint 01 - <meta>" (parâmetro plano; padrão: o nome da sprint)
│   └── Sprint 02
└── 2027
```

A linha de base é o pai da iteração do `ccm.backlog`; o `elm_iteracao` e toda tool que recebe `iteracao` leem o
caminho `<pai>/<sprint>` a partir dela (cache do pa primeiro; espaços nas pontas são ignorados, parte vazia é
erro). Duas iterações com o mesmo nome no mesmo pai são erro `entrada`: o MCP nunca escolhe nem cria uma terceira. Dois planos na mesma iteração mostram os mesmos itens (o plano é a consulta do
"Planejado para" da iteração), por isso a sprint é uma iteração e não um plano a mais no pai.

As `_local` leem o bundle em `<projeto>/<rm.download>` (índice em `<bundle>/sqlite3/okf.db`); depois do
`elm_sync baixar`, indexe com `elm_indexar_local`. O vetor vem da API OpenAI-compatível do `elm.properties`
(`provider_url` + `provider_api_key` obrigatórios para a busca; rerank opcional em `provider_rerank_model`). Trocar
as chaves do provider exige reiniciar o servidor MCP (a config fica em cache) e reindexar o bundle
(`elm_indexar_local`): vetores de outro modelo não coexistem no índice. `elm_explorar_local`
gera um `explorar.html` autocontido na pasta do bundle: grafo 3D simples (3d-force-graph/three.js,
nó = documento, cor por tipo; libs do CDN jsDelivr); o combobox do header alterna as arestas entre os links dos
artefatos e as semânticas (vizinhos por cosseno dos embeddings do índice).
Sem nada baixado devolvem `nao_encontrado` com `campo: "bundle"`: baixe com `elm_sync` ou pesquise no
ELM remoto (`elm_buscar_req`/`elm_ler_req`). Detalhes no manual.

## Testes

```bash
uv run --with pytest python -m pytest -q
```
