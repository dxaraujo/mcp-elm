"""Documento Markdown + YAML das tools de leitura (rm_get_requirement, ccm_get_workitem).

Cabeçalho YAML com os campos, corpo em Markdown. Na leitura, artefato embutido no texto do DOORS Next vira
`![id título](alvo)` e hyperlink para artefato vira `[id título](alvo)` (alvo = URL do ALM ou caminho do arquivo);
na gravação, o `!` volta a ser embed e o resto, hyperlink. A gravação também aceita o legado `![[id]]` e alvos por
id (`2001`) ou por arquivo (`../03-Regras/2001-x.md`).
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from html import escape, unescape
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import unquote

from markdown_it import MarkdownIt

# ponytail: fuso fixo de Brasília (como a UI mostra); tornar configurável se houver servidor em outro fuso
BRT = timezone(timedelta(hours=-3))
# ![[id]] ou ![[id: título]] (legado, aceito na gravação)
EMBED = re.compile(r"!\[\[(\d+)(?::[^\]]*)?\]\]")
# alvo de link com espaço sem <...> ('[2001 REG Nome](2001 REG Nome.md)'): o CommonMark não o reconhece
SPACED_TARGET = re.compile(r"\]\(([^()<>\"\n]*\s[^()<>\"\n]*)\)")
# 'id' ou 'id<separador>...' no último segmento do alvo (arquivo do bundle ou id puro)
ARTIFACT_FILE = re.compile(r"(\d+)(?:[ _:-].*)?")
# (rótulo, alvo) de um artefato do RM pela URL; None se a URL não for de artefato
Artifact = Callable[[str], "tuple[str, str] | None"]
# como o editor do DOORS Next grava um artefato embutido
EMBED_HTML = ('<span class="com-ibm-rdm-editor-EmbeddedResourceDecorator minimised">'
              '<a class="embedded" href="{url}"> </a></span>')
_PLAIN = re.compile(r"[^\W\d][\w .,/()-]*")
_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "y", "n"}


def local_datetime(value) -> str | None:
    """'2024-05-09 18:29:09+00:00' -> '2024-05-09 15:29' (Brasília)."""
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(BRT).strftime("%Y-%m-%d %H:%M")


def utc_datetime(value) -> str | None:
    """ISO 8601 em UTC com offset explícito, precisão de segundos: '2024-05-09T18:29:09Z' (OKF trust/lifecycle).

    Aceita string ISO (com 'Z' ou offset), datetime (naïve é assumido UTC) ou None; devolve None para valor falso.
    Diferente de `local_datetime`, que mantém `created`/`modified` em Brasília."""
    if not value:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def one_line(text: str | None) -> str | None:
    """Primeira linha, sem espaços repetidos nem espaço não separável (títulos do DOORS Next, commits)."""
    return " ".join(next((l for l in text.splitlines() if l.strip()), "").split()) if text else text


def slug(text: str) -> str:
    """Título -> nome de arquivo: sem acentos, minúsculas, '-' no lugar do resto, até 60 caracteres."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:60].rstrip("-")


def md_link(label: str, target: str, embed: bool = False) -> str:
    """[rótulo](alvo) ou ![rótulo](alvo); alvo com espaço ou parêntese vai entre <...>."""
    label = re.sub(r"([\[\]])", r"\\\1", label)
    target = f"<{target}>" if re.search(r"[\s()]", target) else target
    return f"{'!' if embed else ''}[{label}]({target})"


def artifact_ref(target: str) -> str | None:
    """Alvo de link escrito pelo usuário -> URL do artefato no ALM ou id; None se não for artefato do RM.
    Aceita URL do ALM ('/rm/resources/'), id ('2001') e arquivo cujo nome começa pelo id
    ('../03-Regras/2001-x.md', '2001 REG Nome.md', '2001 REG Nome')."""
    target = unquote(unescape(target)).strip()
    if "/rm/resources/" in target:
        return target.split("?")[0]
    if re.match(r"[a-z][a-z0-9+.-]*:|#", target, re.I):  # http:, mailto:, âncora: não é artefato
        return None
    name = target.rstrip("/").rsplit("/", 1)[-1]
    name = name[:-3] if name.lower().endswith(".md") else name
    if re.search(r"\.[A-Za-z0-9]{2,4}$", name):  # outro tipo de arquivo (imagem, pdf...)
        return None
    m = ARTIFACT_FILE.fullmatch(name)
    return m.group(1) if m else None


# --- YAML (só o que o cabeçalho usa: dict, list, str, número, bool, None)

def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    plain = _PLAIN.fullmatch(s) and s == s.strip() and s.lower() not in _RESERVED
    return s if plain else json.dumps(s, ensure_ascii=False)  # string JSON é YAML válido


