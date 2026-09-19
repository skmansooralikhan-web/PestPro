// PCT CRM — global helpers shared across every page

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  });
}

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

// Injects a hidden csrf_token field into every POST form under `root`
// (defaults to the whole document). Safe to call repeatedly — skips forms
// that already have the field. Call this again after any AJAX call that
// injects new <form> elements into the page (see clients/index.html).
function injectCsrfTokens(root) {
  const token = getCsrfToken();
  if (!token) return;
  (root || document).querySelectorAll('form').forEach((form) => {
    const method = (form.getAttribute('method') || 'get').toLowerCase();
    if (method !== 'post') return;
    if (form.querySelector('input[name="csrf_token"]')) return;
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'csrf_token';
    input.value = token;
    form.appendChild(input);
  });
}

// Opens a PDF in an in-page preview modal, rendered via the browser's own
// built-in PDF viewer against a blob: URL — not a direct link/iframe to the
// server URL, and not an external rendering library either.
//
// Why blob: specifically, not a direct <embed src="/invoices/x/pdf">: some
// browsers/profiles are configured to always download PDFs rather than
// display them, but that setting applies to navigating to (or embedding)
// a *server* PDF response — it has nothing to fetch or navigate to attach
// that behaviour to, so it's never in play here.
//
// Earlier this rendered each page onto <canvas> via a bundled copy of
// PDF.js. That added a large external library (1.4MB) with its own worker
// file as a hard dependency for something the browser can already do
// natively — any failure loading that library (a network hiccup, a
// stricter CSP or extension in some environment, a worker/version
// mismatch) broke the preview outright with no real fallback. Handing the
// same bytes to the browser's native viewer removes that whole dependency.
async function showPdfPreview(url, filename, title) {
  const modal = document.getElementById('pdfPreviewModal');
  if (!modal) { window.open(url, '_blank'); return; }

  const statusEl = document.getElementById('pdfPreviewStatus');
  const pagesEl = document.getElementById('pdfPreviewPages');
  const dl = document.getElementById('pdfPreviewDownload');
  document.getElementById('pdfPreviewTitle').textContent = title || 'Preview';
  pagesEl.innerHTML = '';
  statusEl.style.display = 'block';
  statusEl.innerHTML = '<i class="ti ti-loader-2"></i>Loading preview…';
  dl.removeAttribute('href');
  modal.classList.add('open');

  try {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('Server returned ' + resp.status);
    const bytes = await resp.arrayBuffer();

    // Wire up Download from the same bytes already fetched — no second
    // request, and a blob: URL with the `download` attribute always
    // triggers a real "Save As" regardless of any PDF-handling setting.
    const blobUrl = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
    dl.href = blobUrl;
    dl.setAttribute('download', filename || 'document.pdf');

    // Some browsers/profiles are deliberately configured to always
    // download PDFs rather than display them — often an explicit choice
    // (sometimes an enterprise policy). That setting affects an embedded
    // PDF the same way it affects a direct link, so trying to render one
    // here would just fail confusingly (a native "couldn't load plugin"
    // message, or a silent download). navigator.pdfViewerEnabled reports
    // this directly, so it's worth checking before attempting a render
    // rather than after — an honest explanation beats fighting a setting
    // the person may have chosen deliberately.
    if (navigator.pdfViewerEnabled === false) {
      statusEl.style.display = 'block';
      statusEl.innerHTML = '<i class="ti ti-info-circle"></i>This browser is set to always ' +
        'download PDFs rather than display them, so an inline preview isn\'t possible here — ' +
        'use the Download button to save and open it.';
      return;
    }

    const embed = document.createElement('embed');
    embed.type = 'application/pdf';
    embed.src = blobUrl;
    embed.style.width = '100%';
    embed.style.height = '78vh';
    embed.style.border = 'none';
    pagesEl.appendChild(embed);
    statusEl.style.display = 'none';

    // A handful of very old or locked-down browser configurations have no
    // built-in PDF viewer at all — <embed> just renders blank in that case
    // rather than raising a JS error, so there's nothing to `catch` for
    // it. Give a way to open it directly after a moment, in case the
    // inline view genuinely didn't render, without replacing the embed
    // (which usually does work) while this check runs.
    setTimeout(() => {
      if (!pagesEl.querySelector('.open-tab-fallback')) {
        const fallback = document.createElement('div');
        fallback.className = 'open-tab-fallback muted';
        fallback.style.cssText = 'text-align:center;font-size:11.5px;margin-top:8px;';
        fallback.innerHTML = 'Not showing correctly? <a href="' + blobUrl +
          '" target="_blank" style="color:var(--pine);font-weight:700;">Open in a new tab</a> instead.';
        pagesEl.appendChild(fallback);
      }
    }, 1200);
  } catch (err) {
    console.error('PDF preview failed:', err);
    statusEl.style.display = 'block';
    statusEl.innerHTML = '<i class="ti ti-alert-triangle"></i>Couldn\'t render a preview here — ' +
      'use the Download button to save and open it instead.' +
      '<div class="muted" style="font-size:11px;margin-top:6px;">' +
      'Details: ' + (err && err.message ? err.message : String(err)) + '</div>';
    // Still make sure Download works even if fetching failed outright, by
    // falling back to the direct URL.
    if (!dl.getAttribute('href')) {
      dl.href = url;
      dl.setAttribute('download', filename || 'document.pdf');
    }
  }
}

