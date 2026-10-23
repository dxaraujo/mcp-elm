# mcp-elm

Servidor MCP do IBM ELM — **EWM** (work items), **DOORS Next** (requisitos) e **ETM** (casos de teste) — para o
power `sdd`. Expõe **operações**: o servidor lê a configuração da project area no projeto (`pa_<nome>.json`) e a
LLM passa só ids e conteúdo. Manual: [docs/manual-mcp-elm.md](docs/manual-mcp-elm.md).

## Instalação

```bash
uv sync
claude mcp add elm -- uv run --directory /caminho/para/mcps/elm mcp-elm
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
```

## Configuração do projeto

`<projeto>/.kiro/config/sdd/pa_<nome>.json`, um por project area, gravado pela skill `sdd-setup`. O schema é o do
power sdd ([pa.schema.json](../../powers/sdd/skills/sdd-setup/assets/pa.schema.json)); o pacote leva uma cópia
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
`credencial`, `nao_encontrado`, `permissao`, `precondicao`, `conflito`, `entrada`.

## Tools

| Tool | Faz | `dados` |
|---|---|---|
| `elm_status` | confere conexão e pa | `login, servidor, pas[], valido, faltando[]` |
| `elm_ler_wi` | lê um work item | Markdown |
| `elm_listar_wi` | lista por iteração, dono, tipo, pai, prefixo, estado | `itens[], truncado, total` |
| `elm_criar_wi` | cria com padrões do pa | `id, url, valores_usados` |
| `elm_atualizar_wi` | atualiza um ou vários | `atualizados[], falhas[]` |
| `elm_transicao` | assumir · concluir · reabrir | `id, estado, dono, ja_estava, pai?` |
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
| `elm_iteracao` | acha ou cria iteração e planos do time | `id, planos, criados[]` |
| `elm_sync` | bundle do DOORS Next (plano, baixar, enviar) | `baixar[], enviar[], conflitos[]` · `feitos[], conflitos[]` |
| `elm_setup_descobrir` | dados para montar o pa | `ccm, rm?, qm?, valores_de?` |

## Testes e paridade

```bash
uv run --with pytest python -m pytest -q
uv run python paridade/paridade.py <projeto> --pa <nome> --wi <id> --req <id> [--iteracao <nome>]
```

A paridade compara as leituras com o 1.0.43 (commit `a73e925`) contra o ELM real; veja o manual.