def _item(x, indent: int) -> list[str]:
    """Um item de lista: '- mapa' (dict vira bloco com a 1ª chave na linha do '-') ou '- escalar'."""
    pad = "  " * indent
    if isinstance(x, dict) and x:
        body = _yaml(x, indent + 1)
        return [f"{pad}- {body[0].lstrip()}", *body[1:]]  # 1ª chave colada no '-', resto já indentado
    return [f"{pad}- {_scalar(x)}"]


def _yaml(value, indent: int = 0) -> list[str]:
    pad = "  " * indent
    lines = []
    for key, v in value.items():
        if isinstance(v, dict) and v:
            lines += [f"{pad}{_scalar(key)}:", *_yaml(v, indent + 1)]
        elif isinstance(v, list) and v:
            lines.append(f"{pad}{_scalar(key)}:")
            for x in v:
                lines += _item(x, indent + 1)
        else:
            lines.append(f"{pad}{_scalar(key)}: {'{}' if v == {} else '[]' if v == [] else _scalar(v)}")
    return lines


def document(head: dict, body: str) -> str:
    """Cabeçalho YAML (chaves com valor None são omitidas) + corpo Markdown."""
    head = {k: v for k, v in head.items() if v is not None}
    return "---\n" + "\n".join(_yaml(head)) + "\n---\n" + body.strip() + "\n"


# --- XHTML -> Markdown

class _ToMarkdown(HTMLParser):
    def __init__(self, artifact: Artifact | None):
        super().__init__(convert_charrefs=True)
        self.artifact, self.out, self.lists = artifact, [], []
        self.href: str | None = None
        self.in_embed = False  # dentro de um <a> já escrito por inteiro (embed ou artefato): ignora o texto
        self.row_cells: int | None = None
        self.rows = 0
        self.item_start = False  # logo depois do marcador do item: <p> não abre parágrafo

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "p" and self.lists:  # lista solta (<li><p>): parágrafo dentro do item
            if not self.item_start:
                self.out.append("\n\n" + "   " * len(self.lists))
        elif tag in ("p", "div", "table"):
            self.out.append("\n\n")
        elif tag in ("ol", "ul"):
            if not self.lists:  # sublista continua colada no item pai
                self.out.append("\n")
            self.lists.append([tag, 0])
        elif tag == "li" and self.lists:
            kind = self.lists[-1]
            kind[1] += 1
            self.out.append("\n" + "   " * (len(self.lists) - 1) + (f"{kind[1]}. " if kind[0] == "ol" else "- "))
            self.item_start = True
        elif tag in ("b", "strong"):
            self.out.append("**")
        elif tag in ("i", "em"):
            self.out.append("*")
        elif tag == "br":
            self.out.append("\\\n")  # quebra forçada do CommonMark: volta a ser <br /> na gravação
        elif tag == "tr":
            self.row_cells = 0
            self.out.append("\n|")
        elif tag in ("td", "th"):
            self.row_cells = (self.row_cells or 0) + 1
            self.out.append(" ")
        elif tag == "a" and "embedded" in (a.get("class") or "").split():
            href = a.get("href", "")
            label, target = (self.artifact and self.artifact(href)) or (href, href)
            self.out.append(md_link(label, target, embed=True))
            self.in_embed, self.item_start = True, False
        elif tag == "a" and a.get("href") and self.artifact and (found := self.artifact(a["href"])):
            self.out.append(md_link(*found))  # padrão fixo: o texto do link é trocado por 'id título'
            self.in_embed, self.item_start = True, False
        elif tag == "a" and a.get("href"):
            self.href = a["href"]
            self.out.append("[")

    def handle_endtag(self, tag):
        if tag in ("ol", "ul") and self.lists:
            self.lists.pop()
            if not self.lists:  # fim de sublista: o próximo item vem colado (lista compacta)
                self.out.append("\n")
        elif tag in ("b", "strong"):
            self.out.append("**")
        elif tag in ("i", "em"):
            self.out.append("*")
        elif tag in ("td", "th"):
            self.out.append(" |")
        elif tag == "tr":
            self.rows += 1
            if self.rows == 1:  # Markdown exige a linha separadora depois do cabeçalho
                self.out.append("\n|" + " --- |" * (self.row_cells or 1))
        elif tag == "table":
            self.rows = 0
            self.out.append("\n\n")
        elif tag == "a" and self.in_embed:
            self.in_embed = False
        elif tag == "a" and self.href:
            self.out.append(f"]({self.href})")
            self.href = None

    def handle_data(self, data):
        if self.in_embed:  # o conteúdo do embed é só espaço
            return
        if data.strip():
            self.item_start = False
        self.out.append(re.sub(r"\s+", " ", data))

    def markdown(self) -> str:
        md = "".join(self.out)
        md = re.sub(r"\*\*\s*\*\*", "", md)                        # negrito vazio
        md = re.sub(r"(?:\\\n[ \t]*){2,}", "\n\n", md)                # <br/><br/> (EWM) = parágrafo
        md = re.sub(r"\n\n[ \t]+", "\n\n", md)                          # espaço no início do parágrafo
        md = re.sub(r"(?<=\S) {2,}", " ", md)                      # espaços repetidos (não a indentação)
        md = re.sub(r"[ \t]+\n", "\n", md)                         # espaço no fim da linha
        md = re.sub(r"(?m)^(\s*(?:#+ |\d+\. |- ))[ \t]+", r"\1", md)  # espaço depois do marcador
        md = re.sub(r"(?m)^[ \t]*(?:\d+\.|-)[ \t]*$\n?", "", md)   # item de lista vazio
        return re.sub(r"\n{3,}", "\n\n", md).strip()


