# Manual do mcp-elm 2.0

Instalação, credenciais e a lista das tools: [README](../README.md). Aqui, o que cada operação faz por dentro.

## O arquivo pa

`<projeto>/.kiro/config/sdd/pa_<nome>.json`, validado pelo
[schema do power sdd](../../../powers/sdd/skills/sdd-setup/assets/pa.schema.json) a cada leitura (cache pelo mtime:
a `sdd-setup` pode regravar com o MCP no ar). Campo ausente ou inválido → erro `config` com o caminho
(`ccm.horas.time-code`). Vários arquivos sem `pa` → erro `entrada` com os nomes.

| Seção | Usada em |
|---|---|
| `ccm.id`, `ccm.time` | consultas, categoria (`filedAgainst`) de todo work item criado, dono dos planos |
| `ccm.tipos.<ib\|defeito\|tarefa>` | `id` do tipo; `campos` (nome → attribute); `padroes`; `por-prefixo` |
| `ccm.links` | pai, filhos, implementa, relacionado (com padrão); depende (só se a PA tiver) |
| `ccm.iteracoes` | nome → id e planos; `elm_iteracao` grava aqui (indent=2, ordem mantida) |
| `ccm.horas` | time code e work type dos lançamentos |
| `rm` | PA, stream (`configuracao`), `artefatos` (sigla → tipo e pasta), `download` (bundle) |
| `qm` | PA do ETM; sem ela, `elm_criar_ct` e os links com `ct` dão erro `config` |

## Valores de campo

`campos` e `padroes` vêm pelo **nome do pa** e o valor pelo **título**, como a UI mostra. O servidor traduz:

- enumeração: título (sem diferenciar caixa) → URL do literal; título desconhecido → `entrada` com os válidos;
- `filedAgainst`: só a categoria do time do pa (nome ou id);
- `plannedFor`: nome do cache `ccm.iteracoes`, nome no servidor ou itemId;
- pessoas: login ou `eu` (o login do `elm.properties`, como o servidor o grava);
- durações (`estimate`...): horas → ms;
- texto rico (`rdf:XMLLiteral`): Markdown → HTML do EWM.

Na criação a ordem é `padroes` do tipo → `por-prefixo` cujo prefixo inicia o título → `campos` da chamada. Campo
obrigatório do shape (não somente leitura, sem valor padrão) que continua vazio → `precondicao` com o nome do
campo, antes de chamar o servidor. `valores_usados` devolve o que foi aplicado.

## Transições

`elm_transicao` lê o workflow do estado atual (estados e ações no formato OSLC CM 1.0, com o grupo
`open`/`inprogress`/`closed` de cada estado; sem o grupo, ele sai do nome) e percorre as ações até um estado do
grupo-alvo. O workflow não diz de onde cada ação parte e o servidor aceita em silêncio uma ação que não sai do
estado atual: tenta primeiro as que levam ao alvo e, para concluir, as que passam por em andamento.

| alvo | grupo | regras |
|---|---|---|
| `assumir` | em andamento | dono vira o login da sessão; dono outro → `conflito` (campo `dono`, detalhe = login) sem `forcar`; já concluída → `precondicao` (campo `estado`); já minha e em andamento → `ja_estava`. `iniciar_pai`: pai no estado inicial vai para em andamento (o dono dele não muda; sem dono → `precondicao`, campo `dono do pai`, conferido antes de mexer na tarefa) |
| `concluir` | final | passa por em andamento se preciso; recusa do servidor → `precondicao` com o atributo |
| `reabrir` | inicial | |

## Horas e anexos

Pelos serviços internos da UI web (`workItemDTO2`/`workItem2`), como o 1.0.43. `elm_lancar_horas` aceita só o dono
do work item, até 16 h por dia somando o que a pessoa já lançou no dia, sem data futura; devolve o lançado e o total,
não o histórico. `elm_remover_horas(id, data)` remove os lançamentos de quem chama naquela data (`removeLink` do
link de timesheet) e devolve o novo total. `elm_horas(iteracao)` soma por tarefa e pessoa; tarefa sem lançamento aparece com 0 h (para o
relatório de concluídas sem horas). Anexos: `caminho` é relativo ao projeto; enviar substitui anexo de mesmo nome.

## Requisitos e bundle

`elm_ler_req` converte o texto para Markdown; todo requisito citado (link ou embed) vira `[id título](URL)`.
`elm_publicar_req`: `[x](<id>)` cita outro requisito; os ids em `embutir` viram embed e o resto, hyperlink. Sem
`embutir`, a atualização mantém como embed o que já é embed no ELM. Com `modificado_em` e o ELM mais novo →
`conflito` com as duas datas.

