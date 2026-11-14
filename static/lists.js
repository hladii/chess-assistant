(function () {
  const createDialog = document.getElementById('create-dialog');
  if (createDialog && typeof createDialog.showModal === 'function') {
    const nameInput = createDialog.querySelector('#name');

    function openCreate() {
      createDialog.showModal();
      if (nameInput) {
        window.setTimeout(function () {
          nameInput.focus();
        }, 0);
      }
    }

    function closeCreate() {
      if (createDialog.open) createDialog.close();
    }

    document.querySelectorAll('#open-create, [data-open-create]').forEach(function (btn) {
      btn.addEventListener('click', openCreate);
    });

    createDialog.querySelectorAll('[data-close-dialog]').forEach(function (btn) {
      btn.addEventListener('click', closeCreate);
    });

    createDialog.addEventListener('click', function (event) {
      if (event.target === createDialog) closeCreate();
    });
  }

  const deleteDialog = document.getElementById('delete-dialog');
  const deleteForm = document.getElementById('delete-form');
  const deleteMessage = document.getElementById('delete-dialog-message');
  const deleteReturnTo = document.getElementById('delete-return-to');

  if (deleteDialog && deleteForm && typeof deleteDialog.showModal === 'function') {
    function closeDelete() {
      if (deleteDialog.open) deleteDialog.close();
    }

    function openDelete(btn) {
      const action = btn.getAttribute('data-action');
      if (!action) return;
      deleteForm.setAttribute('action', action);
      if (deleteReturnTo) {
        deleteReturnTo.value = btn.getAttribute('data-return-to') || deleteReturnTo.value || 'games';
      }
      if (deleteMessage) {
        deleteMessage.textContent = btn.getAttribute('data-confirm') || '';
      }
      deleteDialog.showModal();
      const confirmBtn = deleteForm.querySelector('button[type="submit"]');
      if (confirmBtn) {
        window.setTimeout(function () {
          confirmBtn.focus();
        }, 0);
      }
    }

    document.querySelectorAll('[data-delete-game]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        openDelete(btn);
      });
    });

    deleteDialog.querySelectorAll('[data-close-delete]').forEach(function (btn) {
      btn.addEventListener('click', closeDelete);
    });

    deleteDialog.addEventListener('click', function (event) {
      if (event.target === deleteDialog) closeDelete();
    });
  }

  const resignDialog = document.getElementById('resign-dialog');
  const resignForm = document.getElementById('resign-form');
  const resignMessage = document.getElementById('resign-dialog-message');

  if (resignDialog && resignForm && typeof resignDialog.showModal === 'function') {
    function closeResign() {
      if (resignDialog.open) resignDialog.close();
    }

    function openResign(btn) {
      const action = btn.getAttribute('data-action');
      if (!action) return;
      resignForm.setAttribute('action', action);
      if (resignMessage) {
        resignMessage.textContent = btn.getAttribute('data-confirm') || '';
      }
      resignDialog.showModal();
      const confirmBtn = resignForm.querySelector('button[type="submit"]');
      if (confirmBtn) {
        window.setTimeout(function () {
          confirmBtn.focus();
        }, 0);
      }
    }

    document.querySelectorAll('[data-resign-game]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        openResign(btn);
      });
    });

    resignDialog.querySelectorAll('[data-close-resign]').forEach(function (btn) {
      btn.addEventListener('click', closeResign);
    });

    resignDialog.addEventListener('click', function (event) {
      if (event.target === resignDialog) closeResign();
    });
  }
})();
