(() => {
  const root = document.querySelector('[data-home-slider]');
  if (!root) return;
  const slides = Array.from(root.querySelectorAll('[data-home-slide]'));
  const controls = root.querySelector('[data-home-controls]');
  const indicators = Array.from(root.querySelectorAll('[data-home-index]'));
  const status = root.querySelector('[data-home-status]');
  let current = 0;
  let gesture = null;

  const show = (index, announce = true) => {
    const next = (index + slides.length) % slides.length;
    if (slides[current].contains(document.activeElement) && next !== current) {
      root.focus({ preventScroll: true });
    }
    slides.forEach((slide, i) => {
      const active = i === next;
      slide.style.setProperty('--slide-offset', i - next);
      slide.classList.toggle('is-active', active);
      slide.inert = !active;
      slide.setAttribute('aria-hidden', String(!active));
      if (!active) slide.querySelectorAll('video').forEach((video) => video.pause());
    });
    indicators.forEach((button, i) => {
      if (i === next) button.setAttribute('aria-current', 'true');
      else button.removeAttribute('aria-current');
    });
    current = next;
    if (announce) status.textContent = `Diapositiva ${next + 1} de ${slides.length}: ${slides[next].dataset.title}`;
  };

  root.querySelector('[data-home-previous]').addEventListener('click', () => show(current - 1));
  root.querySelector('[data-home-next]').addEventListener('click', () => show(current + 1));
  indicators.forEach((button) => button.addEventListener('click', () => show(Number(button.dataset.homeIndex))));
  root.addEventListener('keydown', (event) => {
    if (event.target.closest('[data-home-video], input, textarea, select, [contenteditable]')) return;
    const destinations = { ArrowLeft: current - 1, ArrowRight: current + 1, Home: 0, End: slides.length - 1 };
    if (Object.hasOwn(destinations, event.key)) {
      event.preventDefault();
      show(destinations[event.key]);
    }
  });

  const gestureExcluded = (target) => target.closest('a, button, input, textarea, select, [contenteditable], [data-home-video]');
  root.addEventListener('pointerdown', (event) => {
    if (!event.isPrimary || event.pointerType === 'mouse' || gestureExcluded(event.target)) return;
    gesture = { id: event.pointerId, x: event.clientX, y: event.clientY };
  });
  root.addEventListener('pointerup', (event) => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const dx = event.clientX - gesture.x;
    const dy = event.clientY - gesture.y;
    gesture = null;
    if (!gestureExcluded(event.target) && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) {
      show(current + (dx < 0 ? 1 : -1));
    }
  });
  root.addEventListener('pointercancel', () => { gesture = null; });

  show(0, false);
  root.classList.add('is-ready');
  controls.hidden = false;
})();
