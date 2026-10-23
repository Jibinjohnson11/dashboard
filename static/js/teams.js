/* Team form: campaign assignments only apply to team members. */
(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    var role = document.querySelector("select[name=role]");
    var campaignField = document.querySelector("[data-campaign-field]");
    if (!role || !campaignField) return;
    function apply() { campaignField.hidden = role.value !== "team_member"; }
    role.addEventListener("change", apply);
    apply();
  });
})();
