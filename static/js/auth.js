document.querySelectorAll('[data-password-toggle]').forEach((button) => {
  const input = document.getElementById(button.dataset.passwordToggle);

  if (!input) {
    return;
  }

  button.addEventListener('click', () => {
    const passwordIsVisible = input.type === 'text';
    input.type = passwordIsVisible ? 'password' : 'text';
    button.textContent = passwordIsVisible ? 'Mostrar' : 'Ocultar';
    button.setAttribute('aria-pressed', String(!passwordIsVisible));
    input.focus();
  });
});
