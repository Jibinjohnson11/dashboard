/* Content Writing workspace: lightweight rich-text editor, word count and
 * brand -> campaign filtering. The server sanitises all HTML with an allowlist,
 * so this editor only needs to produce reasonable markup, not safe markup. */
(function () {
  "use strict";

  var ALLOWED_SCHEMES = /^(https?:|mailto:)/i;

  function countWords(text) {
    var words = text.trim().split(/\s+/).filter(Boolean);
    return words.length;
  }

  function initEditor(form) {
    var editor = form.querySelector("[data-editor]");
    var area = form.querySelector("[data-editor-area]");
    var source = form.querySelector("[data-editor-source]");
    var counter = form.querySelector("[data-word-count]");
    if (!editor || !area || !source || typeof document.execCommand !== "function") return;

    // Enhance: hide the plain textarea, show the editor seeded with its (server-sanitised) HTML.
    // Parse via a template element so nothing executes while loading the draft.
    var tpl = document.createElement("template");
    tpl.innerHTML = source.value;
    tpl.content.querySelectorAll("script,style,iframe,object,embed").forEach(function (n) { n.remove(); });
    tpl.content.querySelectorAll("*").forEach(function (el) {
      Array.prototype.slice.call(el.attributes).forEach(function (attr) {
        if (/^on/i.test(attr.name)) el.removeAttribute(attr.name);
      });
    });
    area.appendChild(tpl.content);
    source.classList.add("editor-source", "is-enhanced");
    source.setAttribute("tabindex", "-1");
    source.setAttribute("aria-hidden", "true");
    editor.hidden = false;

    try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch (e) { /* not supported */ }
    try { document.execCommand("styleWithCSS", false, false); } catch (e) { /* not supported */ }

    function sync() {
      var html = area.innerHTML.trim();
      if (html === "<br>" || html === "<p><br></p>") html = "";
      source.value = html;
      if (counter) {
        var n = countWords(area.innerText || "");
        counter.textContent = n + (n === 1 ? " word" : " words");
      }
    }

    function updateActive() {
      editor.querySelectorAll("[data-cmd]").forEach(function (btn) {
        var cmd = btn.getAttribute("data-cmd");
        var active = false;
        try {
          if (cmd === "formatBlock") {
            active = (document.queryCommandValue("formatBlock") || "").toLowerCase() === btn.getAttribute("data-value");
          } else if (["bold", "italic", "underline", "insertUnorderedList", "insertOrderedList"].indexOf(cmd) !== -1) {
            active = document.queryCommandState(cmd);
          }
        } catch (e) { active = false; }
        btn.classList.toggle("is-active", active);
        if (btn.hasAttribute("aria-pressed") || active) btn.setAttribute("aria-pressed", String(active));
      });
    }

    editor.querySelector(".editor-toolbar").addEventListener("mousedown", function (e) {
      if (e.target.closest("[data-cmd]")) e.preventDefault(); // keep the text selection
    });

    editor.querySelector(".editor-toolbar").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-cmd]");
      if (!btn) return;
      var cmd = btn.getAttribute("data-cmd");
      var value = btn.getAttribute("data-value");
      area.focus();
      if (cmd === "createLink") {
        var url = window.prompt("Link URL (https://…)", "https://");
        if (!url) return;
        url = url.trim();
        if (!ALLOWED_SCHEMES.test(url)) {
          if (window.SEO && window.SEO.toast) window.SEO.toast("Links must start with https://, http:// or mailto:", "error");
          return;
        }
        document.execCommand("createLink", false, url);
      } else if (cmd === "formatBlock") {
        var current = (document.queryCommandValue("formatBlock") || "").toLowerCase();
        document.execCommand("formatBlock", false, current === value && value !== "p" ? "p" : value);
      } else {
        document.execCommand(cmd, false, null);
      }
      sync();
      updateActive();
    });

    // Paste as plain text: avoids importing messy styles from Word or web pages.
    area.addEventListener("paste", function (e) {
      if (!e.clipboardData) return;
      e.preventDefault();
      var text = e.clipboardData.getData("text/plain");
      document.execCommand("insertText", false, text);
    });

    area.addEventListener("input", sync);
    area.addEventListener("keyup", updateActive);
    area.addEventListener("mouseup", updateActive);
    document.addEventListener("selectionchange", function () {
      if (document.activeElement === area) updateActive();
    });
    form.addEventListener("submit", sync, true);

    // Warn before leaving with unsaved changes.
    var initial = source.value;
    var submitting = false;
    form.addEventListener("submit", function () { submitting = true; });
    window.addEventListener("beforeunload", function (e) {
      sync();
      if (!submitting && source.value !== initial) {
        e.preventDefault();
        e.returnValue = "";
      }
    });

    sync();
  }

  function initCampaignFilter(form) {
    var brand = form.querySelector("[data-brand-select]");
    var campaign = form.querySelector("[data-campaign-select]");
    var keyword = form.querySelector("input[name=keyword]");
    if (!brand || !campaign || brand.disabled) return;

    var options = Array.prototype.slice.call(campaign.options);
    function apply() {
      var selectedBrand = brand.value;
      options.forEach(function (opt) {
        if (!opt.value) return;
        var match = !selectedBrand || opt.getAttribute("data-brand") === selectedBrand;
        opt.hidden = !match;
        opt.disabled = !match;
      });
      var current = campaign.options[campaign.selectedIndex];
      if (current && current.disabled) campaign.value = "";
      campaign.disabled = !selectedBrand;
    }
    brand.addEventListener("change", apply);
    campaign.addEventListener("change", function () {
      var opt = campaign.options[campaign.selectedIndex];
      if (opt && opt.value && !brand.value) brand.value = opt.getAttribute("data-brand");
      if (keyword && !keyword.value && opt && opt.getAttribute("data-keyword")) keyword.value = opt.getAttribute("data-keyword");
    });
    // Re-enable before submit so the browser sends the value.
    form.addEventListener("submit", function () { campaign.disabled = false; }, true);
    apply();
  }

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.querySelector("[data-content-form]");
    if (!form) return;
    initEditor(form);
    initCampaignFilter(form);
  });
})();
