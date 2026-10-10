(function () {
  const boardEl = document.getElementById('board');
  const userSideEl = document.getElementById('user-side');
  const sideToMoveEl = document.getElementById('side-to-move');
  const moveCountEl = document.getElementById('move-count');
  const messageEl = document.getElementById('message');
  const errorEl = document.getElementById('error');
  const historyEl = document.getElementById('move-history');
  const moveForm = document.getElementById('move-form');
  const movePromptLabel = document.getElementById('move-prompt-label');
  const sanInput = document.getElementById('san');
  const suggestionsEl = document.getElementById('suggestions');
  const loader = document.getElementById('loader');
  const loaderText = document.getElementById('loader-text');
  const hintBtn = document.getElementById('hint-btn');
  const undoBtn = document.getElementById('undo-btn');

  let busy = false;

  function setLoader(visible, text) {
    if (!loader) return;
    loader.hidden = !visible;
    if (text) loaderText.textContent = text;
    document.body.classList.toggle('is-loading', visible);
  }

  function setText(el, value, isError) {
    if (!el) return;
    const text = value || '';
    el.textContent = text;
    el.hidden = !text;
    if (isError != null) {
      el.classList.toggle('error', isError);
      el.classList.toggle('message', !isError);
    }
  }

  function renderSuggestions(state) {
    const list = state.suggestions || [];
    if (!list.length) {
      suggestionsEl.innerHTML = state.your_turn
        ? '<p class="muted">No lines yet — press Hint.</p>'
        : '<p class="muted">Waiting for opponent move.</p>';
      return;
    }

    const canSelect = state.your_turn && !state.game_over;
    const items = list.map(function (option) {
      const san = option.first_move || '';
      const disabled = !canSelect || !san ? ' disabled' : '';
      return (
        '<li>' +
        '<button type="button" class="suggestion" data-san="' + escapeAttr(san) + '"' + disabled + '>' +
        '<span class="line">' + escapeHtml(option.line) + '</span>' +
        '<span class="eval">' + escapeHtml(option.evaluation) + '</span>' +
        '</button>' +
        '</li>'
      );
    }).join('');
    suggestionsEl.innerHTML = '<ol class="suggestion-list">' + items + '</ol>';
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/'/g, '&#39;');
  }

  function applyState(state) {
    if (state.board_svg) {
      boardEl.innerHTML = state.board_svg;
    }
    userSideEl.textContent = state.user_side || '';
    sideToMoveEl.textContent = state.side_to_move || '';
    moveCountEl.textContent = String(state.move_count || 0);
    setText(messageEl, state.message || '', false);
    setText(errorEl, state.error || '', true);

    if (state.history) {
      historyEl.textContent = state.history;
      historyEl.classList.remove('muted');
    } else {
      historyEl.innerHTML = '<span class="muted">No moves yet.</span>';
    }

    movePromptLabel.textContent = (state.move_prompt || 'Move') + ' (SAN)';
    moveForm.hidden = !!state.game_over;
    renderSuggestions(state);
    sanInput.focus();
  }

  async function apiPost(url, body, loaderMessage) {
    if (busy) return null;
    busy = true;
    setLoader(true, loaderMessage || 'Working…');
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: body ? JSON.stringify(body) : '{}',
      });
      const data = await response.json();
      applyState(data);
      if (!response.ok && data.error) {
        setText(errorEl, data.error, true);
      }
      return data;
    } catch (err) {
      setText(errorEl, 'Request failed. Try again.', true);
      return null;
    } finally {
      busy = false;
      setLoader(false);
    }
  }

  async function playSan(san, loaderMessage) {
    const trimmed = (san || '').trim();
    if (!trimmed) {
      setText(errorEl, 'Enter a move in SAN, e.g. e4 or Nf6.', true);
      return;
    }
    const data = await apiPost('/api/move', { san: trimmed }, loaderMessage || 'Playing move…');
    if (data && data.ok) {
      sanInput.value = '';
    }
  }

  moveForm.addEventListener('submit', function (event) {
    event.preventDefault();
    const prompt = movePromptLabel.textContent || '';
    const analyzing = prompt.indexOf('Your move') !== -1;
    playSan(
      sanInput.value,
      analyzing ? 'Analyzing position…' : 'Submitting opponent move…'
    );
  });

  suggestionsEl.addEventListener('click', function (event) {
    const button = event.target.closest('.suggestion');
    if (!button || button.disabled) return;
    const san = button.getAttribute('data-san');
    if (!san) return;
    sanInput.value = san;
    playSan(san, 'Playing selected move…');
  });

  hintBtn.addEventListener('click', function () {
    apiPost('/api/hint', null, 'Analyzing…');
  });

  undoBtn.addEventListener('click', function () {
    apiPost('/api/undo', null, 'Undoing…');
  });
})();
