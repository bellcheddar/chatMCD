/* chatMCD — WordPress front end.
 *
 * Two jobs: open and close the floating panel, and resize an inline embed to
 * whatever height the widget reports over postMessage.
 *
 * The panel's iframe carries data-src rather than src, so a visitor who never
 * opens the bubble never loads the widget at all.
 */
(function () {
  'use strict';

  var settings = window.chatmcdSettings || {};
  var ORIGIN = (function () {
    try { return new URL(settings.origin || '').origin; } catch (e) { return ''; }
  })();

  /* ------------------------------------------------------------- the bubble */

  function initBubble(root) {
    var toggle = root.querySelector('[data-chatmcd-toggle]');
    var close = root.querySelector('[data-chatmcd-close]');
    var panel = root.querySelector('.chatmcd-panel');
    var frame = root.querySelector('iframe');
    if (!toggle || !panel || !frame) return;

    function open() {
      if (frame.dataset.src && !frame.src) {
        frame.src = frame.dataset.src;   // load on first open, never before
      }
      panel.hidden = false;
      root.classList.add('is-open');
      toggle.setAttribute('aria-expanded', 'true');
      // Move focus into the panel so keyboard users land where the eye does.
      window.setTimeout(function () { frame.focus(); }, 60);
    }

    function shut(returnFocus) {
      panel.hidden = true;
      root.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
      if (returnFocus) toggle.focus();
    }

    toggle.addEventListener('click', function () {
      if (panel.hidden) open(); else shut(false);
    });
    if (close) close.addEventListener('click', function () { shut(true); });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !panel.hidden) shut(true);
    });
    document.addEventListener('click', function (e) {
      if (!panel.hidden && !root.contains(e.target)) shut(false);
    });
  }

  /* -------------------------------------------------------- height reporting */

  function initAutoresize() {
    window.addEventListener('message', function (e) {
      if (ORIGIN && e.origin !== ORIGIN) return;          // only our widget
      var data = e.data;
      if (!data || data.type !== 'chatmcd:height') return;
      var height = parseInt(data.height, 10);
      if (!height || height < 240 || height > 2000) return;

      var frames = document.querySelectorAll('.chatmcd-embed[data-chatmcd-autoresize] iframe');
      for (var i = 0; i < frames.length; i++) {
        if (frames[i].contentWindow === e.source) {
          frames[i].style.height = height + 'px';
        }
      }
    });
  }

  function init() {
    var bubbles = document.querySelectorAll('[data-chatmcd-bubble]');
    for (var i = 0; i < bubbles.length; i++) initBubble(bubbles[i]);
    initAutoresize();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