`elm_sync` mantém o bundle OKF em `<projeto>/<rm.download>` (mesmo formato do 1.0.43: `sync.md`, `index.md` por
pasta, `log.md`, um `.md` por requisito). `plano` lista as pastas do pa e classifica: novo, sincronizado,
desatualizado, atualizado, normalizado, conflito. `baixar` sem ids leva a fila (novo, desatualizado, erro); com ids,
fica a versão do ELM. `enviar` sem ids sobe atualizado/normalizado só se o ELM não mudou desde o download (senão
conflito); com ids, fica a versão do md. `baixar` termina reindexando o bundle local (abaixo); se passar de ~40 s,
devolve `indexando: true, restantes: n`.

### Bundle local

Quatro tools leem o bundle baixado, sem ir ao ELM — mesmo nome da operação remota com o sufixo `_local`:

| Tool | Faz | `dados` |
|---|---|---|
| `elm_buscar_req_local` | busca híbrida (sentido + palavra), filtros `pasta` (1ª pasta do path) e `tipo` (`type` do frontmatter) | `itens[{path, id, titulo, tipo, pasta, score, trechos[{secao, texto}]}], indexando?, restantes?` |
| `elm_ler_req_local` | documento por id ou path relativo, com links de saída e de entrada | `path, markdown, links{saida[], entrada[]}` |
| `elm_listar_req_local` | inventário (sem corpo), mesmos filtros | `itens[{path, id, titulo, tipo, pasta, descricao, indexado}]` |
| `elm_indexar_local` | indexa o que mudou (sha256), até ~40 s por chamada; `forcar` reindexa tudo | `documentos, indexados, removidos, restantes` |

O índice fica em `<bundle>/sqlite3/okf.db` (com `.gitignore`): chunks por heading com vetor do
`paraphrase-multilingual-MiniLM-L12-v2` (fastembed, carregado só na 1ª busca/indexação) + FTS5, fundidos por RRF.
`elm_buscar_req_local` reindexa antes, incrementalmente; se o tempo acabar, busca com o que já está indexado e devolve
`indexando: true, restantes: n`. `elm_indexar_local` é para a 1ª indexação de um bundle grande: chame até
`restantes` = 0.

**Nada baixado** (pasta de `rm.download` ausente ou sem `.md`): buscar, ler, listar e indexar devolvem
`nao_encontrado` com `campo: "bundle"` e o detalhe indica baixar com `elm_sync` (`direcao: "baixar"`) ou pesquisar no
ELM remoto com `elm_buscar_req`/`elm_ler_req`. Sem `rm.download` no pa: `config`.

## Iteração e planos

`elm_iteracao(nome)` acha a iteração pelo nome; se não existe, cria irmã da última iteração do cache do pa
(precisa de `inicio`). Garante os planos do time — `Backlog <time>` (backlog do produto) e `<nome> - <time>` (plano
da sprint) — e grava `ccm.iteracoes[nome]`. Criar exige a permissão de modificar iterações e planos.

## Descoberta

`elm_setup_descobrir(ccm, rm?, qm?, valores_de?)` acha a PA pelo trecho do nome (várias → só a lista) e devolve o que
a `sdd-setup` precisa: times com categorias; só os tipos do fluxo (story, defect, task, epic; os outros em
`outros_tipos`, nome e id) com campos (título → attribute), obrigatórios, `enumeracoes` (nomes) e os valores só das
obrigatórias e das pedidas em `valores_de`; os tipos de link do fluxo; as 6 iterações mais recentes com planos
(`iteracoes_total`); no DOORS Next componente, stream, pastas e tipos; no ETM a PA. A PA também exige campos por
regra de processo (o 403 "needs to be set"), que o shape não marca: peça os valores deles por `valores_de`.

## Paridade com o 1.0.43

```bash
uv run python paridade/paridade.py <projeto> --pa <nome> --wi <id> --req <id> [--iteracao <nome>] \
  [--pa-antigo <elm.json>] [--escrita --wi-teste <id>]
```

Extrai o 1.0.43 do git para um diretório temporário, roda as operações equivalentes nele (outro processo) e no 2.0
(este processo, pelo MCP) e imprime, por operação, igual/diferente nos campos essenciais e o tamanho da resposta
antigo × novo. Leituras: ler WI, horas do WI, listar da iteração, ler e buscar requisito, setup, sync plano (sobre
cópias do bundle). `--escrita` roda comentar e atualizar a descrição no `--wi-teste`, perguntando "sim" antes de
cada uma.
