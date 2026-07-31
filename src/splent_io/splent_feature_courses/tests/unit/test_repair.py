"""Pointing migrated bodies at the files that came with them.

The import brought every attachment across and rewrote most references,
but eighteen across thirteen pages kept the shape the old wiki wrote them
in: an image whose target is a bare filename. A relative target resolves
against the page's own URL, so a reader got a broken image where a set of
slides should be, or an empty bullet reading "Presentación en pdf: .".

Nothing was missing. Every one of the eighteen matched an attachment of
the page that referenced it.
"""

import pytest

from splent_io.splent_feature_courses.repair import repair_body, unresolved


class Attachment:
    def __init__(self, name, media_item_id):
        self.name = name
        self.media_item_id = media_item_id


@pytest.fixture
def files():
    return [Attachment("slides.pdf", 158), Attachment("diagram.png", 507)]


class TestWhatGetsRewritten:
    def test_a_document_becomes_a_link(self, files):
        """A set of slides is not an illustration. Rendered as an image it is
        a broken icon; as a link it is what the sentence promised."""
        body, changes = repair_body("![](slides.pdf)", files)
        assert body == "[slides.pdf](/media/file/158)"
        assert changes == [("slides.pdf", "/media/file/158")]

    def test_a_picture_stays_a_picture(self, files):
        body, _ = repair_body("![](diagram.png)", files)
        assert body == "![diagram.png](/media/file/507)"

    def test_a_title_does_not_hide_the_reference(self, files):
        """The shape the old wiki actually wrote, and the reason a simpler
        pattern missed these: it stops at the space before the title and
        then fails to find the closing bracket."""
        body, changes = repair_body('![](slides.pdf "slides.pdf")', files)
        assert "/media/file/158" in body
        assert len(changes) == 1

    def test_the_same_thing_written_as_html(self, files):
        body, changes = repair_body('<img src="diagram.png" alt="">', files)
        assert "/media/file/507" in body
        assert len(changes) == 1


class TestWhatIsLeftAlone:
    @pytest.mark.parametrize(
        "body",
        [
            "![](https://example.org/x.png)",
            "![](/media/file/999)",
            "![](data:image/png;base64,AAAA)",
        ],
    )
    def test_a_target_that_is_already_a_url(self, body, files):
        repaired, changes = repair_body(body, files)
        assert repaired == body
        assert changes == []

    def test_a_name_no_attachment_answers_for(self, files):
        """A reference to a file nobody migrated is a missing file. Inventing
        a URL for it would turn a visible problem into an invisible one."""
        body, changes = repair_body("![](nowhere.png)", files)
        assert body == "![](nowhere.png)"
        assert changes == []
        assert unresolved("![](nowhere.png)", files) == ["nowhere.png"]

    def test_a_name_belonging_to_another_page(self):
        """Names are matched against the page's own attachments and never
        across the wiki: two courses can each have a diagrama.png, and
        guessing between them would quietly show the wrong year."""
        body, changes = repair_body("![](diagram.png)", [])
        assert body == "![](diagram.png)"
        assert changes == []


class TestRunningItTwice:
    def test_the_second_pass_changes_nothing(self, files):
        once, first = repair_body("![](slides.pdf)", files)
        twice, second = repair_body(once, files)
        assert twice == once
        assert second == []


class TestEmptyBodies:
    @pytest.mark.parametrize("body", ["", None])
    def test_nothing_to_do(self, body, files):
        repaired, changes = repair_body(body, files)
        assert repaired == ""
        assert changes == []
