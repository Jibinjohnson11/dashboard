/* Permission editor: module/global select helpers, View-gating, override badges,
 * role switching (re-applies that role's limits and defaults) and confirmation for
 * high-impact saves. The server re-validates everything. */
(function () {
  "use strict";

  function init(form) {
    var boxes = function (scope) { return Array.prototype.slice.call((scope || form).querySelectorAll("input[data-perm]")); };
    var configEl = document.getElementById("perm-config");
    var config = configEl ? JSON.parse(configEl.textContent) : null;
    var roleSelect = form.querySelector("[data-perm-role]");
    var initialRole = roleSelect ? roleSelect.value : null;
    var initial = {};
    boxes().forEach(function (b) { initial[b.value] = b.checked; });

    function refresh() {
      form.querySelectorAll("[data-perm-module]").forEach(function (card) {
        var view = card.querySelector("input[data-perm-view]");
        var off = !view || !view.checked;
        card.classList.toggle("is-off", off);
      });
      boxes().forEach(function (b) {
        var tag = b.closest("[data-perm-item]").querySelector("[data-override-tag]");
        if (tag) tag.hidden = b.disabled || b.checked === (b.getAttribute("data-default") === "1");
      });
      // High-impact: role change, or removing any module the member could open before.
      var lostModule = boxes().some(function (b) { return b.hasAttribute("data-perm-view") && initial[b.value] && !b.checked; });
      var roleChanged = roleSelect && roleSelect.value !== initialRole;
      var saveBtn = form.querySelector("button[type=submit]:not([name=reset])");
      if (saveBtn && roleSelect) {
        if (roleChanged || lostModule) {
          saveBtn.setAttribute("data-confirm", roleChanged
            ? "Change this member's role? Their module access and assignments follow the new role's rules from their next request."
            : "Remove module access? The member loses those pages immediately on their next request.");
          saveBtn.setAttribute("data-confirm-title", roleChanged ? "Change role" : "Remove module access");
          saveBtn.setAttribute("data-confirm-label", "Save changes");
          saveBtn.setAttribute("data-confirm-tone", "primary");
        } else {
          saveBtn.removeAttribute("data-confirm");
        }
      }
    }

    function setAll(scope, value) {
      boxes(scope).forEach(function (b) { if (!b.disabled) b.checked = value; });
      refresh();
    }

    form.addEventListener("click", function (e) {
      if (e.target.closest("[data-perm-all]")) setAll(form, true);
      else if (e.target.closest("[data-perm-none]")) setAll(form, false);
      else if (e.target.closest("[data-perm-module-all]")) setAll(e.target.closest("[data-perm-module]"), true);
      else if (e.target.closest("[data-perm-module-none]")) setAll(e.target.closest("[data-perm-module]"), false);
    });
    form.addEventListener("change", function (e) {
      var b = e.target;
      if (b.matches && b.matches("input[data-perm]")) {
        var card = b.closest("[data-perm-module]");
        if (!b.hasAttribute("data-perm-view") && b.checked) {
          var view = card.querySelector("input[data-perm-view]");
          if (view && !view.disabled) view.checked = true; // an action implies View
        }
        refresh();
      }
    });

    if (roleSelect && config) {
      roleSelect.addEventListener("change", function () {
        var role = config[roleSelect.value];
        var isAdmin = roleSelect.value === "admin";
        boxes().forEach(function (b) {
          var allowed = role.ceiling.indexOf(b.value) !== -1;
          var isDefault = role.defaults.indexOf(b.value) !== -1;
          b.disabled = !allowed || isAdmin;
          b.checked = isAdmin || isDefault;
          b.setAttribute("data-default", isDefault ? "1" : "0");
          var item = b.closest("[data-perm-item]");
          var unavailable = item.querySelector(".perm-tag:not([data-override-tag])");
          if (unavailable) unavailable.hidden = allowed;
        });
        var note = form.querySelector("[data-admin-note]");
        if (note) note.hidden = !isAdmin;
        refresh();
      });
      if (roleSelect.value === "admin") boxes().forEach(function (b) { b.disabled = true; b.checked = true; });
    }
    refresh();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-perm-form]").forEach(init);
  });
})();
