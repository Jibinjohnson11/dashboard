/* Brand form: preview a chosen logo before upload. The server re-validates the file. */
(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    var input = document.querySelector("[data-logo-input]");
    var preview = document.querySelector("[data-logo-preview]");
    var filename = document.querySelector("[data-logo-filename]");
    if (!input || !preview) return;
    var objectUrl = null;
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) return;
      if (filename) filename.textContent = file.name;
      if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type)) {
        if (window.SEO && window.SEO.toast) window.SEO.toast("Choose a PNG, JPG, WebP or GIF image.", "error");
        return;
      }
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(file);
      preview.textContent = "";
      var img = document.createElement("img");
      img.alt = "New logo preview";
      img.src = objectUrl;
      preview.appendChild(img);
      var remove = document.querySelector("input[name=remove_logo]");
      if (remove) remove.checked = false;
    });
  });
})();
