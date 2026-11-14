import { Chess } from './vendor/chess.bundle.js';
import { Chessground } from './vendor/chessground/chessground.min.js';
import { initGameSheet } from './game-sheet.js';

(function () {
  const gameEl = document.getElementById('game');
  if (!gameEl) return;

  const gameId = Number(gameEl.dataset.gameId);

  const I18N = window.I18N || {};
  function t(key, vars) {
    let s = I18N[key] != null ? I18N[key] : key;
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        s = s.replace('%(' + k + ')s', vars[k]).replace('%(' + k + ')s', vars[k]);
      });
    }
    return s;
  }

  const isTrainer = gameEl.dataset.mode === 'trainer';
  const isDuel = gameEl.dataset.mode === 'duel';
  const csrfToken = (
    gameEl.dataset.csrf ||
    (document.querySelector('meta[name="csrf-token"]') || {}).content ||
    ''
  );
  const boardEl = document.getElementById('board');
  const promoPicker = document.getElementById('promo-picker');
  const userSideEl = document.getElementById('user-side');
  const sideToMoveEl = document.getElementById('side-to-move');
  const moveCountEl = document.getElementById('move-count');
  const accuracyChip = document.getElementById('accuracy-chip');
  const accuracyValueEl = document.getElementById('accuracy-value');
  const eloValueEl = document.getElementById('elo-value');
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
  const resignBtn = document.getElementById('resign-btn');
  const analyzeHintBtn = document.getElementById('analyze-hint-btn');
  const duelLiveBar = document.getElementById('duel-live-bar');
  const duelAnalyzeBar = document.getElementById('duel-analyze-bar');
  const duelWaitText = document.getElementById('duel-wait-text');
  const loader = document.getElementById('loader');
  const loaderText = document.getElementById('loader-text');
  const coachBox = document.getElementById('coach-box');
  const coachBadge = document.getElementById('coach-badge');
  const coachText = document.getElementById('coach-text');
  const coachBest = document.getElementById('coach-best');

  let busy = false;
  let chess = new Chess(gameEl.dataset.fen || undefined);
  let ground = null;
  let pendingPromo = null;
  let pollTimer = null;

  function setLoader(visible, text) {
    if (!loader) return;
    loader.hidden = !visible;
    if (text) loaderText.textContent = text;
    document.body.classList.toggle('is-loading', visible);
  }

  function setText(el, value) {
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

  function renderHistory(historyText, reviewPly) {
    if (!historyEl) return;
    const rows = parseHistoryRows(historyText);
    if (!rows.length) {
      historyEl.innerHTML = '<span class="muted">' + t('noMovesYet') + '</span>';
      historyEl.dataset.history = '';
      return;
    }
    const reviewable = isDuel && gameEl.dataset.gameOver === '1';
    const activePly = reviewPly == null ? null : Number(reviewPly);
    const body = rows.map(function (row) {
      const whitePly = (Number(row.n) - 1) * 2 + 1;
      const blackPly = whitePly + 1;
      function cell(san, ply) {
        if (!san) return '<td></td>';
        const active = activePly === ply ? ' history-ply-active' : '';
        if (!reviewable) {
          return '<td>' + escapeHtml(san) + '</td>';
        }
        return (
          '<td><button type="button" class="history-ply' + active + '" data-ply="' +
          ply +
          '">' +
          escapeHtml(san) +
          '</button></td>'
        );
      }
      return (
        '<tr>' +
        '<th scope="row">' + escapeHtml(row.n) + '.</th>' +
        cell(row.white, whitePly) +
        cell(row.black, blackPly) +
        '</tr>'
      );
    }).join('');
    historyEl.innerHTML =
      '<table class="history-table">' +
      '<thead><tr><th scope="col">' + t('colNum') + '</th><th scope="col">' + t('colWhite') + '</th><th scope="col">' + t('colBlack') + '</th></tr></thead>' +
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
    const classKey = 'classification_' + cls;
    coachBadge.textContent = I18N[classKey] || (cls.charAt(0).toUpperCase() + cls.slice(1));
    coachText.textContent = coach.message || '';
    if (coach.best_move_san && cls !== 'best') {
      coachBest.hidden = false;
      coachBest.className = 'coach-better muted';
      coachBest.textContent = t('betterWas', { move: coach.best_move_san });
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
    const duelMode = isDuel || state.mode === 'duel';
    if (duelMode && !state.game_over) {
      if (suggestionsWrap) suggestionsWrap.hidden = true;
      return;
    }
    if (suggestionsWrap) suggestionsWrap.hidden = false;

    const list = state.suggestions || [];
    if (!list.length) {
      if (duelMode && state.game_over) {
        suggestionsEl.innerHTML = '<p class="muted">' + t('pressAnalyze') + '</p>';
      } else {
        suggestionsEl.innerHTML = state.your_turn
          ? '<p class="muted">' + t('pressHint') + '</p>'
          : '<p class="muted">' + t('enterOpponent') + '</p>';
      }
      return;
    }

    const canSelect = state.your_turn && !state.game_over && !duelMode;
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

  function parseLastMove(uci) {
    if (!uci || uci.length < 4) return undefined;
    return [uci.slice(0, 2), uci.slice(2, 4)];
  }

  function turnColor() {
    return chess.turn() === 'w' ? 'white' : 'black';
  }

  function toDests() {
    const dests = new Map();
    chess.moves({ verbose: true }).forEach(function (move) {
      const from = move.from;
      if (!dests.has(from)) dests.set(from, []);
      dests.get(from).push(move.to);
    });
    return dests;
  }

  function canInteract(state) {
    if (busy || pendingPromo) return false;
    if (state.game_over) return false;
    const trainerMode = isTrainer || state.mode === 'trainer';
    const duelMode = isDuel || state.mode === 'duel';
    if (trainerMode || duelMode) return !!state.your_turn;
    return true;
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPolling() {
    if (!isDuel || pollTimer) return;
    pollTimer = setInterval(function () {
      if (busy) return;
      if (gameEl.dataset.gameOver === '1') {
        stopPolling();
        return;
      }
      if (gameEl.dataset.yourTurn === '1') return;
      pollState();
    }, 2000);
  }

  async function pollState() {
    try {
      const response = await fetch('/api/state?game_id=' + encodeURIComponent(gameId), {
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
      });
      if (response.status === 401) {
        window.location.href = '/login';
        return;
      }
      if (!response.ok) return;
      const data = await response.json();
      if (!data || !data.ok) return;
      applyState(data);
    } catch (err) {
      /* ignore transient poll errors */
    }
  }

  function hidePromo() {
    pendingPromo = null;
    if (promoPicker) promoPicker.hidden = true;
  }

  function showPromo(orig, dest, color) {
    pendingPromo = { orig: orig, dest: dest, color: color };
    if (!promoPicker) return;
    promoPicker.hidden = false;
    promoPicker.dataset.color = color;
  }

  function isPromotion(orig, dest) {
    const piece = chess.get(orig);
    if (!piece || piece.type !== 'p') return false;
    const rank = dest[1];
    return rank === '8' || rank === '1';
  }

  function submitBoardMove(orig, dest, promotion) {
    let result;
    try {
      result = chess.move({
        from: orig,
        to: dest,
        promotion: promotion || undefined,
      });
    } catch (err) {
      syncBoardFromChess();
      setText(errorEl, t('illegalMove'));
      return;
    }
    if (!result) {
      syncBoardFromChess();
      setText(errorEl, t('illegalMove'));
      return;
    }
    const san = result.san;
    // Keep client position unchanged until the server confirms (and may reply in trainer).
    chess.undo();
    syncBoardFromChess();
    if (sanInput) sanInput.value = san;
    const trainerMode = isTrainer;
    const duelMode = isDuel;
    const analyzing = trainerMode || duelMode ? false : (gameEl.dataset.yourTurn === '1');
    playSan(
      san,
      trainerMode
        ? t('checkingMove')
        : duelMode
          ? t('playingMove')
          : (analyzing ? t('analyzingPosition') : t('submittingOpponent'))
    );
  }

  function onAfterMove(orig, dest) {
    if (busy) {
      syncBoardFromChess();
      return;
    }
    if (isPromotion(orig, dest)) {
      const color = turnColor();
      // Revert visual until promotion chosen; Chessground already moved the piece.
      syncBoardFromChess();
      showPromo(orig, dest, color);
      return;
    }
    submitBoardMove(orig, dest);
  }

  function boardConfig(state) {
    const interactive = canInteract(state);
    const color = turnColor();
    return {
      fen: chess.fen(),
      orientation: state.orientation || gameEl.dataset.orientation || 'white',
      turnColor: color,
      lastMove: parseLastMove(state.last_move || gameEl.dataset.lastMove || ''),
      check: chess.isCheck() ? color : false,
      viewOnly: !interactive,
      coordinates: true,
      coordinatesOnSquares: false,
      ranksPosition: 'left',
      draggable: {
        enabled: interactive,
      },
      selectable: {
        enabled: interactive,
      },
      movable: {
        free: false,
        color: interactive ? color : undefined,
        dests: interactive ? toDests() : new Map(),
        showDests: true,
        events: {
          after: onAfterMove,
        },
      },
      highlight: {
        lastMove: true,
        check: true,
      },
      animation: {
        enabled: true,
        duration: 160,
      },
    };
  }

  function syncBoardFromChess(stateOverrides) {
    if (!ground) return;
    const state = Object.assign({
      orientation: gameEl.dataset.orientation || 'white',
      last_move: gameEl.dataset.lastMove || '',
      your_turn: gameEl.dataset.yourTurn === '1',
      game_over: gameEl.dataset.gameOver === '1',
      mode: gameEl.dataset.mode || 'assistant',
    }, stateOverrides || {});
    ground.set(boardConfig(state));
  }

  function initBoard() {
    if (!boardEl) return;
    const initial = {
      fen: gameEl.dataset.fen || chess.fen(),
      orientation: gameEl.dataset.orientation || 'white',
      last_move: gameEl.dataset.lastMove || '',
      your_turn: gameEl.dataset.yourTurn === '1',
      game_over: gameEl.dataset.gameOver === '1',
      mode: gameEl.dataset.mode || 'assistant',
    };
    try {
      chess = new Chess(initial.fen);
    } catch (err) {
      chess = new Chess();
    }
    ground = Chessground(boardEl, boardConfig(initial));
  }

  function applyState(state) {
    if (state.fen) {
      try {
        chess.load(state.fen);
      } catch (err) {
        /* keep previous position */
      }
      gameEl.dataset.fen = state.fen;
    }
    if (state.orientation) gameEl.dataset.orientation = state.orientation;
    if (state.last_move != null) gameEl.dataset.lastMove = state.last_move || '';
    gameEl.dataset.yourTurn = state.your_turn ? '1' : '0';
    gameEl.dataset.gameOver = state.game_over ? '1' : '0';
    if (state.mode) gameEl.dataset.mode = state.mode;
    if (state.side_to_move) gameEl.dataset.sideToMove = state.side_to_move;

    hidePromo();
    syncBoardFromChess(state);

    if (userSideEl) userSideEl.textContent = state.user_side || '';
    if (sideToMoveEl) sideToMoveEl.textContent = state.side_to_move || '';
    const sideCompact = document.getElementById('side-to-move-compact');
    if (sideCompact) sideCompact.textContent = state.side_to_move || '';
    if (moveCountEl) moveCountEl.textContent = String(state.move_count || 0);
    if (accuracyValueEl) {
      accuracyValueEl.textContent =
        state.accuracy == null ? '—' : String(state.accuracy) + '%';
    }
    const accuracyCompact = document.getElementById('accuracy-value-compact');
    if (accuracyCompact) {
      accuracyCompact.textContent =
        state.accuracy == null ? '—' : String(state.accuracy) + '%';
    }
    if (modeLabelEl && state.mode) {
      if (state.mode === 'trainer') {
        modeLabelEl.textContent = t('trainer') + ' · ' + (state.trainer_elo || '');
      } else if (state.mode === 'duel') {
        modeLabelEl.textContent = t('duel');
      } else {
        modeLabelEl.textContent = t('assistant');
      }
    }
    if (eloValueEl) {
      let eloText = state.your_elo != null ? String(state.your_elo) : '—';
      if (state.your_elo_delta != null) {
        const d = state.your_elo_delta;
        eloText += ' (' + (d > 0 ? '+' : '') + d + ')';
      }
      eloValueEl.textContent = eloText;
    }
    const eloCompact = document.getElementById('elo-value-compact');
    if (eloCompact) {
      eloCompact.textContent = state.your_elo != null ? String(state.your_elo) : '—';
    }
    setText(messageEl, state.message || '');
    setText(errorEl, state.error || '');
    renderCoach(state.coach);
    renderHistory(state.history || '', state.review_ply);

    const trainerMode = isTrainer || state.mode === 'trainer';
    const duelMode = isDuel || state.mode === 'duel';
    if (movePromptLabel) {
      movePromptLabel.textContent = (state.move_prompt || t('move')) + ' ' + t('sanSuffix');
    }
    if (trainerMode || duelMode) {
      moveForm.hidden = !!state.game_over || !state.your_turn;
      if (hintBtn) hintBtn.hidden = true;
      if (undoBtn) undoBtn.hidden = duelMode;
    } else {
      moveForm.hidden = !!state.game_over;
      if (hintBtn) hintBtn.hidden = false;
      if (undoBtn) undoBtn.hidden = false;
    }
    if (duelLiveBar) {
      duelLiveBar.hidden = !duelMode || !!state.game_over;
    }
    if (duelWaitText) {
      duelWaitText.hidden = !duelMode || !!state.game_over || !!state.your_turn;
    }
    if (resignBtn) {
      resignBtn.hidden = !duelMode || !!state.game_over || state.can_resign === false;
    }
    if (duelAnalyzeBar) {
      duelAnalyzeBar.hidden = !duelMode || !state.game_over;
    }
    const iconResign = document.getElementById('icon-resign-btn');
    if (iconResign) {
      iconResign.hidden = !duelMode || !!state.game_over || state.can_resign === false;
    }
    const iconAnalyze = document.getElementById('icon-analyze-btn');
    if (iconAnalyze) {
      iconAnalyze.hidden = !duelMode || !state.game_over;
    }
    renderSuggestions(state);
    if (duelMode) {
      if (state.game_over) stopPolling();
      else startPolling();
    }
    if (!moveForm.hidden && sanInput) {
      sanInput.focus();
    }
  }

  async function apiPost(url, body, loaderMessage) {
    if (busy) return null;
    busy = true;
    syncBoardFromChess({
      orientation: gameEl.dataset.orientation || 'white',
      last_move: gameEl.dataset.lastMove || '',
      your_turn: gameEl.dataset.yourTurn === '1',
      game_over: gameEl.dataset.gameOver === '1',
      mode: gameEl.dataset.mode || 'assistant',
    });
    setLoader(true, loaderMessage || t('working'));
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
      setText(errorEl, t('requestFailed'));
      syncBoardFromChess();
      return null;
    } finally {
      busy = false;
      setLoader(false);
      syncBoardFromChess({
        orientation: gameEl.dataset.orientation || 'white',
        last_move: gameEl.dataset.lastMove || '',
        your_turn: gameEl.dataset.yourTurn === '1',
        game_over: gameEl.dataset.gameOver === '1',
        mode: gameEl.dataset.mode || 'assistant',
      });
    }
  }

  async function playSan(san, loaderMessage) {
    const trimmed = (san || '').trim();
    if (!trimmed) {
      setText(errorEl, t('enterSan'));
      return;
    }
    const data = await apiPost('/api/move', { san: trimmed }, loaderMessage || t('playingMove'));
    if (data && data.ok) {
      if (sanInput) sanInput.value = '';
    } else {
      // Server rejected — reload position from dataset fen if present.
      if (gameEl.dataset.fen) {
        try {
          chess.load(gameEl.dataset.fen);
        } catch (err) {
          /* ignore */
        }
      }
      syncBoardFromChess();
    }
  }

  if (promoPicker) {
    promoPicker.addEventListener('click', function (event) {
      const btn = event.target.closest('.promo-btn');
      if (!btn || !pendingPromo) return;
      const piece = btn.getAttribute('data-piece');
      const { orig, dest } = pendingPromo;
      hidePromo();
      submitBoardMove(orig, dest, piece);
    });
  }

  // Initial history from server-rendered data attribute.
  renderHistory(historyEl ? historyEl.dataset.history : '', null);
  initBoard();

  if (historyEl) {
    historyEl.addEventListener('click', function (event) {
      const plyBtn = event.target.closest('[data-ply]');
      if (!plyBtn) return;
      if (!isDuel || gameEl.dataset.gameOver !== '1') return;
      const ply = Number(plyBtn.getAttribute('data-ply'));
      if (!Number.isFinite(ply) || ply < 0) return;
      apiPost('/api/hint', { ply: ply }, t('analyzing'));
    });
  }

  if (moveForm) {
    moveForm.addEventListener('submit', function (event) {
      event.preventDefault();
      if (isTrainer) {
        playSan(sanInput.value, t('checkingMove'));
        return;
      }
      if (isDuel) {
        playSan(sanInput.value, t('playingMove'));
        return;
      }
      const analyzing = gameEl.dataset.yourTurn === '1';
      playSan(
        sanInput.value,
        analyzing ? t('analyzingPosition') : t('submittingOpponent')
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
      playSan(san, isTrainer ? t('checkingMove') : t('playingSelected'));
    });
  }

  if (hintBtn) {
    hintBtn.addEventListener('click', function () {
      apiPost('/api/hint', null, t('analyzing'));
    });
  }

  if (analyzeHintBtn) {
    analyzeHintBtn.addEventListener('click', function () {
      apiPost('/api/hint', null, t('analyzing'));
    });
  }

  if (undoBtn) {
    undoBtn.addEventListener('click', function () {
      apiPost('/api/undo', null, t('undoing'));
    });
  }

  if (resignBtn) {
    resignBtn.addEventListener('click', function () {
      if (!window.confirm(t('confirmResign'))) return;
      apiPost('/api/resign', null, t('resigning'));
    });
  }

  if (isDuel) {
    startPolling();
  }

  initGameSheet({
    root: gameEl,
    getGround: function () {
      return ground;
    },
  });
})();
