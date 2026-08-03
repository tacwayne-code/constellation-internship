(function () {
  var KEY = "aftersales_theme";
  function apply() {
    var saved = localStorage.getItem(KEY);
    if (saved !== "light" && saved !== "dark") saved = "light";
    document.documentElement.setAttribute("data-theme", saved);
    window.__aftersalesTheme = saved;
  }
  apply();
  // 用户已显式选择浅色/深色后不再跟随系统切换
  window.__applyAftersalesTheme = apply;
})();