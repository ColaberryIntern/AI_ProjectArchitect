/* My Day usage telemetry — first-party, privacy-scoped.
 *
 * Runs ONLY on /my-day/*. Captures:
 *   - one `view` event per page load: the view/tier/project/list/person from the
 *     URL (this alone reveals which views + filters people actually use).
 *   - `click` events on elements carrying a [data-track] label.
 *
 * It NEVER captures form values, keystrokes, cursor coordinates, or any
 * fingerprint. The user is known server-side from the session cookie, so nothing
 * here identifies anyone. Batches POST to /my-day/events (same-origin, cookie).
 */
(function () {
  'use strict';
  if (location.pathname.indexOf('/my-day') !== 0) return;

  var buf = [];
  function qs(k) { return new URLSearchParams(location.search).get(k) || ''; }

  function pushView() {
    buf.push({
      type: 'view',
      label: 'myday.view.' + (qs('view') || 'briefing'),
      view: qs('view') || 'briefing',
      tier: qs('tier'),
      project: qs('project'),
      list: qs('list'),
      person: qs('person'),
      path: location.pathname
    });
    flush(false);
  }

  function onClick(e) {
    var el = e.target;
    for (var i = 0; i < 4 && el; i++) {
      if (el.getAttribute && el.getAttribute('data-track')) {
        buf.push({
          type: 'click',
          label: el.getAttribute('data-track'),
          view: qs('view') || 'briefing',
          path: location.pathname
        });
        flush(false);
        return;
      }
      el = el.parentElement;
    }
  }

  function flush(useBeacon) {
    if (!buf.length) return;
    var body = JSON.stringify({ events: buf.splice(0) });
    if (useBeacon && navigator.sendBeacon) {
      try { navigator.sendBeacon('/my-day/events', new Blob([body], { type: 'application/json' })); } catch (e) {}
      return;
    }
    fetch('/my-day/events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body,
      credentials: 'same-origin',
      keepalive: true
    }).catch(function () {});
  }

  document.addEventListener('click', onClick, true);
  window.addEventListener('beforeunload', function () { flush(true); });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', pushView);
  } else {
    pushView();
  }
})();
