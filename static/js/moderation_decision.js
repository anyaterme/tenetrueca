(() => {
  'use strict';

  document.querySelectorAll('form[data-decision-drafts]').forEach((form) => {
    const source = document.getElementById(form.dataset.decisionDrafts);
    const notes = form.querySelector('textarea[name="notes"]');
    const options = Array.from(form.querySelectorAll('input[name="decision"]'));
    if (!source || !notes || !options.length) return;

    const proposals = JSON.parse(source.textContent);
    const drafts = new Map();
    let current = options.find((option) => option.checked)?.value;
    // The rendered form may contain a human draft returned after validation.
    if (current) drafts.set(current, notes.value);
    let unassignedEdited = !current && notes.value !== '';
    notes.addEventListener('input', () => {
      if (current) drafts.set(current, notes.value);
      else unassignedEdited = true;
    });

    options.forEach((option) => {
      option.addEventListener('change', () => {
        if (!option.checked || option.value === current) return;
        if (current) drafts.set(current, notes.value);
        // Protect text entered before a choice, including deliberately cleared text.
        if (!current && unassignedEdited) drafts.set(option.value, notes.value);
        current = option.value;
        if (!drafts.has(current)) drafts.set(current, proposals[current] ?? '');
        notes.value = drafts.get(current);
      });
    });

    const description = notes.getAttribute('aria-describedby');
    notes.setAttribute('aria-describedby', [description, 'decision-draft-help'].filter(Boolean).join(' '));
  });
})();
