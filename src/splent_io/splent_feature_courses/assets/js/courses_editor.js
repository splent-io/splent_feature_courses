/* The markdown editor for the page body.
 *
 * The wiki this replaces had zero active users, and the diagnosis was that
 * writing in it was too much work. So the editor matters as much as the
 * reading side: a toolbar for the marks staff actually use, a live preview,
 * and keyboard shortcuts.
 *
 * EasyMDE is vendored under assets/vendor, not loaded from a CDN. The
 * product sits behind a university proxy and a remote script is a broken
 * editor waiting for a bad day.
 *
 * Markdown stays the stored form. This editor writes into the same
 * textarea the form already posted, so saving, the server-rendered preview
 * and the published page cannot drift apart: the server is still the one
 * that renders.
 */
(function () {
    "use strict";

    function init() {
        var textarea = document.querySelector("[data-courses-editor]");
        if (!textarea || typeof EasyMDE === "undefined") {
            // No editor on this screen, or the vendored script did not
            // load. Either way the plain textarea still works and the page
            // is still editable, which is the point of degrading here
            // rather than throwing.
            return;
        }

        new EasyMDE({
            element: textarea,
            // The textarea is the form field, so its value has to stay
            // authoritative on submit.
            forceSync: true,
            autoDownloadFontAwesome: false,
            spellChecker: false,
            status: ["lines", "words"],
            minHeight: "420px",
            // Only the marks course material is written with. A shorter
            // toolbar is faster to learn than a complete one.
            toolbar: [
                "bold",
                "italic",
                "heading",
                "|",
                "code",
                "quote",
                "unordered-list",
                "ordered-list",
                "table",
                "|",
                "link",
                "image",
                "|",
                "preview",
                "side-by-side",
                "fullscreen",
                "|",
                "guide",
            ],
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
