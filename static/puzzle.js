import { Chess } from './vendor/chess.bundle.js';
import { Chessground } from './vendor/chessground/chessground.min.js';
import { initGameSheet } from './game-sheet.js';

(function () {
  const root = document.getElementById('puzzle');
  if (!root) return;

  window.I18N = Object.assign({}, window.I18N || {}, {
    sheetMove: (window.I18N && window.I18N.sheetMove) || 'Move',
    sheetHistory: (window.I18N && window.I18N.sheetHistory) || 'Move history',
  });

  const I18N = window.I18N || {};
  function t(key) {
    return I18N[key] != null ? I18N[key] : key;
  }

  const csrfToken =
    root.dataset.csrf ||
    (document.querySelector('meta[name="csrf-token"]') || {}).content ||
    '';

  const boardEl = document.getElementById('board');
  const promoPicker = document.getElementById('promo-picker');
  const messageEl = document.getElementById('message');
  const errorEl = document.getElementById('error');
  const historyEl = document.getElementById('move-history');
  const moveForm = document.getElementById('move-form');
  const sanInput = document.getElementById('san');
  const progressEl = document.getElementById('progress');
  const statusLabelEl = document.getElementById('status-label');
  const retryBar = document.getElementById('retry-bar');
  const retryBtn = document.getElementById('retry-btn');
  const loader = document.getElementById('loader');
  const loaderText = document.getElementById('loader-text');

  let busy = false;
  let chess = new Chess(root.dataset.fen || undefined);
  let ground = null;
  let pendingPromo = null;
  let attemptId = Number(root.dataset.attemptId);
  const puzzleId = Number(root.dataset.puzzleId);
  let finished = root.dataset.finished === '1';

  function setLoader(visible, text) {
    if (!loader) return;
    loader.hidden = !visible;
    if (text && loaderText) loaderText.textContent = text;
    document.body.classList.toggle('is-loading', visible);
  }

  function setText(el, value) {
    if (!el) return;
    const text = value || '';
    el.textContent = text;
    el.hidden = !text;
  }

  function destsFromChess() {
    const dests = new Map();
    if (finished) return dests;
    chess.board().forEach(function (row) {
      row.forEach(function (piece) {
        if (!piece) return;
      });
    });
    const moves = chess.moves({ verbose: true });
    moves.forEach(function (m) {
      if (!dests.has(m.from)) dests.set(m.from, []);
      dests.get(m.from).push(m.to);
    });
    return dests;
  }

  function lastMoveArray(uci) {
    if (!uci || uci.length < 4) return undefined;
    return [uci.slice(0, 2), uci.slice(2, 4)];
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function renderMoveTicker(historyText) {
    const ticker = document.getElementById('move-ticker');
    if (!ticker) return;
    const text = (historyText || '').trim();
    if (!text) {
      ticker.innerHTML = '<span class="move-ticker-empty">' + escapeHtml(t('noMovesYet')) + '</span>';
      return;
    }
    const tokens = text.split(/\s+/).filter(Boolean);
    const rows = [];
    let cur = null;
    tokens.forEach(function (token) {
      if (/^\d+\.$/.test(token)) {
        cur = { n: token.replace('.', ''), moves: [] };
        rows.push(cur);
        return;
      }
      if (!cur) {
        cur = { n: String(rows.length + 1), moves: [] };
        rows.push(cur);
      }
      cur.moves.push(token);
    });
    ticker.innerHTML = rows
      .map(function (row) {
        return (
          '<span class="move-ticker-pair"><span class="move-ticker-num">' +
          escapeHtml(row.n) +
          '.</span>' +
          row.moves
            .map(function (m) {
              return '<span class="move-ticker-san">' + escapeHtml(m) + '</span>';
            })
            .join('') +
          '</span>'
        );
      })
      .join('');
    const sans = ticker.querySelectorAll('.move-ticker-san');
    if (sans.length) {
      const last = sans[sans.length - 1];
      last.classList.add('is-latest');
      last.scrollIntoView({ inline: 'end', block: 'nearest', behavior: 'smooth' });
    }
  }

  function syncBoard(state) {
    if (state.fen) {
      chess = new Chess(state.fen);
    }
    finished = !!state.finished;
    root.dataset.finished = finished ? '1' : '0';
    root.dataset.status = state.status || '';
    if (state.attempt_id) {
      attemptId = state.attempt_id;
      root.dataset.attemptId = String(attemptId);
    }
    setText(messageEl, state.message || '');
    setText(errorEl, state.error || '');
    if (progressEl && state.solution_length != null) {
      progressEl.textContent = (state.ply_index || 0) + '/' + state.solution_length;
    }
    const progressCompact = document.getElementById('progress-compact');
    if (progressCompact && state.solution_length != null) {
      progressCompact.textContent = (state.ply_index || 0) + '/' + state.solution_length;
    }
    if (statusLabelEl) statusLabelEl.textContent = state.status_label || state.status || '';
    const statusCompact = document.getElementById('status-label-compact');
    if (statusCompact) statusCompact.textContent = state.status_label || state.status || '';
    const hist = state.history || (state.moves_played || []).join(' ');
    if (historyEl) {
      historyEl.textContent = hist || t('noMovesYet');
      historyEl.dataset.history = hist || '';
    }
    renderMoveTicker(hist);
    const turnDot = document.getElementById('turn-dot');
    if (turnDot) {
      turnDot.setAttribute('data-your-turn', !finished && state.your_turn ? '1' : '0');
    }
    const youClock = document.getElementById('rail-you-clock');
    if (youClock) {
      youClock.textContent = finished
        ? state.status_label || state.status || t('done')
        : state.your_turn
          ? t('yourTurn')
          : t('waiting');
      youClock.classList.toggle('player-clock-active', !finished && !!state.your_turn);
    }
    if (moveForm) moveForm.hidden = finished;
    if (retryBar) retryBar.hidden = !finished;
    const iconRetry = document.getElementById('icon-retry-btn');
    if (iconRetry) iconRetry.hidden = !finished;

    const color = chess.turn() === 'w' ? 'white' : 'black';
    const movableColor = !finished && state.your_turn ? color : undefined;
    ground.set({
      fen: chess.fen(),
      lastMove: lastMoveArray(state.last_move),
      turnColor: color,
      coordinates: true,
      coordinatesOnSquares: false,
      ranksPosition: 'left',
      movable: {
        color: movableColor,
        dests: movableColor ? destsFromChess() : new Map(),
        events: { after: onUserMove },
      },
      draggable: { enabled: !!movableColor },
    });
  }

  function apiPost(url, body, loadingText) {
    if (busy) return Promise.resolve();
    busy = true;
    setLoader(true, loadingText || t('working'));
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken,
      },
      body: JSON.stringify(body || {}),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { okHttp: res.ok, data: data };
        });
      })
      .then(function (result) {
        const data = result.data || {};
        syncBoard(data);
        if (!result.okHttp && !data.error && !data.message) {
          setText(errorEl, t('requestFailed'));
        }
        return data;
      })
      .catch(function () {
        setText(errorEl, t('requestFailed'));
      })
      .finally(function () {
        busy = false;
        setLoader(false);
        if (sanInput && !finished) {
          sanInput.value = '';
          sanInput.focus();
        }
      });
  }

  function submitSan(san) {
    const value = (san || '').trim();
    if (!value) {
      setText(errorEl, t('enterSan'));
      return;
    }
    apiPost(
      '/api/puzzle/move',
      { attempt_id: attemptId, san: value },
      t('checkingMove')
    );
  }

  function onUserMove(orig, dest, metadata) {
    if (finished || busy) return;
    const needsPromo =
      metadata && metadata.premove === false
        ? false
        : (() => {
            const piece = chess.get(orig);
            if (!piece || piece.type !== 'p') return false;
            const rank = dest[1];
            return (
              (piece.color === 'w' && rank === '8') ||
              (piece.color === 'b' && rank === '1')
            );
          })();

    if (needsPromo) {
      pendingPromo = { orig: orig, dest: dest };
      if (promoPicker) promoPicker.hidden = false;
      return;
    }
    const move = chess.move({ from: orig, to: dest, promotion: 'q' });
    if (!move) {
      syncBoard({
        fen: chess.fen(),
        your_turn: true,
        finished: false,
        status: 'in_progress',
        last_move: root.dataset.lastMove || '',
        moves_played: (historyEl && historyEl.dataset.history
          ? historyEl.dataset.history.split(/\s+/).filter(Boolean)
          : []),
        history: historyEl ? historyEl.dataset.history : '',
        ply_index: Number((progressEl.textContent || '0').split('/')[0]),
        solution_length: Number((progressEl.textContent || '0/0').split('/')[1] || 0),
        status_label: statusLabelEl ? statusLabelEl.textContent : '',
      });
      setText(errorEl, t('illegalMove'));
      return;
    }
    // Revert local apply; server is source of truth
    chess.undo();
    submitSan(move.san);
  }

  if (promoPicker) {
    promoPicker.querySelectorAll('.promo-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!pendingPromo) return;
        const piece = btn.getAttribute('data-piece') || 'q';
        const move = chess.move({
          from: pendingPromo.orig,
          to: pendingPromo.dest,
          promotion: piece,
        });
        promoPicker.hidden = true;
        pendingPromo = null;
        if (!move) {
          setText(errorEl, t('illegalMove'));
          return;
        }
        chess.undo();
        submitSan(move.san);
      });
    });
  }

  ground = Chessground(boardEl, {
    fen: chess.fen(),
    orientation: root.dataset.orientation || 'white',
    turnColor: chess.turn() === 'w' ? 'white' : 'black',
    lastMove: lastMoveArray(root.dataset.lastMove),
    coordinates: true,
    coordinatesOnSquares: false,
    ranksPosition: 'left',
    movable: {
      free: false,
      color: finished ? undefined : chess.turn() === 'w' ? 'white' : 'black',
      showDests: true,
      dests: finished ? new Map() : destsFromChess(),
      events: { after: onUserMove },
    },
    highlight: { lastMove: true, check: true },
    draggable: { enabled: !finished },
  });

  if (moveForm) {
    moveForm.addEventListener('submit', function (event) {
      event.preventDefault();
      submitSan(sanInput ? sanInput.value : '');
    });
  }

  if (retryBtn) {
    retryBtn.addEventListener('click', function () {
      apiPost(
        '/api/puzzle/retry',
        { puzzle_id: puzzleId, attempt_id: attemptId },
        t('working')
      );
    });
  }

  renderMoveTicker(
    (historyEl && historyEl.dataset.history) ||
      (document.getElementById('move-ticker') || {}).dataset.history ||
      ''
  );

  initGameSheet({
    root: root,
    getGround: function () {
      return ground;
    },
  });
})();
