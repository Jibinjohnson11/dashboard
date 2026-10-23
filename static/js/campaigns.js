/* Campaign form: live preview of secondary keywords as chips, and role-aware
 * assignment hints on the team form. */
(function () {
  "use strict";

  function initKeywordPreview() {
    var input = document.querySelector("[data-keyword-input]");
    var preview = document.querySelector("[data-keyword-preview]");
    if (!input || !preview) return;

    function render() {
      preview.textContent = "";
      var seen = {};
      input.value.split(/[,\n]/).forEach(function (raw) {
        var keyword = raw.trim().replace(/\s+/g, " ");
        var key = keyword.toLowerCase();
        if (!keyword || seen[key]) return;
        seen[key] = true;
        var chip = document.createElement("span");
        chip.className = "tag";
        chip.textContent = keyword;
        preview.appendChild(chip);
      });
      preview.hidden = !preview.childElementCount;
    }
    input.addEventListener("input", render);
    render();
  }

  document.addEventListener("DOMContentLoaded", initKeywordPreview);
})();
