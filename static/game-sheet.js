/** Mobile bottom-sheet + icon bar for game/puzzle pages. */

const SHEET_TITLES = {
  move: 'sheetMove',
  history: 'sheetHistory',
  lines: 'sheetLines',
  coach: 'sheetCoach',
};

function t(key, fallback) {
  const I18N = window.I18N || {};
  return I18N[key] != null ? I18N[key] : fallback || key;
}

export function initGameSheet(options) {
  const root = options.root;
  const panel = options.panel || document.getElementById('game-panel');
  const backdrop = options.backdrop || document.getElementById('game-sheet-backdrop');
  const titleEl = options.titleEl || document.getElementById('game-sheet-title');
  const ground = options.getGround || function () { return null; };

  if (!root || !panel) {
    return { openSheet: function () {}, closeSheet: function () {} };
  }

  let active = null;

  function isMobile() {
    return window.matchMedia('(max-width: 1100px)').matches;
  }

  function redrawBoard() {
    const g = ground();
    if (g && typeof g.redrawAll === 'function') {
      window.requestAnimationFrame(function () {
        g.redrawAll();
      });
    }
  }

  function closeSheet() {
    active = null;
    root.classList.remove('sheet-open');
    root.removeAttribute('data-sheet');
    panel.querySelectorAll('[data-sheet-section]').forEach(function (el) {
      el.classList.remove('is-sheet-active');
    });
    root.querySelectorAll('.game-icon-btn[data-sheet-open]').forEach(function (btn) {
      btn.classList.remove('is-active');
    });
    if (backdrop) backdrop.hidden = true;
    document.body.classList.remove('game-sheet-open');
    redrawBoard();
  }

  function openSheet(name) {
    if (!isMobile()) return;
    const section = panel.querySelector('[data-sheet-section="' + name + '"]');
    if (!section) return;

    active = name;
    root.classList.add('sheet-open');
    root.setAttribute('data-sheet', name);
    document.body.classList.add('game-sheet-open');
    if (backdrop) backdrop.hidden = false;

    panel.querySelectorAll('[data-sheet-section]').forEach(function (el) {
      const on = el.getAttribute('data-sheet-section') === name;
      el.classList.toggle('is-sheet-active', on);
      if (on) el.hidden = false;
    });
    root.querySelectorAll('.game-icon-btn[data-sheet-open]').forEach(function (btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-sheet-open') === name);
    });

    if (titleEl) {
      const key = SHEET_TITLES[name];
      titleEl.textContent = t(key, name);
    }

    redrawBoard();
  }

  root.querySelectorAll('[data-sheet-open]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const name = btn.getAttribute('data-sheet-open');
      if (active === name) closeSheet();
      else openSheet(name);
    });
  });

  root.querySelectorAll('[data-close-sheet]').forEach(function (btn) {
    btn.addEventListener('click', closeSheet);
  });

  if (backdrop) {
    backdrop.addEventListener('click', closeSheet);
  }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && root.classList.contains('sheet-open')) {
      closeSheet();
    }
  });

  window.addEventListener('resize', function () {
    if (!isMobile() && root.classList.contains('sheet-open')) {
      closeSheet();
    } else {
      redrawBoard();
    }
  });

  // Icon actions that proxy to existing desktop controls
  root.querySelectorAll('[data-icon-action]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const action = btn.getAttribute('data-icon-action');
      if (action === 'undo') {
        const target = document.getElementById('undo-btn');
        if (target) target.click();
      } else if (action === 'hint') {
        const target = document.getElementById('hint-btn');
        if (target) target.click();
      } else if (action === 'resign') {
        const target = document.getElementById('resign-btn');
        if (target) target.click();
      } else if (action === 'analyze') {
        const target = document.getElementById('analyze-hint-btn');
        if (target) target.click();
      } else if (action === 'retry') {
        const target = document.getElementById('retry-btn');
        if (target) target.click();
      }
    });
  });

  return { openSheet: openSheet, closeSheet: closeSheet };
}
