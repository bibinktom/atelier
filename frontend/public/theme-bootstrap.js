// Pre-hydration theme bootstrap. Loaded synchronously in <head> so the page
// never flashes the wrong theme between first byte and React hydration.
// Reads localStorage('atelier-theme') in {"light","dark"} else falls back to
// the OS prefers-color-scheme. ThemeToggle writes the same key.
(function () {
  try {
    var stored = localStorage.getItem('atelier-theme');
    var sysDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    var dark = stored === 'dark' || (stored !== 'light' && sysDark);
    if (dark) document.documentElement.classList.add('dark');
  } catch (e) { /* localStorage blocked — leave default theme */ }
})();
