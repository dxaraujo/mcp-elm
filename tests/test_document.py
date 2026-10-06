from datetime import datetime, timezone

from mcp_alm.infra.document import (document, local_datetime, one_line, to_ewm_html, to_markdown, to_xhtml,
                                    utc_datetime)

UC_HTML = """<div xmlns="http://www.w3.org/1999/xhtml">
<h2 dir="ltr" id="_1">Pré-condição:</h2>
<p dir="ltr"><span style="font-size:12pt"><span class="TextRun">esteja </span><span class="NormalTextRun">difer</span><span>ente de cancelado.</span></span></p>
<h1><b><span style="font-size:14pt">Fluxo Básico:</span></b></h1>
<ol dir="ltr"><li><span>O sistema verifica se o cliente foi identificado:<span> </span><span class="com-ibm-rdm-editor-EmbeddedResourceDecorator minimised"><a class="embedded" href="https://alm.test/rm/resources/TX_1">  </a></span></span> <span>  </span></li>
<li><span>O sistema verifica a compatibilidade: </span> <span>  </span></li>
<li id="_3"> </li></ol>
<p> </p></div>"""


def test_yaml_header_quotes_only_what_needs_it():
    head = {"id": 1, "title": "UC - A: b", "state": "Pronto", "attributes": {"Estimativa": "4h", "Vazio": {}},
            "links": {"Pai": ["2: X"]}, "none": None}
    assert document(head, "corpo") == ('---\nid: 1\ntitle: "UC - A: b"\nstate: Pronto\nattributes:\n'
                                       '  Estimativa: "4h"\n  Vazio: {}\nlinks:\n  Pai:\n    - "2: X"\n---\ncorpo\n')


def test_yaml_renders_list_of_mappings_as_block_sequence():
    # OKF trust: verified é uma lista de mapas {by, at}; deve virar bloco YAML válido, não repr de dict
    head = {"verified": [{"by": "human:joao", "at": "2024-10-01T13:45:10Z"},
                         {"by": "human:ana", "at": "2024-10-02T09:00:00Z"}]}
    assert document(head, "corpo") == (
        '---\nverified:\n  - by: "human:joao"\n    at: "2024-10-01T13:45:10Z"\n'
        '  - by: "human:ana"\n    at: "2024-10-02T09:00:00Z"\n---\ncorpo\n')


def test_utc_datetime_normalizes_to_utc_iso():
    assert utc_datetime("2026-09-30T22:35:27.611Z") == "2026-09-30T22:35:27Z"
    assert utc_datetime("2024-05-09 18:29:09+00:00") == "2024-05-09T18:29:09Z"
    assert utc_datetime("2024-05-09T15:00:00-03:00") == "2024-05-09T18:00:00Z"  # Brasília -> UTC
    assert utc_datetime("2024-05-09T18:29:09") == "2024-05-09T18:29:09Z"  # naïve = UTC
    assert utc_datetime(datetime(2024, 5, 9, 18, 29, 9, tzinfo=timezone.utc)) == "2024-05-09T18:29:09Z"
    assert utc_datetime(None) is None
    assert utc_datetime("") is None


def test_word_html_to_markdown_keeps_structure_and_embeds():
    md = to_markdown(UC_HTML, lambda url: "2001: Regra de validação")
    assert md == ("## Pré-condição:\n\nesteja diferente de cancelado.\n\n# **Fluxo Básico:**\n\n"
                  "1. O sistema verifica se o cliente foi identificado: ![[2001: Regra de validação]]\n"
                  "2. O sistema verifica a compatibilidade:")


def test_line_break_round_trip():
    md = to_markdown("a<br/>b")
    assert md == "a\\\nb"
    assert to_xhtml(md, str) == "<p>a<br />\nb</p>"


def test_collapses_spaces_around_embed_but_keeps_indentation():
    md = to_markdown('<ul><li>a,  <a class="embedded" href="u"> </a>  e;<ul><li>b</li></ul></li></ul>', lambda u: "1: X")
    assert md == "- a, ![[1: X]] e;\n   - b"


def test_table_to_markdown():
    md = to_markdown("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
    assert md == "| A | B |\n| --- | --- |\n| 1 | 2 |"


def test_markdown_to_xhtml_with_embed():
    html = to_xhtml("## Fluxo\n\n1. Passo ![[2001: Regra]]\n2. Outro & mais", lambda i: f"https://alm.test/rm/{i}")
    assert "<h2>Fluxo</h2>" in html and "<ol>" in html and "&amp; mais" in html
    assert '<a class="embedded" href="https://alm.test/rm/2001"> </a>' in html


def test_local_datetime_and_one_line():
    assert local_datetime("2024-05-09 18:29:09.534000+00:00") == "2024-05-09 15:29"
    assert local_datetime("2026-09-30T22:35:27.611Z") == "2026-09-30 19:35"
    assert one_line("\ntask 1 - ajustes\n\nSee merge") == "task 1 - ajustes"
    assert one_line("RN\xa0-\xa0Regra") == "RN - Regra"


# descrição de WI como o EWM grava: texto, <br/> e '•' digitado
EWM_DESCRIPTION = ("Reprocessar os pedidos pendentes.<br/>Regras especiais:<br/>• Os pedidos devem ser aprovados<br/>"
              "• Lista: anexo.csv<br/>Continuação da demanda 900.<br/><br/> Tarefa 1001 - Teste")


def test_ewm_description_round_trip():
    md = to_markdown(EWM_DESCRIPTION)
    assert md == ("Reprocessar os pedidos pendentes.\\\nRegras especiais:\\\n• Os pedidos devem ser aprovados\\\n"
                  "• Lista: anexo.csv\\\nContinuação da demanda 900.\n\nTarefa 1001 - Teste")
    assert to_ewm_html(md) == EWM_DESCRIPTION.replace("<br/> Tarefa", "<br/>Tarefa")


def test_markdown_to_ewm_html_flattens_blocks():
    html = to_ewm_html("## Regras\n\n- **um** & *dois*\n- [link](https://x)\n\n1. a\n2. b\n\nFim")
    assert html == ('<b>Regras</b><br/>• <b>um</b> &amp; <i>dois</i><br/>• <a href="https://x">link</a><br/><br/>'
                    "1. a<br/>2. b<br/><br/>Fim")



def test_wiki_link_without_bang_is_plain_text():
    assert "embedded" not in to_xhtml("[[9]]", lambda i: f"https://x/{i}")


def test_ewm_html_escapes_text_and_survives_raw_tags():
    assert to_ewm_html("Tom & Jerry < 5") == "Tom &amp; Jerry &lt; 5"
    assert to_ewm_html("use a tag <ul> aqui") == "use a tag <ul> aqui"  # <ul> sem fechamento: não trava
    assert to_ewm_html("1. a\n\ntexto\n\n2. b") == "1. a<br/><br/>texto<br/><br/>2. b"  # <ol start="2">


def test_nested_list_round_trip_is_stable():
    md = "1. Item\n   - sub A\n   - sub B\n2. Outro"
    assert to_markdown(to_xhtml(md, str)) == md


def test_loose_list_keeps_numbering():
    html = "<ol><li><p>Item</p><ul><li>sub A</li></ul></li><li><p>Outro</p></li></ol>"
    assert to_markdown(html) == "1. Item\n   - sub A\n2. Outro"