function closePdfPreview() {
  const pagesEl = document.getElementById('pdfPreviewPages');
  const dl = document.getElementById('pdfPreviewDownload');
  if (pagesEl) pagesEl.innerHTML = '';
  if (dl && dl.getAttribute('href') && dl.getAttribute('href').startsWith('blob:')) {
    URL.revokeObjectURL(dl.getAttribute('href'));
  }
}

function showToast(message, kind) {
  const wrap = document.getElementById('toastWrap');
  if (!wrap) return;
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || 'success');
  el.textContent = message;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

document.addEventListener('DOMContentLoaded', () => {
  injectCsrfTokens();

  // ---- Flash messages -> toasts -----------------------------------------
  const flashEl = document.getElementById('flashMessages');
  if (flashEl) {
    try {
      const messages = JSON.parse(flashEl.dataset.messages);
      messages.forEach(([category, msg]) => {
        showToast(msg, category === 'error' ? 'error' : 'success');
      });
    } catch (e) { /* no-op */ }
  }

  // ---- Sidebar toggle (mobile) -------------------------------------------
  const menuToggle = document.getElementById('menuToggle');
  const sidebar = document.getElementById('sidebar');
  if (menuToggle && sidebar) {
    menuToggle.addEventListener('click', () => sidebar.classList.toggle('open'));
    document.addEventListener('click', (e) => {
      if (sidebar.classList.contains('open') && !sidebar.contains(e.target) && e.target !== menuToggle) {
        sidebar.classList.remove('open');
      }
    });
  }

  // ---- Notification bell --------------------------------------------------
  const notifBtn = document.getElementById('notifBtn');
  const notifDot = document.getElementById('notifDot');
  const notifDropdown = document.getElementById('notifDropdown');

  function loadUnreadCount() {
    fetch('/notifications/unread-count.json').then(r => r.json()).then(data => {
      if (!notifDot) return;
      if (data.count > 0) {
        notifDot.style.display = 'flex';
        notifDot.textContent = data.count > 9 ? '9+' : data.count;
      } else {
        notifDot.style.display = 'none';
      }
    }).catch(() => {});
  }

  function renderNotifDropdown() {
    fetch('/notifications/recent.json').then(r => r.json()).then(items => {
      if (!notifDropdown) return;
      if (!items.length) {
        notifDropdown.innerHTML = '<div style="padding:18px;text-align:center;color:var(--muted);font-size:12.5px;">No notifications yet.</div>';
        return;
      }
      notifDropdown.innerHTML = items.map(n => `
        <div style="padding:11px 14px;border-bottom:1px solid var(--border);${n.is_read ? 'opacity:.55;' : ''}">
          <div style="font-size:12.5px;font-weight:700;">${n.title}</div>
          <div style="font-size:11.5px;color:var(--muted);margin-top:2px;">${n.message}</div>
        </div>`).join('') +
        '<a href="/notifications/" style="display:block;text-align:center;padding:9px;font-size:12px;font-weight:600;color:var(--accent-hover);">View all</a>';
    }).catch(() => {});
  }

  if (notifBtn) {
    loadUnreadCount();
    setInterval(loadUnreadCount, 60000);
    notifBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = notifDropdown.style.display === 'block';
      notifDropdown.style.display = isOpen ? 'none' : 'block';
      if (!isOpen) renderNotifDropdown();
    });
    document.addEventListener('click', (e) => {
      if (notifDropdown && !notifDropdown.contains(e.target) && e.target !== notifBtn) {
        notifDropdown.style.display = 'none';
      }
    });
  }

  // ---- Chip-style multi-select checkboxes ---------------------------------
  // Each chip is a <label> wrapping its (visually hidden) checkbox — clicking
  // anywhere in the label already natively toggles the wrapped input and
  // then re-dispatches a second click with the input itself as the target,
  // as browsers do for any label/control pairing. The old code additionally
  // toggled input.checked by hand on the first (non-input-target) click,
  // which combined with the browser's own native toggle to flip the value
  // twice per click — visually netting to no change at all. Letting the
  // browser's native behavior be the only thing that changes the checked
  // state, and only using the click event to resync the visual class,
  // fixes it.
  document.querySelectorAll('.chip').forEach(chip => {
    const input = chip.querySelector('input');
    if (!input) return;
    const sync = () => chip.classList.toggle('checked', input.checked);
    sync();
    chip.addEventListener('click', () => sync());
  });

  // ---- Generic modal open/close -------------------------------------------
  document.querySelectorAll('[data-modal-open]').forEach(btn => {
    btn.addEventListener('click', () => {
      const modal = document.getElementById(btn.dataset.modalOpen);
      if (modal) modal.classList.add('open');
    });
  });
  document.querySelectorAll('[data-modal-close]').forEach(btn => {
    btn.addEventListener('click', () => {
      const modal = btn.closest('.modal-overlay');
      if (modal) {
        modal.classList.remove('open');
        if (modal.id === 'pdfPreviewModal') closePdfPreview();
      }
    });
  });
  document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        overlay.classList.remove('open');
        if (overlay.id === 'pdfPreviewModal') closePdfPreview();
      }
    });
  });

  // ---- Confirm-before-submit forms -----------------------------------------
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', (e) => {
      if (!confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  initGlobalSearch();
});

