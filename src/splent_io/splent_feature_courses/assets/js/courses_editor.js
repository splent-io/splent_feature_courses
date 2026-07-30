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

        // The live preview asks the server, not a bundled renderer. The
        // library's own previewer is a different markdown engine: no code
        // highlighting, none of the plugins, another sanitiser, so what it
        // showed was almost the published page, and "almost" is exactly
        // what an editor cannot check against. Debounced, and stamped with
        // a sequence number so a slow response can never overwrite a newer
        // one.
        var previewUrl = textarea.getAttribute("data-preview-url");
        var csrfField = document.querySelector('input[name="csrf_token"]');
        var timer = null;
        var seq = 0;

        function renderPreview(text, preview) {
            if (!previewUrl) {
                return "";
            }
            var mine = ++seq;
            clearTimeout(timer);
            timer = setTimeout(function () {
                fetch(previewUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-CSRFToken": csrfField ? csrfField.value : "",
                    },
                    body: "body_md=" + encodeURIComponent(text),
                })
                    .then(function (response) {
                        if (!response.ok) {
                            throw new Error(String(response.status));
                        }
                        return response.text();
                    })
                    .then(function (html) {
                        // The server sanitised this exactly as it does for
                        // readers; the editor adds nothing on top.
                        if (mine === seq) {
                            preview.innerHTML = html;
                        }
                    })
                    .catch(function () {
                        if (mine === seq) {
                            preview.innerHTML =
                                "<p class=\"text-muted\">" +
                                "La previsualizaci\u00f3n no responde; " +
                                "el contenido se guarda igualmente.</p>";
                        }
                    });
            }, 350);
            // Shown until the fetch lands; keeping the old markup avoids a
            // flash of empty panel between keystrokes.
            return preview.innerHTML || "";
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
            previewRender: renderPreview,
            // The preview panel dresses as the page it will become:
            // cms-public and site-main are the selectors the theme and the
            // skin scope the reading styles under, prose is the measure.
            previewClass: ["editor-preview", "cms-public", "site-main", "prose"],
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
