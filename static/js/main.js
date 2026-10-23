/* SEO Management Dashboard — shared UI behaviour (vanilla JS, no dependencies).
 * Everything here is progressive enhancement: the app works without JavaScript,
 * and all authorisation happens on the server. */
(function () {
  "use strict";

  var COLLAPSE_KEY = "seo.sidebarCollapsed";

  function storageGet(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function storageSet(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* storage unavailable */ }
  }

  /* ---------- Sidebar: desktop collapse + mobile drawer ---------- */
  function initSidebar() {
    var layout = document.querySelector("[data-layout]");
    if (!layout) return;
    var collapseBtn = document.querySelector("[data-sidebar-collapse]");
    var drawerBtn = document.querySelector("[data-drawer-toggle]");

    function setCollapsed(collapsed) {
      layout.classList.toggle("is-collapsed", collapsed);
      if (collapseBtn) {
        collapseBtn.setAttribute("aria-pressed", String(collapsed));
        collapseBtn.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
        collapseBtn.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
      }
    }
    setCollapsed(storageGet(COLLAPSE_KEY) === "1");
    if (collapseBtn) {
      collapseBtn.addEventListener("click", function () {
        var next = !layout.classList.contains("is-collapsed");
        setCollapsed(next);
        storageSet(COLLAPSE_KEY, next ? "1" : "0");
      });
    }

    function setDrawer(open) {
      layout.classList.toggle("is-drawer-open", open);
      if (drawerBtn) drawerBtn.setAttribute("aria-expanded", String(open));
      if (open) {
        var first = document.querySelector("#sidebar .nav-link");
        if (first) first.focus();
      }
    }
    if (drawerBtn) drawerBtn.addEventListener("click", function () { setDrawer(!layout.classList.contains("is-drawer-open")); });
    document.querySelectorAll("[data-drawer-close]").forEach(function (el) {
      el.addEventListener("click", function () { setDrawer(false); });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && layout.classList.contains("is-drawer-open")) {
        setDrawer(false);
        if (drawerBtn) drawerBtn.focus();
      }
    });
  }

  /* ---------- Dropdown menus (row actions, notifications, user menu) ---------- */
  function closeAllMenus(except) {
    document.querySelectorAll("[data-menu]").forEach(function (menu) {
      if (menu === except) return;
      var list = menu.querySelector(".menu-list");
      var toggle = menu.querySelector("[data-menu-toggle]");
      if (list && !list.hidden) {
        list.hidden = true;
        if (toggle) toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  function initMenus() {
    document.addEventListener("click", function (e) {
      var toggle = e.target.closest("[data-menu-toggle]");
      if (toggle) {
        var menu = toggle.closest("[data-menu]");
        var list = menu.querySelector(".menu-list");
        var opening = list.hidden;
        closeAllMenus(menu);
        list.hidden = !opening;
        toggle.setAttribute("aria-expanded", String(opening));
        if (opening) {
          // Open upwards if there's no room below (e.g. last table rows).
          list.classList.remove("menu-up");
          var rect = list.getBoundingClientRect();
          if (rect.bottom > window.innerHeight - 8 && rect.height < rect.top) list.classList.add("menu-up");
          var firstItem = list.querySelector(".menu-item");
          if (firstItem && e.detail === 0) firstItem.focus(); // keyboard activation
        }
        return;
      }
      if (!e.target.closest(".menu-list")) closeAllMenus(null);
    });

    document.addEventListener("keydown", function (e) {
      var list = e.target.closest && e.target.closest(".menu-list");
      if (e.key === "Escape") {
        var open = document.querySelector("[data-menu] .menu-list:not([hidden])");
        if (open) {
          var toggle = open.closest("[data-menu]").querySelector("[data-menu-toggle]");
          closeAllMenus(null);
          if (toggle) toggle.focus();
        }
        return;
      }
      if (list && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        var items = Array.prototype.slice.call(list.querySelectorAll(".menu-item"));
        var index = items.indexOf(document.activeElement);
        var next = e.key === "ArrowDown" ? (index + 1) % items.length : (index - 1 + items.length) % items.length;
        if (items[next]) items[next].focus();
      }
    });
  }

  /* ---------- Toast notifications ---------- */
  function dismissToast(toast) {
    toast.classList.add("is-leaving");
    window.setTimeout(function () { toast.remove(); }, 220);
  }

  function showToast(message, tone) {
    var stack = document.querySelector("[data-toasts]");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "toast-stack";
      stack.setAttribute("role", "status");
      stack.setAttribute("aria-live", "polite");
      stack.setAttribute("data-toasts", "");
      document.body.appendChild(stack);
    }
    var toast = document.createElement("div");
    toast.className = "toast toast-" + (tone || "success");
    var p = document.createElement("p");
    p.textContent = message;
    toast.appendChild(p);
    stack.appendChild(toast);
    window.setTimeout(function () { dismissToast(toast); }, 2400);
  }
  window.SEO = window.SEO || {};
  window.SEO.toast = showToast;

  function initToasts() {
    document.querySelectorAll("[data-toast]").forEach(function (toast) {
      if (!toast.hasAttribute("data-sticky")) {
        window.setTimeout(function () { dismissToast(toast); }, 5000);
      }
    });
    document.addEventListener("click", function (e) {
      var close = e.target.closest("[data-toast-close]");
      if (close) dismissToast(close.closest(".toast"));
    });
  }

  /* ---------- Copy to clipboard ---------- */
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.className = "sr-only";
      document.body.appendChild(area);
      area.select();
      try { document.execCommand("copy") ? resolve() : reject(); } catch (err) { reject(err); }
      area.remove();
    });
  }

  function initCopy() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-copy]");
      if (!btn) return;
      e.preventDefault();
      copyText(btn.getAttribute("data-copy")).then(function () {
        btn.classList.add("is-copied");
        window.setTimeout(function () { btn.classList.remove("is-copied"); }, 1200);
        showToast("Copied to clipboard", "success");
      }, function () {
        showToast("Couldn't copy — please copy it manually.", "error");
      });
      closeAllMenus(null);
    });
  }

  /* ---------- Confirmation dialog ---------- */
  function initConfirm() {
    var dialog = document.getElementById("confirm-dialog");

    document.addEventListener("submit", function (e) {
      var form = e.target;
      var submitter = e.submitter || null;
      var source = submitter && submitter.hasAttribute("data-confirm") ? submitter : (form.hasAttribute("data-confirm") ? form : null);
      if (!source || form.dataset.confirmed === "1") return;

      // Native validation for decision forms: feedback required for some actions.
      if (submitter && submitter.hasAttribute("data-requires-feedback")) {
        var feedback = form.querySelector("textarea[name=feedback]");
        if (feedback && feedback.value.trim().length < 5) return; // let the server explain
      }

      if (!dialog || typeof dialog.showModal !== "function") {
        if (!window.confirm(source.getAttribute("data-confirm"))) e.preventDefault();
        return;
      }
      e.preventDefault();
      e.stopImmediatePropagation();
      dialog.querySelector("[data-confirm-title]").textContent = source.getAttribute("data-confirm-title") || "Are you sure?";
      dialog.querySelector("[data-confirm-message]").textContent = source.getAttribute("data-confirm");
      var okBtn = dialog.querySelector("[data-confirm-button]");
      okBtn.textContent = source.getAttribute("data-confirm-label") || "Confirm";
      okBtn.className = "btn " + (source.getAttribute("data-confirm-tone") === "primary" ? "btn-primary" : "btn-danger");
      var icon = dialog.querySelector(".dialog-icon");
      if (icon) icon.hidden = source.getAttribute("data-confirm-tone") === "primary";

      dialog.returnValue = "";
      dialog.showModal();
      okBtn.focus();
      dialog.addEventListener("close", function onClose() {
        dialog.removeEventListener("close", onClose);
        if (dialog.returnValue === "confirm") {
          form.dataset.confirmed = "1";
          if (form.requestSubmit) form.requestSubmit(submitter || undefined);
          else form.submit();
        }
      });
    }, true);
  }

  /* ---------- Loading state + double-submit prevention ---------- */
  function initLoadingForms() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (e.defaultPrevented) return;
      if (form.dataset.submitting === "1") { e.preventDefault(); return; }
      if (!form.hasAttribute("data-loading-form") && form.method.toLowerCase() !== "post") return;
      form.dataset.submitting = "1";
      var submitter = e.submitter;
      if (submitter) submitter.classList.add("is-loading");
      // Disable after the browser has captured the submitter's name/value.
      window.setTimeout(function () {
        form.querySelectorAll("button[type=submit], input[type=submit]").forEach(function (b) { b.disabled = true; });
      }, 0);
      // Recover if the user navigates back to a cached page.
      window.addEventListener("pageshow", function () {
        form.dataset.submitting = "";
        delete form.dataset.confirmed;
        form.querySelectorAll("button[type=submit], input[type=submit]").forEach(function (b) { b.disabled = false; b.classList.remove("is-loading"); });
      }, { once: true });
    });
  }

  /* ---------- Show / hide password ---------- */
  function initPasswordToggles() {
    document.querySelectorAll("[data-password-toggle]").forEach(function (btn) {
      var input = document.getElementById(btn.getAttribute("data-password-toggle"));
      if (!input) return;
      btn.addEventListener("click", function () {
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.setAttribute("aria-pressed", String(show));
        btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
        var use = btn.querySelector("use");
        if (use) use.setAttribute("href", show ? "#i-eye-off" : "#i-eye");
      });
    });
  }

  /* ---------- Filter toolbars: submit when a select changes ---------- */
  function initAutoSubmit() {
    document.querySelectorAll("form[data-auto-submit]").forEach(function (form) {
      form.querySelectorAll("select, input[type=date]").forEach(function (el) {
        el.addEventListener("change", function () {
          var page = form.querySelector("input[name=page]");
          if (page) page.remove();
          form.requestSubmit ? form.requestSubmit() : form.submit();
        });
      });
    });
  }

  /* ---------- Filter long checkbox lists (team & campaign assignment) ---------- */
  function initChoiceFilters() {
    document.querySelectorAll("[data-choice-filter]").forEach(function (input) {
      var list = input.parentElement;
      input.addEventListener("input", function () {
        var q = input.value.trim().toLowerCase();
        list.querySelectorAll("label").forEach(function (label) {
          var row = label.parentElement;
          row.hidden = q && label.textContent.toLowerCase().indexOf(q) === -1;
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initSidebar();
    initMenus();
    initToasts();
    initCopy();
    initConfirm();
    initLoadingForms();
    initPasswordToggles();
    initAutoSubmit();
    initChoiceFilters();
  });
})();