// ---- Global search: debounced quick-results dropdown in the header ----------
function initGlobalSearch() {
  const input = document.getElementById('globalSearchInput');
  const dropdown = document.getElementById('globalSearchDropdown');
  if (!input || !dropdown) return;

  let debounceTimer = null;
  let currentQuery = '';

  input.addEventListener('input', () => {
    const q = input.value.trim();
    clearTimeout(debounceTimer);
    if (q.length < 2) {
      dropdown.style.display = 'none';
      return;
    }
    debounceTimer = setTimeout(() => runQuickSearch(q), 250);
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const q = input.value.trim();
      if (q.length >= 2) window.location.href = '/search/?q=' + encodeURIComponent(q);
    } else if (e.key === 'Escape') {
      dropdown.style.display = 'none';
      input.blur();
    }
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.global-search')) dropdown.style.display = 'none';
  });

  async function runQuickSearch(q) {
    currentQuery = q;
    try {
      const resp = await fetch('/search/quick?q=' + encodeURIComponent(q));
      if (!resp.ok) throw new Error('Search request failed (' + resp.status + ')');
      const data = await resp.json();
      if (q !== currentQuery) return; // a newer keystroke already superseded this response
      renderQuickResults(data, q);
    } catch (err) {
      console.error('Global search failed:', err);
      dropdown.style.display = 'none';
    }
  }

  function renderQuickResults(data, q) {
    const sections = [
      { key: 'clients', label: 'Clients', icon: 'ti-users',
        render: (c) => `<a href="/clients/?client=${c.id}" class="search-result-row">
          <strong>${escapeHtml(c.name)}</strong><span class="muted">${escapeHtml(c.phone || '')}${c.area ? ' · ' + escapeHtml(c.area) : ''}</span></a>` },
      { key: 'invoices', label: 'Invoices', icon: 'ti-file-invoice',
        render: (i) => `<a href="/invoices/${i.id}" class="search-result-row">
          <strong>${escapeHtml(i.inv_number)}</strong><span class="muted">${escapeHtml(i.client_name)}</span></a>` },
      { key: 'amc', label: 'AMC Contracts', icon: 'ti-file-certificate',
        render: (a) => `<a href="/amc/" class="search-result-row">
          <strong>${escapeHtml(a.contract_number)}</strong><span class="muted">${escapeHtml(a.client_name)}</span></a>` },
      { key: 'leads', label: 'Leads', icon: 'ti-target-arrow',
        render: (l) => `<a href="/leads/" class="search-result-row">
          <strong>${escapeHtml(l.name)}</strong><span class="muted">${escapeHtml(l.phone || '')}</span></a>` },
    ];

    const total = Object.values(data).reduce((sum, arr) => sum + arr.length, 0);
    if (total === 0) {
      dropdown.innerHTML = '<div class="muted" style="padding:14px;text-align:center;font-size:12.5px;">No matches for "' + escapeHtml(q) + '"</div>';
      dropdown.style.display = 'block';
      return;
    }

    let html = '';
    sections.forEach(section => {
      const items = data[section.key] || [];
      if (!items.length) return;
      html += `<div class="search-section-label"><i class="ti ${section.icon}"></i> ${section.label}</div>`;
      html += items.map(section.render).join('');
    });
    html += `<a href="/search/?q=${encodeURIComponent(q)}" class="search-result-row" style="justify-content:center;color:var(--pine);font-weight:700;">
      See all results for "${escapeHtml(q)}" <i class="ti ti-arrow-right"></i></a>`;
    dropdown.innerHTML = html;
    dropdown.style.display = 'block';
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }
}
