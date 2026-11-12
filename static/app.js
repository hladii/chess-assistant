(function () {
  const gameEl = document.getElementById('game');
  if (!gameEl) return;

  const gameId = Number(gameEl.dataset.gameId);
  const isTrainer = gameEl.dataset.mode === 'trainer';
  const csrfToken = (
    gameEl.dataset.csrf ||
    (document.querySelector('meta[name="csrf-token"]') || {}).content ||
    ''
  );
  const boardEl = document.getElementById('board');
  const userSideEl = document.getElementById('user-side');
  const sideToMoveEl = document.getElementById('side-to-move');
  const moveCountEl = document.getElementById('move-count');
  const modeLabelEl = document.getElementById('mode-label');
  const messageEl = document.getElementById('message');
  const errorEl = document.getElementById('error');
  const historyEl = document.getElementById('move-history');
  const moveForm = document.getElementById('move-form');
  const movePromptLabel = document.getElementById('move-prompt-label');
  const sanInput = document.getElementById('san');
  const suggestionsEl = document.getElementById('suggestions');
  const suggestionsWrap = document.getElementById('suggestions-wrap');
  const hintBtn = document.getElementById('hint-btn');
  const undoBtn = document.getElementById('undo-btn');
  const loader = document.getElementById('loader');
  const loaderText = document.getElementById('loader-text');
  const coachBox = document.getElementById('coach-box');
  const coachBadge = document.getElementById('coach-badge');
  const coachText = document.getElementById('coach-text');
  const coachBest = document.getElementById('coach-best');

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

  function parseHistoryRows(historyText) {
    const text = (historyText || '').trim();
    if (!text) return [];
    const tokens = text.split(/\s+/).filter(Boolean);
    const rows = [];
    let current = null;
    tokens.forEach(function (token) {
      if (/^\d+\.$/.test(token)) {
        current = { n: token.replace('.', ''), white: '', black: '' };
        rows.push(current);
        return;
      }
      if (!current) {
        current = { n: String(rows.length + 1), white: '', black: '' };
        rows.push(current);
      }
      if (!current.white) current.white = token;
      else if (!current.black) current.black = token;
    });
    return rows;
  }

  function renderHistory(historyText) {
    if (!historyEl) return;
    const rows = parseHistoryRows(historyText);
    if (!rows.length) {
      historyEl.innerHTML = '<span class="muted">No moves yet.</span>';
      historyEl.dataset.history = '';
      return;
    }
    const body = rows.map(function (row) {
      return (
        '<tr>' +
        '<th scope="row">' + escapeHtml(row.n) + '.</th>' +
        '<td>' + escapeHtml(row.white || '') + '</td>' +
        '<td>' + escapeHtml(row.black || '') + '</td>' +
        '</tr>'
      );
    }).join('');
    historyEl.innerHTML =
      '<table class="history-table">' +
      '<thead><tr><th scope="col">#</th><th scope="col">White</th><th scope="col">Black</th></tr></thead>' +
      '<tbody>' + body + '</tbody></table>';
    historyEl.dataset.history = historyText || '';
    historyEl.scrollTop = historyEl.scrollHeight;
  }

  function renderCoach(coach) {
    if (!coachBox) return;
    if (!coach) {
      coachBox.hidden = true;
      return;
    }
    coachBox.hidden = false;
    const cls = coach.classification || 'best';
    coachBadge.className = 'badge coach-' + cls;
    coachBadge.textContent = cls.charAt(0).toUpperCase() + cls.slice(1);
    coachText.textContent = coach.message || '';
    if (coach.best_move_san && cls !== 'best') {
      coachBest.hidden = false;
      coachBest.className = 'coach-better muted';
      coachBest.textContent = 'Better was ' + coach.best_move_san;
    } else {
      coachBest.hidden = true;
      coachBest.textContent = '';
    }
  }

  function renderSuggestions(state) {
    if (!suggestionsEl) return;
    if (isTrainer || state.mode === 'trainer') {
      if (suggestionsWrap) suggestionsWrap.hidden = true;
      return;
    }
    if (suggestionsWrap) suggestionsWrap.hidden = false;

    const list = state.suggestions || [];
    if (!list.length) {
      suggestionsEl.innerHTML = state.your_turn
        ? '<p class="muted">Press Hint for engine lines.</p>'
        : '<p class="muted">Enter the opponent move, then your turn suggestions appear.</p>';
      return;
    }

    const canSelect = state.your_turn && !state.game_over;
    const items = list.map(function (option) {
      const san = option.first_move || '';
      const disabled = !canSelect || !san ? ' disabled' : '';
      const plies = option.plies && option.plies.length
        ? option.plies
        : (option.moves || []).map(function (move, index) {
            return { san: move, side: index % 2 === 0 ? 'you' : 'opponent' };
          });
      const lineHtml = plies.map(function (ply) {
        const cls = ply.side === 'you' ? 'ply ply-you' : 'ply ply-opp';
        return '<span class="' + cls + '">' + escapeHtml(ply.san) + '</span>';
      }).join('');
      return (
        '<li>' +
        '<button type="button" class="btn suggestion" data-variant="outline" data-san="' + escapeAttr(san) + '"' + disabled + '>' +
        '<span class="line">' + lineHtml + '</span>' +
        '<span class="eval">' + escapeHtml(option.evaluation) + '</span>' +
        '</button>' +
        '</li>'
      );
    }).join('');
    suggestionsEl.innerHTML = '<ol class="suggestion-list">' + items + '</ol>';
  }

  function applyState(state) {
    if (state.board_svg && boardEl) {
      boardEl.innerHTML = state.board_svg;
    }
    if (userSideEl) userSideEl.textContent = state.user_side || '';
    if (sideToMoveEl) sideToMoveEl.textContent = state.side_to_move || '';
    if (moveCountEl) moveCountEl.textContent = String(state.move_count || 0);
    if (modeLabelEl && state.mode) {
      modeLabelEl.textContent = state.mode === 'trainer'
        ? ('Trainer · ' + (state.trainer_elo || ''))
        : 'Assistant';
    }
    setText(messageEl, state.message || '');
    setText(errorEl, state.error || '');
    renderCoach(state.coach);
    renderHistory(state.history || '');

    const trainerMode = isTrainer || state.mode === 'trainer';
    if (movePromptLabel) {
      movePromptLabel.textContent = (state.move_prompt || 'Move') + ' (SAN)';
    }
    if (trainerMode) {
      moveForm.hidden = !!state.game_over || !state.your_turn;
      if (hintBtn) hintBtn.hidden = true;
    } else {
      moveForm.hidden = !!state.game_over;
      if (hintBtn) hintBtn.hidden = false;
    }
    renderSuggestions(state);
    if (!moveForm.hidden && sanInput) {
      sanInput.focus();
    }
  }

  async function apiPost(url, body, loaderMessage) {
    if (busy) return null;
    busy = true;
    setLoader(true, loaderMessage || 'Working…');
    try {
      const payload = Object.assign({ game_id: gameId }, body || {});
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-CSRF-Token': csrfToken,
        },
        body: JSON.stringify(payload),
        credentials: 'same-origin',
      });
      if (response.status === 401) {
        window.location.href = '/login';
        return null;
      }
      const data = await response.json();
      applyState(data);
      if (!response.ok && data.error) {
        setText(errorEl, data.error);
      }
      return data;
    } catch (err) {
      setText(errorEl, 'Request failed. Try again.');
      return null;
    } finally {
      busy = false;
      setLoader(false);
    }
  }

  async function playSan(san, loaderMessage) {
    const trimmed = (san || '').trim();
    if (!trimmed) {
      setText(errorEl, 'Enter a move in SAN, e.g. e4 or Nf6.');
      return;
    }
    const data = await apiPost('/api/move', { san: trimmed }, loaderMessage || 'Playing move…');
    if (data && data.ok) {
      sanInput.value = '';
    }
  }

  // Initial history from server-rendered data attribute.
  renderHistory(historyEl ? historyEl.dataset.history : '');

  if (moveForm) {
    moveForm.addEventListener('submit', function (event) {
      event.preventDefault();
      if (isTrainer) {
        playSan(sanInput.value, 'Checking your move…');
        return;
      }
      const prompt = movePromptLabel.textContent || '';
      const analyzing = prompt.indexOf('Your move') !== -1;
      playSan(
        sanInput.value,
        analyzing ? 'Analyzing position…' : 'Submitting opponent move…'
      );
    });
  }

  if (suggestionsEl) {
    suggestionsEl.addEventListener('click', function (event) {
      const button = event.target.closest('.suggestion');
      if (!button || button.disabled) return;
      const san = button.getAttribute('data-san');
      if (!san) return;
      sanInput.value = san;
      playSan(san, isTrainer ? 'Checking your move…' : 'Playing selected move…');
    });
  }

  if (hintBtn) {
    hintBtn.addEventListener('click', function () {
      apiPost('/api/hint', null, 'Analyzing…');
    });
  }

  if (undoBtn) {
    undoBtn.addEventListener('click', function () {
      apiPost('/api/undo', null, 'Undoing…');
    });
  }
})();
