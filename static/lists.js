(function () {
  const dialog = document.getElementById('create-dialog');
  if (!dialog || typeof dialog.showModal !== 'function') return;

  const nameInput = dialog.querySelector('#name');

  function openCreate() {
    dialog.showModal();
    if (nameInput) {
      window.setTimeout(function () {
        nameInput.focus();
      }, 0);
    }
  }

  function closeCreate() {
    if (dialog.open) dialog.close();
  }

  document.querySelectorAll('#open-create, [data-open-create]').forEach(function (btn) {
    btn.addEventListener('click', openCreate);
  });

  dialog.querySelectorAll('[data-close-dialog]').forEach(function (btn) {
    btn.addEventListener('click', closeCreate);
  });

  dialog.addEventListener('click', function (event) {
    if (event.target === dialog) closeCreate();
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && dialog.open) {
      closeCreate();
    }
  });
})();
