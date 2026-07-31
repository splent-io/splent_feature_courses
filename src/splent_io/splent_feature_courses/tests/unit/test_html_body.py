"""Pages written in BookStack's visual editor.

BookStack keeps a column per editor. A page written in markdown has its
source in ``markdown``; a page written in the WYSIWYG editor has only
``html``, and its ``markdown`` is the empty string. The WYSIWYG editor is
the one BookStack ships as the default, so a wiki whose authors never
changed that setting stores every page that way.

Reading only ``markdown`` imported those pages blank and reported it as
"has an empty body in the source", which reads like a fact about the wiki
being migrated rather than a failure to read it. EGC 2026/2027 arrived as
eleven empty pages: 130 KB of a teacher's material for the course starting
in September, every one of them looking like a page nobody had written.
"""

import pytest

from splent_io.splent_feature_courses.html_body import body_of, html_to_markdown


class TestWhichColumnIsUsed:
    def test_markdown_wins_when_there_is_any(self):
        """It is what the author actually typed. A round trip through HTML
        loses the shape of the source even when it keeps every word."""
        body, converted = body_of(
            {"markdown": "# Título\n\ntexto", "html": "<h1>Otro</h1>"}
        )

        assert body.startswith("# Título")
        assert converted is False

    def test_html_is_read_when_the_markdown_is_empty(self):
        body, converted = body_of({"markdown": "", "html": "<h1>Práctica 0</h1>"})

        assert "# Práctica 0" in body
        assert converted is True

    def test_whitespace_does_not_count_as_markdown(self):
        body, converted = body_of({"markdown": "   \n\n ", "html": "<p>real</p>"})

        assert "real" in body
        assert converted is True

    def test_a_page_with_neither_is_genuinely_empty(self):
        assert body_of({"markdown": "", "html": ""}) == ("", False)

    def test_a_missing_column_is_not_an_error(self):
        """A source read with a narrower query still has to work."""
        assert body_of({}) == ("", False)


class TestWhatTheConversionKeeps:
    def test_headings_become_atx(self):
        """Setext underlining would be technically equal and unreadable in
        an editor beside a hundred other headings."""
        assert "## Contenidos" in html_to_markdown("<h2>Contenidos</h2>")

    def test_a_code_block_keeps_the_language_it_declared(self):
        """It is what decides the highlighting a reader gets, and this wiki
        is mostly install instructions."""
        html = '<pre><code class="language-bash">apt update</code></pre>'

        assert "```bash" in html_to_markdown(html)
        assert "apt update" in html_to_markdown(html)

    def test_the_language_is_lowercased(self):
        """The same wiki spells it yaml and YAML, and a fence is matched
        against a lowercase name."""
        html = '<pre><code class="language-YAML">a: 1</code></pre>'

        assert "```yaml" in html_to_markdown(html)

    def test_a_code_block_with_no_language_still_fences(self):
        assert "```" in html_to_markdown("<pre><code>algo</code></pre>")

    def test_a_table_stays_a_table(self):
        html = "<table><tbody><tr><td>uno</td><td>dos</td></tr></tbody></table>"

        out = html_to_markdown(html)

        assert "| uno | dos |" in out

    def test_a_link_keeps_its_target(self):
        html = '<p>Ver <a href="https://uvlhub.io">uvlhub</a></p>'

        assert "[uvlhub](https://uvlhub.io)" in html_to_markdown(html)

    def test_inline_code_survives(self):
        assert "`pyproject.toml`" in html_to_markdown(
            "<p><code>pyproject.toml</code></p>"
        )

    def test_lists_use_one_bullet(self):
        """Mixing - and * in one wiki is noise in every diff afterwards."""
        out = html_to_markdown("<ul><li>uno</li><li>dos</li></ul>")

        assert "- uno" in out
        assert "* uno" not in out


class TestWhatTheConversionRemoves:
    def test_the_editor_anchors_do_not_travel(self):
        """BookStack stamps an id on nearly every block so its own editor
        can jump to it. They mean nothing here and would survive into the
        markdown as raw HTML."""
        html = '<p id="bkmrk-primera-sesi%C3%B3n">Primera sesión</p>'

        out = html_to_markdown(html)

        assert "bkmrk" not in out
        assert "Primera sesión" in out

    def test_a_non_breaking_space_becomes_a_space(self):
        """It renders as a space and reads as one, but it is not one: it
        stops a line wrapping and shows up as a literal &nbsp; wherever the
        text is used as text, which is what a search extract is."""
        out = html_to_markdown("<p>uno&nbsp;dos</p>")

        assert " " not in out
        assert "uno dos" in out

    def test_it_is_removed_from_markdown_bodies_too(self):
        """Those arrive already carrying the entity, from somebody pasting
        rich text into the markdown editor."""
        body, _ = body_of({"markdown": "uno&nbsp;dos", "html": ""})

        assert "&nbsp;" not in body

    def test_scripts_and_styles_do_not_travel(self):
        html = "<p>texto</p><script>alert(1)</script><style>p{color:red}</style>"

        out = html_to_markdown(html)

        assert "alert" not in out
        assert "color:red" not in out

    def test_long_runs_of_blank_lines_collapse(self):
        out = html_to_markdown("<p>uno</p>\n\n\n\n\n<p>dos</p>")

        assert "\n\n\n" not in out


class TestTheShapeOfTheOutput:
    @pytest.mark.parametrize("html", ["", "   ", "\n"])
    def test_nothing_in_nothing_out(self, html):
        assert html_to_markdown(html) == ""

    def test_it_ends_with_exactly_one_newline(self):
        out = html_to_markdown("<p>uno</p>")

        assert out.endswith("\n")
        assert not out.endswith("\n\n")

    def test_the_escaped_newlines_the_column_stores_become_real_ones(self):
        r"""The html column holds \n as two characters, so a body read
        straight out of it is one enormous line."""
        out = html_to_markdown("<p>uno</p>\\n<p>dos</p>")

        assert "\\n" not in out
        assert "uno" in out and "dos" in out
