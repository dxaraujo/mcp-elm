# Manual do mcp-elm 3.0

Instalação, credenciais e a lista das tools: [README](../README.md). Aqui, o que cada operação faz por dentro.

## O arquivo pa

`<projeto>/.kiro/config/elm/pa_<nome>.json`, validado pelo
[pa.schema.json](https://github.com/dxaraujo/mcps/blob/main/elm/pa.schema.json) a cada leitura. Campo ausente ou inválido → erro `config` com o caminho
(`ccm.horas.time-code`). Vários arquivos sem `pa` → erro `entrada` com os nomes.

| Seção | Usada em |
|---|---|
| `ccm.id`, `ccm.time` | consultas, categoria (`filedAgainst`) de todo work item criado, dono dos planos |
| `ccm.tipos.<ib\|defeito\|tarefa>` | `id` do tipo; `campos` (nome → attribute); `padroes`; `por-prefixo` |
| `ccm.links` | pai, filhos, implementa, relacionado (com padrão); depende (só se a PA tiver) |
| `ccm.iteracoes` | nome ou caminho (`2026/Sprint 1`) → id e planos; `elm_iteracao` grava aqui (indent=2, ordem mantida) |
| `ccm.horas` | time code e work type dos lançamentos |
| `rm` | PA, stream (`configuracao`), `artefatos` (sigla → tipo e pasta), `download` (bundle) |
| `qm` | PA do ETM; sem ela, `elm_criar_ct` e os links com `ct` dão erro `config` |

## Valores de campo

`campos` e `padroes` vêm pelo **nome do pa** e o valor pelo **título**, como a UI mostra. O servidor traduz:

- enumeração: título (sem diferenciar caixa) → URL do literal; título desconhecido → `entrada` com os válidos;
- `filedAgainst`: só a categoria do time do pa (nome ou id);
- `plannedFor`: `backlog` (o `ccm.backlog` do pa; sem ele, `precondicao`), nome do cache `ccm.iteracoes`, nome no
  servidor ou itemId;
- pessoas: login ou `eu` (o login do `elm.properties`, como o servidor o grava);
- durações (`estimate`...): horas → ms;
- texto rico (`rdf:XMLLiteral`): Markdown → HTML do EWM.

Na criação a ordem é `padroes` do tipo → `por-prefixo` cujo prefixo inicia o título → `campos` da chamada. Campo
obrigatório do shape (não somente leitura, sem valor padrão) que continua vazio → `precondicao` com o nome do
campo, antes de chamar o servidor. `valores_usados` devolve o que foi aplicado.

## Transições

`elm_transicao` lê o workflow do estado atual (estados e ações no formato OSLC CM 1.0, com o grupo
`open`/`inprogress`/`closed` de cada estado; sem o grupo, ele sai do nome) e percorre as ações até um estado do
grupo-alvo (para cancelar, um estado de descarte do grupo final). O workflow não diz de onde cada ação parte e o
servidor aceita em silêncio uma ação que não sai do estado atual: tenta primeiro as que levam ao alvo e, para
concluir, as que passam por em andamento.

| alvo | grupo | regras |
|---|---|---|
| `assumir` | em andamento | dono vira o login da sessão; dono outro → `conflito` (campo `dono`, detalhe = login) sem `forcar`; já concluída → `precondicao` (campo `estado`); já minha e em andamento → `ja_estava`. `iniciar_pai`: pai no estado inicial vai para em andamento (o dono dele não muda; sem dono → `precondicao`, campo `dono do pai`, conferido antes de mexer na tarefa) |
| `concluir` | final | passa por em andamento se preciso; recusa do servidor → `precondicao` com o atributo |
| `reabrir` | inicial | |
| `cancelar` | descarte | só ações que levam a um estado de descarte (Cancelado, Inválido, Rejeitado, Duplicado, Descartado); já em descarte → `ja_estava`; concluída → `precondicao` (`já concluída: reabra antes`, campo `estado`); workflow sem estado de descarte → `precondicao` (`o workflow não tem cancelamento`) |

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
conflito); com ids, fica a versão do md. Indexar é à parte (`elm_indexar_local`, abaixo): quem baixa decide se
indexa agora.

### Bundle local

Cinco tools leem o bundle baixado, sem ir ao ELM — mesmo nome da operação remota com o sufixo `_local`:

