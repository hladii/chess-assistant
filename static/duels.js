(function () {
  const form = document.getElementById('create-form');
  if (!form) return;

  const queryInput = document.getElementById('opponent_query');
  const hiddenId = document.getElementById('opponent_id');
  const listEl = document.getElementById('opponent-suggestions');
  const pickedEl = document.getElementById('opponent-picked');
  const submitBtn = document.getElementById('create-duel-submit');
  let timer = null;
  let lastQuery = '';

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function setPicked(user) {
    if (!user) {
      hiddenId.value = '';
      if (pickedEl) {
        pickedEl.hidden = true;
        pickedEl.textContent = '';
      }
      if (submitBtn) submitBtn.disabled = true;
      return;
    }
    hiddenId.value = String(user.id);
    if (pickedEl) {
      pickedEl.hidden = false;
      pickedEl.textContent = user.label || user.display_name || user.email;
    }
    if (queryInput) {
      queryInput.value = user.display_name || user.email || '';
    }
    if (submitBtn) submitBtn.disabled = false;
    hideList();
  }

  function hideList() {
    if (!listEl) return;
    listEl.hidden = true;
    listEl.innerHTML = '';
    if (queryInput) queryInput.setAttribute('aria-expanded', 'false');
  }

  function renderUsers(users) {
    if (!listEl) return;
    if (!users.length) {
      listEl.innerHTML = '<li class="autocomplete-empty muted">—</li>';
      listEl.hidden = false;
      if (queryInput) queryInput.setAttribute('aria-expanded', 'true');
      return;
    }
    listEl.innerHTML = users
      .map(function (u) {
        const name = u.display_name || u.email || String(u.id);
        const elo = u.elo_rating != null ? String(u.elo_rating) : '';
        const label = u.label || (name + (elo ? ' · ' + elo : ''));
        return (
          '<li role="option" tabindex="-1" data-id="' +
          escapeHtml(u.id) +
          '" data-label="' +
          escapeHtml(label) +
          '" data-name="' +
          escapeHtml(name) +
          '">' +
          '<span class="autocomplete-name">' +
          escapeHtml(name) +
          '</span>' +
          (elo
            ? '<span class="autocomplete-elo">' + escapeHtml(elo) + '</span>'
            : '') +
          '</li>'
        );
      })
      .join('');
    listEl.hidden = false;
    if (queryInput) queryInput.setAttribute('aria-expanded', 'true');
  }

  async function search(q) {
    lastQuery = q;
    try {
      const response = await fetch('/api/users/search?q=' + encodeURIComponent(q), {
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
      });
      if (!response.ok) return;
      const data = await response.json();
      if (lastQuery !== q) return;
      renderUsers((data && data.users) || []);
    } catch (err) {
      /* ignore network errors while typing */
    }
  }

  if (queryInput) {
    queryInput.addEventListener('input', function () {
      setPicked(null);
      const q = queryInput.value.trim();
      if (timer) clearTimeout(timer);
      if (q.length < 2) {
        hideList();
        return;
      }
      timer = setTimeout(function () {
        search(q);
      }, 200);
    });

    queryInput.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        hideList();
        return;
      }
      if (event.key !== 'Enter' || !listEl || listEl.hidden) return;
      const first = listEl.querySelector('[data-id]');
      if (!first) return;
      event.preventDefault();
      setPicked({
        id: first.getAttribute('data-id'),
        label: first.getAttribute('data-label'),
        display_name: first.getAttribute('data-name'),
      });
    });
  }

  if (listEl) {
    listEl.addEventListener('mousedown', function (event) {
      // Prevent input blur before click selects an option.
      event.preventDefault();
    });
    listEl.addEventListener('click', function (event) {
      const item = event.target.closest('[data-id]');
      if (!item) return;
      setPicked({
        id: item.getAttribute('data-id'),
        label: item.getAttribute('data-label'),
        display_name: item.getAttribute('data-name'),
      });
    });
  }

  document.addEventListener('click', function (event) {
    if (!listEl || listEl.hidden) return;
    const anchor = queryInput && queryInput.closest('.autocomplete-anchor');
    if (anchor && anchor.contains(event.target)) return;
    hideList();
  });

  form.addEventListener('submit', function (event) {
    if (!hiddenId.value) {
      event.preventDefault();
      if (queryInput) queryInput.focus();
    }
  });
})();
