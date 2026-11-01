/* No dependency. Submission state prevents duplicate filter requests without trapping keyboard users. */
document.querySelectorAll('.catalog-filters').forEach(form => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button[type=submit]');
    if (button) { button.textContent = 'Aplicando…'; button.setAttribute('aria-busy', 'true'); }
  });
});
