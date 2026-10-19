(() => {
  const header = document.querySelector('[data-site-header]');
  if (!header) return;
  const toggle = header.querySelector('[data-site-menu-toggle]');
  const nav = header.querySelector('[data-site-nav]');
  if (!toggle || !nav) return;

  const mobile = window.matchMedia('(max-width: 768px)');
  let expanded = false;

  const setExpanded = (open, restoreFocus = false) => {
    expanded = mobile.matches && open;
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.setAttribute('aria-label', expanded ? 'Cerrar menú' : 'Abrir menú');
    nav.hidden = mobile.matches && !expanded;
    if (restoreFocus) toggle.focus();
  };

  const updateLayout = () => {
    const focusInNav = nav.contains(document.activeElement);
    const focusOnToggle = document.activeElement === toggle;
    toggle.hidden = !mobile.matches;
    setExpanded(false);
    if (mobile.matches && focusInNav) toggle.focus();
    else if (!mobile.matches && focusOnToggle) nav.querySelector('a')?.focus();
  };

  toggle.addEventListener('click', () => setExpanded(!expanded));
  nav.addEventListener('click', (event) => {
    if (mobile.matches && event.target.closest('a')) setExpanded(false, true);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && expanded) {
      event.preventDefault();
      setExpanded(false, true);
    }
  });
  mobile.addEventListener('change', updateLayout);
  updateLayout();
})();