| Tool | Faz | `dados` |
|---|---|---|
| `elm_buscar_req_local` | busca híbrida (sentido + palavra), filtros `pasta` (1ª pasta do path) e `tipo` (`type` do frontmatter) | `itens[{path, id, titulo, tipo, pasta, score, trechos[{secao, texto}]}], indexando?, restantes?, erro_rerank?` |
| `elm_ler_req_local` | documento por id ou path relativo, com links de saída e de entrada | `path, markdown, links{saida[], entrada[]}` |
| `elm_listar_req_local` | inventário (sem corpo), mesmos filtros | `itens[{path, id, titulo, tipo, pasta, descricao, indexado}]` |
| `elm_indexar_local` | indexa o que mudou (sha256) numa thread e responde na hora; chamar de novo mostra o progresso; `forcar` reindexa tudo | `documentos, restantes, indexando, erro_indice?` |
| `elm_explorar_local` | gera o `explorar.html` (visualizador offline do bundle em galáxias) na pasta do bundle | `path, documentos, chunks, galaxias, links, gerado_em` |

O índice fica em `<bundle>/sqlite3/okf.db` (com `.gitignore`): chunks por heading e parágrafo — parágrafo maior
que ~1.500 chars quebra por linha (lista longa não vira um único vetor diluído), e tabela markdown repete
header + separador em cada parte — com vetor denso normalizado + FTS5, fundidos por RRF (cosseno + BM25). O vetor
vem da API OpenAI-compatível do `elm.properties`: `provider_url` + `provider_api_key` são obrigatórios para a
busca semântica (sem eles, indexar e buscar falham com erro `config` apontando as chaves — não existe modelo
local) e o modelo fica em `provider_embed_model` (padrão `text-embedding-3-small`). A assinatura do embedder
(base + modelo) e a versão do chunking (meta `chunking`) ficam na meta do índice: mudou o embedder (a dimensão
dos vetores muda), o formato do chunk, ou o índice nasceu sem proveniência (meta sem o embedder — o caso das
versões anteriores), o índice é limpo e refeito, porque vetores de modelos diferentes não podem coexistir na
matriz e o texto dos chunks antigos não reflete o formato novo. E a busca num índice de outro embedder falha com
erro `config` apontando o `elm_indexar_local` antes de gastar a API da query: cossinos de espaços diferentes
são inválidos, e a dimensão mista (o matmul) vira o mesmo erro, nunca traceback. Trocar as chaves do provider
exige reiniciar o servidor MCP (a config fica em cache) e reindexar o bundle.

Com `provider_rerank_model` no `elm.properties` (mesma base e key), a busca é em dois estágios: o RRF larga um top largo
de candidatos e o rerank (cross-encoder por API) reordena — a score do rerank substitui a do RRF em
`itens[].score`. Cada chunk vai truncado (~8k chars: margem de segurança no contexto de 8192 tokens do modelo),
e o rerank é refinamento opcional: se a API dele falhar, a busca segue na ordem RRF e devolve `erro_rerank` em
`dados` (no lugar de a busca inteira falhar). `elm_status` mostra `embedder` e `rerank` sempre (`null` sem
provider / sem rerank configurado); falhas da
API de embedding/rerank viram o erro do contrato correspondente (`credencial` 401/403, `config` 404,
`indisponivel` 429/5xx/falha de rede transitória, com 3 tentativas) — na indexação, `erro_indice` em `dados`.

Depois do 2º estágio (ou do RRF, sem rerank ou na falha dele), o type-boost ajusta o pool inteiro antes do
slicing: cada chunk multiplica a score pelo fator do `type` exato do frontmatter — Caso de Uso ×1,03 >
História de Usuário ×1,02 > Regra ×1,01 > demais ×1,00 ("Regra de Negócio" não é "Regra"). Tipos de negócio
ficam acima de chunks de relevância equivalente, e `itens[].score` é o valor ajustado.

A indexação roda numa thread do servidor (o 1º uso percorre o bundle pela API de embedding, que leva minutos):
nenhuma chamada espera. `elm_buscar_req_local` com arquivos alterados dispara a indexação e busca no que já está
no índice, devolvendo `indexando: true, restantes: n`. Com o provider, cada documento indexado vai numa chamada
única (todas as partes dele no mesmo lote) — o reindex do bundle inteiro é uma chamada por documento, com
retomada incremental: falhou no meio, o `elm_indexar_local` de novo embute só o que diverge (hash + meta).

