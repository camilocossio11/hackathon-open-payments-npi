// Abbo · ayudas visuales compartidas (solo presentación: no llama a la API).
(function () {
  const KEY = 'abbo:theme';
  const root = document.documentElement;
  try { const t = localStorage.getItem(KEY); if (t) root.dataset.theme = t; } catch (e) {}

  const isDark = () => root.dataset.theme
    ? root.dataset.theme === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;

  const SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  const MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

  window.AbboUI = {
    // Botón de tema claro/oscuro
    themeToggle(btn) {
      const paint = () => {
        btn.innerHTML = isDark() ? SUN : MOON;
        btn.setAttribute('aria-label', isDark() ? 'Usar tema claro' : 'Usar tema oscuro');
      };
      btn.addEventListener('click', () => {
        root.dataset.theme = isDark() ? 'light' : 'dark';
        try { localStorage.setItem(KEY, root.dataset.theme); } catch (e) {}
        paint();
      });
      paint();
    },
    // Pinta el relleno del deslizador hasta el pulgar
    fill(range) {
      const min = Number(range.min) || 0, max = Number(range.max) || 100;
      const p = Math.max(0, Math.min(100, ((Number(range.value) - min) / (max - min)) * 100));
      range.style.setProperty('--p', p + '%');
    },
    // Une un deslizador con su campo numérico: mover uno actualiza el otro y dispara «input» en el campo
    link(range, number) {
      const fromNumber = () => { if (number.value !== '') range.value = number.value; this.fill(range); };
      range.addEventListener('input', () => {
        number.value = range.value;
        number.dispatchEvent(new Event('input', { bubbles: true }));
        this.fill(range);
      });
      number.addEventListener('input', fromNumber);
      fromNumber();
    },
    // Anillo SVG: circle.fg con r=50
    ring(svg, pct) {
      const fg = svg.querySelector('.fg');
      const c = 2 * Math.PI * 50;
      fg.setAttribute('stroke-dasharray', c.toFixed(1));
      fg.setAttribute('stroke-dashoffset', (c * (1 - Math.max(0, Math.min(100, pct)) / 100)).toFixed(1));
      const label = svg.querySelector('.rv');
      if (label) label.textContent = Math.round(pct) + '%';
      svg.setAttribute('aria-label', `Pagado ${Math.round(pct)} %`);
    },
  };
})();
