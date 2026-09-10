(function () {
  function normalize(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
  }

  function filterSelect(input) {
    var selectId = input.getAttribute("aria-controls");
    var select = selectId ? document.getElementById(selectId) : null;
    if (!select) {
      return;
    }
    var needle = normalize(input.value);
    Array.prototype.forEach.call(select.options, function (option) {
      if (!option.value) {
        option.hidden = false;
        return;
      }
      var hay = normalize(option.getAttribute("data-label") || option.textContent);
      option.hidden = Boolean(needle) && hay.indexOf(needle) === -1;
    });
  }

  document.addEventListener("input", function (event) {
    var input = event.target;
    if (!input || !input.hasAttribute("data-agent-filter")) {
      return;
    }
    filterSelect(input);
  });
})();
