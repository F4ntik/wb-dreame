// HomeUI and its embedded dashboard share an origin, but iframe CSS is isolated.
(() => {
  const panel = document.documentElement;
  const host = window.frameElement?.ownerDocument.documentElement;
  const systemDark = window.matchMedia('(prefers-color-scheme: dark)');
  const palettes = ['light', 'dark', 'bootstrap'];
  const colors = [
    '--primary-color', '--primary-color-hover', '--text-color', '--background-color',
    '--background-accent-color', '--container-background', '--container-background-hover',
    '--border-color', '--input-color', '--input-background', '--input-border-color',
    '--gray-color', '--light-gray-color', '--success-color', '--warn-color',
    '--danger-color', '--button-text-color',
  ];

  function savedTheme() {
    try { return window.localStorage.getItem('theme'); }
    catch { return null; } // Storage may be disabled for this origin.
  }

  function syncTheme() {
    const fromHomeUI = palettes.includes(host?.dataset.theme);
    const name = fromHomeUI ? host.dataset.theme : savedTheme();
    panel.dataset.theme = palettes.includes(name) ? name : (systemDark.matches ? 'dark' : 'light');
    const style = fromHomeUI ? host.ownerDocument.defaultView.getComputedStyle(host) : null;
    for (const color of colors) {
      const value = style?.getPropertyValue(color).trim();
      if (value && CSS.supports('color', value)) panel.style.setProperty(color, value);
      else panel.style.removeProperty(color);
    }
  }

  syncTheme();
  if (host) new MutationObserver(syncTheme).observe(host, {
    attributes: true, attributeFilter: ['data-theme', 'style', 'class'],
  });
  systemDark.addEventListener('change', syncTheme);
  if (!host) window.addEventListener('storage', event => {
    if (event.key === 'theme' || event.key === null) syncTheme();
  });
})();
