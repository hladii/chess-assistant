(function () {
  const burger = document.getElementById('nav-burger');
  const panel = document.getElementById('mobile-nav');
  if (!burger || !panel) return;

  const I18N = window.I18N || {};
  function t(key) {
    return I18N[key] != null ? I18N[key] : key;
  }

  function setOpen(open) {
    document.body.classList.toggle('nav-open', open);
    panel.hidden = !open;
    burger.setAttribute('aria-expanded', open ? 'true' : 'false');
    burger.setAttribute('aria-label', open ? t('closeMenu') : t('openMenu'));
  }

  burger.addEventListener('click', function () {
    setOpen(panel.hidden);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !panel.hidden) {
      setOpen(false);
      burger.focus();
    }
  });

  // Close after navigating within the drawer.
  panel.querySelectorAll('a').forEach(function (link) {
    link.addEventListener('click', function () {
      setOpen(false);
    });
  });

  window.addEventListener('resize', function () {
    if (window.matchMedia('(min-width: 801px)').matches && !panel.hidden) {
      setOpen(false);
    }
  });
})();
