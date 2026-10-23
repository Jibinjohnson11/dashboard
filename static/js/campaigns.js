/* Campaigns: create/edit in a modal (progressive enhancement over the full-page form)
 * and a chip-style tag input for secondary keywords. The server validates everything. */
(function () {
  "use strict";

  function makeIcon(name) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS(ns, "use");
    use.setAttribute("href", "#i-" + name);
    svg.appendChild(use);
    return svg;
  }

  /* ---------- Secondary keywords tag input ---------- */
  function initTagInput(root) {
    var source = root.querySelector("textarea[data-keyword-input]");
    var box = root.querySelector("[data-tag-input]");
    if (!source || !box || box.dataset.ready) return;
    box.dataset.ready = "1";
    var chips = box.querySelector("[data-tag-chips]");
    var field = box.querySelector("[data-tag-field]");
    var keywords = [];

    function parse(text) {
      return text.split(/[,\n]/).map(function (k) { return k.trim().replace(/\s+/g, " "); }).filter(Boolean);
    }
    function sync() { source.value = keywords.join("\n"); }
    function render() {
      chips.textContent = "";
      keywords.forEach(function (keyword, index) {
        var chip = document.createElement("span");
        chip.className = "tag-input-chip";
        chip.appendChild(document.createTextNode(keyword));
        var remove = document.createElement("button");
        remove.type = "button";
        remove.setAttribute("aria-label", "Remove " + keyword);
        remove.appendChild(makeIcon("x"));
        remove.addEventListener("click", function () { keywords.splice(index, 1); render(); sync(); field.focus(); });
        chip.appendChild(remove);
        chips.appendChild(chip);
      });
    }
    function add(text) {
      parse(text).forEach(function (keyword) {
        var exists = keywords.some(function (k) { return k.toLowerCase() === keyword.toLowerCase(); });
        if (!exists && keywords.length < 30) keywords.push(keyword.slice(0, 120));
      });
      render();
      sync();
    }

    add(source.value);
    source.classList.add("keyword-source", "is-enhanced");
    source.setAttribute("tabindex", "-1");
    box.hidden = false;

    box.addEventListener("click", function (e) { if (e.target === box || e.target === chips) field.focus(); });
    field.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === ",") {
        e.preventDefault();
        if (field.value.trim()) { add(field.value); field.value = ""; }
      } else if (e.key === "Backspace" && !field.value && keywords.length) {
        keywords.pop(); render(); sync();
      }
    });
    field.addEventListener("paste", function (e) {
      var text = (e.clipboardData || window.clipboardData).getData("text");
      if (/[,\n]/.test(text)) { e.preventDefault(); add(text); }
    });
    // Keep anything still typed in the box when the form is submitted.
    source.form.addEventListener("submit", function () { if (field.value.trim()) { add(field.value); field.value = ""; } }, true);
  }

  /* ---------- Modal create / edit ---------- */
  function initModal() {
    var dialog = document.getElementById("campaign-modal");
    if (!dialog || typeof dialog.showModal !== "function") return; // links fall back to full pages
    var body = dialog.querySelector("[data-modal-body]");

    document.addEventListener("click", function (e) {
      var opener = e.target.closest("[data-modal-open]");
      if (!opener) return;
      e.preventDefault();
      body.textContent = "";
      var loading = document.createElement("div");
      loading.className = "modal-loading";
      loading.textContent = "Loading…";
      body.appendChild(loading);
      dialog.showModal();
      fetch(opener.getAttribute("data-modal-open"), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) {
          body.innerHTML = html; // same-origin, server-rendered and auto-escaped by Django templates
          initTagInput(body);
          initChoiceFilter(body);
          var first = body.querySelector("input:not([type=hidden]):not([disabled]), select:not([disabled])");
          if (first) first.focus();
        })
        .catch(function () { window.location.href = opener.getAttribute("href"); });
    });
    dialog.addEventListener("click", function (e) {
      if (e.target.closest("[data-modal-close]") || e.target === dialog) dialog.close();
    });
  }

  function initChoiceFilter(root) {
    root.querySelectorAll("[data-choice-filter]").forEach(function (input) {
      input.addEventListener("input", function () {
        var q = input.value.trim().toLowerCase();
        input.parentElement.querySelectorAll("label").forEach(function (label) {
          label.parentElement.hidden = q && label.textContent.toLowerCase().indexOf(q) === -1;
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTagInput(document);
    initModal();
  });
})();