**Visualizador** (`elm_explorar_local`): gera `<bundle>/explorar.html` — HTML único que abre o bundle no
navegador sem internet (intranet): galáxias de documentos (planeta = documento, lua = chunk do planeta
selecionado) na vista 3D (3d-force-graph/three.js) ou 2D (Cytoscape.js), com busca, filtros por
tipo/pasta/galáxia e Markdown (marked + DOMPurify). As quatro libs ficam em `assets/` e vão embutidas no
HTML no gerar. Documentos, vetores e chunks saem do índice em modo somente leitura (nada de reindexação
aqui): sem índice, `precondicao` com `campo: "indice"` apontando o `elm_indexar_local`. O agrupamento é
semântico, não por pasta: agregativo, linkage média, distância de cosseno entre a média normalizada dos
vetores dos chunks do documento, funde enquanto a distância for menor que 0.30. Nota: o asset do
3d-force-graph é o 1.73.4 e, no embutir, recebe o backport do fix oficial upstream
(vasturiano/three-forcegraph@`f89a2000ee`, "Prevent layoutTick from running before layout is assigned") —
sem ele, o 1º `graphData` faz `state.layout.tick()` num layout ainda não atribuído e a vista 3D nasce em
branco (o `RuntimeError` do `_ler_asset` trava o gerar se o asset trocar de versão).

**Nada baixado** (pasta de `rm.download` ausente ou sem `.md`): buscar, ler, listar, indexar e explorar devolvem
`nao_encontrado` com `campo: "bundle"` e o detalhe indica baixar com `elm_sync` (`direcao: "baixar"`) ou pesquisar no
ELM remoto com `elm_buscar_req`/`elm_ler_req`. Sem `rm.download` no pa: `config`.

## Iteração e planos

`elm_iteracao(nome)` acha a iteração da sprint pelo nome; se não existe, cria irmã da última iteração do cache do pa
(precisa de `inicio`). Garante o plano da sprint do time (`<nome> - <time>`) e grava `ccm.iteracoes[nome]`.
`nome` pode ser o **caminho** `<pai>/<sprint>` (`2026/Sprint 1`): a sprint é procurada e criada dentro do pai — duas
"Sprint 1" em pais diferentes deixam de ser ambíguas —; pai que não existe nasce irmão do pai da última sprint do cache
(`2027` ao lado de `2026`, na mesma release). O plano fica `<pai> <sprint> - <time>` e a chave do cache é o caminho.
Devolve também `pai` (o nome). O caminho vale em toda tool que recebe `iteracao`.

`elm_backlog` cuida do **backlog do produto**, onde os itens nascem antes de entrar numa sprint — um só para o
projeto, que pode servir a várias entregas. Sem `plano` nem `nome`, devolve o `atual` (o `ccm.backlog` do pa) e os
`candidatos` (planos de backlog da PA, com a iteração). `plano` (itemId ou o link do plano na UI web, com `id=_…`)
usa um existente. `nome` acha a iteração ou a cria — filha de `pai` ou, sem ele, no topo da timeline das sprints — e
garante o plano `Backlog <time>`. Nos dois casos grava `ccm.backlog {nome, id, plano}`; daí em diante
`iteracao: "backlog"` em `elm_criar_wi`/`elm_atualizar_wi`/`elm_listar_wi` é essa iteração, e entrar na sprint é
`elm_atualizar_wi(ids, iteracao = a da sprint)`. Criar exige a permissão de modificar iterações e planos.

## Descoberta

`elm_setup_descobrir(ccm, rm?, qm?, valores_de?)` acha a PA pelo trecho do nome (várias → só a lista) e devolve o que
`outros_tipos`, nome e id) com campos (título → attribute), obrigatórios, `enumeracoes` (nomes) e os valores só das
obrigatórias e das pedidas em `valores_de`; os tipos de link do fluxo; as 6 iterações mais recentes com planos
(`iteracoes_total`); no DOORS Next componente, stream, pastas e tipos; no ETM a PA. A PA também exige campos por
regra de processo (o 403 "needs to be set"), que o shape não marca: peça os valores deles por `valores_de`.
