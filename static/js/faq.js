(() => {
  const root = document.querySelector('[data-faq]');
  if (!root) return;
  const search = root.querySelector('[data-faq-search]');
  if (!search) return;
  const input = root.querySelector('[data-faq-input]');
  const count = root.querySelector('[data-faq-count]');
  const empty = root.querySelector('[data-faq-no-results]');
  const normalize = (text) => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es').trim();
  const items = Array.from(root.querySelectorAll('[data-faq-item]'), (element) => ({
    element,
    text: normalize(element.textContent),
  }));

  const filter = () => {
    const terms = normalize(input.value).split(/\s+/).filter(Boolean);
    let visible = 0;
    for (const item of items) {
      const matches = terms.every((term) => item.text.includes(term));
      item.element.hidden = !matches;
      if (matches) visible += 1;
      else item.element.open = false;
    }
    empty.hidden = visible > 0;
    count.textContent = `${visible} ${visible === 1 ? 'pregunta encontrada' : 'preguntas encontradas'}`;
  };

  search.hidden = false;
  input.addEventListener('input', filter);
  filter();
})();
