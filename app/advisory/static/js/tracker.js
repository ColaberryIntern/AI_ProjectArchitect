(function() {
  'use strict';

  var API = 'https://enterprise.colaberry.ai';
  var FP_KEY = 'cb_visitor_fp';
  var LEAD_KEY = 'cb_lead_id';
  var LID_KEY = 'cb_lid';
  var buffer = [];
  var initialized = false;
  var firedThresholds = {};
  var lastScrollTime = 0;
  var visibleStart = Date.now();
  var totalVisibleMs = 0;

  function djb2(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
    return h.toString(16).padStart(8, '0');
  }

  function fingerprint() {
    var raw = navigator.userAgent + screen.width + screen.height
      + Intl.DateTimeFormat().resolvedOptions().timeZone + navigator.language;
    return (djb2(raw) + djb2(raw + 'x') + djb2(raw + 'y') + djb2(raw + 'z')).slice(0, 64);
  }

  function ensureFingerprint() {
    var fp = localStorage.getItem(FP_KEY);
    if (!fp) { fp = fingerprint(); localStorage.setItem(FP_KEY, fp); }
    return fp;
  }

  function getLeadId() {
    return localStorage.getItem(LEAD_KEY) || localStorage.getItem(LID_KEY) || null;
  }

  function deviceType() {
    var w = screen.width;
    if (w < 768) return 'mobile';
    if (w < 1024) return 'tablet';
    return 'desktop';
  }

  function detectBrowser() {
    var ua = navigator.userAgent;
    if (/Edg\//i.test(ua)) return 'Edge';
    if (/Chrome/i.test(ua)) return 'Chrome';
    if (/Firefox/i.test(ua)) return 'Firefox';
    if (/Safari/i.test(ua)) return 'Safari';
    return 'Other';
  }

  function detectOS() {
    var ua = navigator.userAgent;
    if (/Windows/i.test(ua)) return 'Windows';
    if (/iPhone|iPad|iPod/i.test(ua)) return 'iOS';
    if (/Mac/i.test(ua)) return 'Mac';
    if (/Android/i.test(ua)) return 'Android';
    if (/Linux/i.test(ua)) return 'Linux';
    return 'Other';
  }

  function push(eventType, props) {
    props = props || {};
    props.page_url = location.href;
    props.page_path = location.pathname;
    buffer.push({
      event_type: eventType,
      timestamp: new Date().toISOString(),
      page_url: location.href,
      page_path: location.pathname,
      event_data: props
    });
  }

  function flush(useBeacon) {
    if (!buffer.length) return;
    var fp = ensureFingerprint();
    var leadId = getLeadId();
    var events = buffer.splice(0);

    var body = JSON.stringify({
      fingerprint: fp,
      user_agent: navigator.userAgent,
      device_type: deviceType(),
      browser: detectBrowser(),
      os: detectOS(),
      lead_id: leadId || undefined,
      events: events
    });

    if (useBeacon) {
      try { navigator.sendBeacon(API + '/api/t/batch', body); } catch(e) {}
      return;
    }

    fetch(API + '/api/t/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body,
      keepalive: true
    }).catch(function() {});
  }

  function onScroll() {
    var now = Date.now();
    if (now - lastScrollTime < 500) return;
    lastScrollTime = now;
    var doc = document.documentElement;
    var scrollable = doc.scrollHeight - doc.clientHeight;
    if (scrollable <= 0) return;
    var pct = Math.round((window.scrollY / scrollable) * 100);
    [25, 50, 75, 90, 100].forEach(function(t) {
      if (pct >= t && !firedThresholds[t]) {
        firedThresholds[t] = true;
        push('scroll', { depth: t });
      }
    });
  }

  function onClick(e) {
    var el = e.target;
    for (var i = 0; i < 4 && el; i++) {
      if (el.tagName === 'VIDEO' || el.tagName === 'AUDIO') {
        push('media_play', {
          element_tag: el.tagName.toLowerCase(),
          element_text: el.getAttribute('title') || el.getAttribute('aria-label') || 'media'
        });
        return;
      }
      if (el.matches && el.matches('.btn-primary, .btn-secondary, .cta, [data-track-cta], [data-track], button[type="submit"]')) {
        push('cta_click', {
          element_text: (el.textContent || '').trim().slice(0, 120),
          href: el.href || (el.closest('a') || {}).href || null,
          data_track: el.getAttribute('data-track') || null,
          is_cta: true
        });
        return;
      }
      if (el.matches && el.matches('a[href], button, [role="button"]')) {
        push('click', {
          element_text: (el.textContent || '').trim().slice(0, 120),
          element_tag: el.tagName.toLowerCase(),
          href: el.href || null
        });
        return;
      }
      el = el.parentElement;
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'hidden') {
      totalVisibleMs += Date.now() - visibleStart;
      push('time_on_page', { seconds: Math.round(totalVisibleMs / 1000) });
      flush(true);
    } else {
      visibleStart = Date.now();
    }
  }

  function onFormSubmit(e) {
    var form = e.target;
    if (form && form.tagName === 'FORM') {
      push('form_submit', { form_action: form.action || '', form_id: form.id || '' });
    }
  }

  function onFormFocus(e) {
    var el = e.target;
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')) {
      var form = el.closest('form');
      if (form && !form._trackStarted) {
        form._trackStarted = true;
        push('form_start', { form_action: form.action || '', form_id: form.id || '' });
      }
    }
  }

  function init() {
    if (initialized) return;
    if (typeof window === 'undefined') return;
    initialized = true;

    ensureFingerprint();

    var params = new URLSearchParams(location.search);
    var lid = params.get('lid');
    if (lid) {
      localStorage.setItem(LEAD_KEY, lid);
      localStorage.setItem(LID_KEY, lid);
    }

    push('pageview', { title: document.title });

    window.addEventListener('scroll', onScroll, { passive: true });
    document.addEventListener('click', onClick, true);
    document.addEventListener('visibilitychange', onVisibilityChange);
    document.addEventListener('submit', onFormSubmit, true);
    document.addEventListener('focusin', onFormFocus, true);
    window.addEventListener('beforeunload', function() { flush(true); });

    setInterval(function() { flush(); }, 5000);
    setInterval(function() {
      if (document.visibilityState === 'visible') push('heartbeat');
    }, 60000);
  }

  window.trackBookingEvent = function(eventType, data) {
    push(eventType, data || {});
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