def to_markdown(html: str | None, artifact: Artifact | None = None) -> str:
    """XHTML -> Markdown. Com `artifact(url) -> (rótulo, alvo) | None`, artefato embutido vira ![rótulo](alvo) e
    hyperlink para artefato vira [rótulo](alvo); outros links ficam [texto](href)."""
    parser = _ToMarkdown(artifact)
    parser.feed(html or "")
    return parser.markdown()


# --- Markdown -> XHTML

_md = MarkdownIt("commonmark", {"html": True, "xhtmlOut": True}).enable("table")


def to_xhtml(markdown: str, artifact_url: Callable[[str], str]) -> str:
    """Markdown -> XHTML. `![x](alvo)` e o legado `![[id]]` viram o embed do DOORS Next; `[x](alvo)` de artefato
    vira hyperlink para a URL dele. `artifact_url(id ou URL)` dá a URL do artefato; alvos aceitos em `artifact_ref`.
    Imagem e link que não são de artefato ficam como estão."""
    def embed(ref: str) -> str:
        return EMBED_HTML.format(url=escape(artifact_url(ref)))

    def image(m: re.Match) -> str:
        ref = artifact_ref(m.group(1))
        return embed(ref) if ref else m.group(0)

    def link(m: re.Match) -> str:
        ref = artifact_ref(m.group(1))
        return f'<a href="{escape(artifact_url(ref))}"' if ref else m.group(0)

    markdown = SPACED_TARGET.sub(r"](<\1>)", EMBED.sub(lambda m: embed(m.group(1)), markdown))
    html = _md.render(markdown).strip()
    html = re.sub(r'<img src="([^"]*)"[^>]*>', image, html)
    return re.sub(r'<a href="([^"]*)"', link, html)


def to_ewm_html(markdown: str) -> str:
    """Markdown -> descrição do EWM, que só tem texto, <br/>, <b>, <i> e <a> (listas viram '• ' / '1. ').
    Citar outro WI: 'Tarefa 123' no texto; o EWM cria o link 'Menções' ao salvar."""
    html = _md.render(markdown)
    html = re.sub(r"<h[1-6]>(.*?)</h[1-6]>", r"<b>\1</b><br/>", html)

    def items(match: re.Match) -> str:
        ordered = match.group(1) == "ol"
        start = int(m.group(1)) if (m := re.search(r'start="(\d+)"', match.group(2) or "")) else 1
        found = re.findall(r"<li>(.*?)</li>", match.group(3), re.S)
        lines = "".join(f"{f'{i}. ' if ordered else '• '}{item.strip()}<br/>" for i, item in enumerate(found, start))
        return lines + "<br/>"  # linha em branco depois da lista, como entre parágrafos

    # ponytail: sublistas saem planas; tratar a indentação se o time usar listas aninhadas na descrição
    # de dentro para fora; para quando nada muda (um <ul> digitado sem fechamento não trava)
    while (flat := re.sub(r"<(ol|ul)(\s[^>]*)?>((?:(?!<(?:ol|ul)[\s>]).)*?)</\1>", items, html, flags=re.S)) != html:
        html = flat
    html = re.sub(r"<p>(.*?)</p>", r"\1<br/><br/>", html, flags=re.S)
    html = html.replace("<strong>", "<b>").replace("</strong>", "</b>").replace("<em>", "<i>").replace("</em>", "</i>")
    html = re.sub(r"\\?<br\s*/?>\s*", "<br/>", html).replace("\n", " ")  # '\' solto antes de uma lista
    return re.sub(r"(<br/>)+$", "", html.strip())
