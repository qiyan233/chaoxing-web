// Midnight Operator — frontend helpers
// Pure vanilla, no Bootstrap dependency. Alpine.js handles state.

(function () {
  const PREFIX = {
    success: '◉ OK',
    danger:  '◉ ERR',
    warning: '◉ WARN',
    info:    '◉ INFO',
  };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[c]);
  }

  function ensureToastStack() {
    let stack = document.querySelector('.toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'toast-stack';
      document.body.appendChild(stack);
    }
    return stack;
  }

  function showToast(message, type = 'info', timeout = 4200) {
    const stack = ensureToastStack();
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `
      <span class="prefix">${PREFIX[type] || PREFIX.info}</span>
      <span class="toast-msg">${escapeHtml(message)}</span>
    `;
    stack.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity 0.3s, transform 0.3s';
      el.style.opacity = '0';
      el.style.transform = 'translateX(20px)';
      setTimeout(() => el.remove(), 320);
    }, timeout);
  }

  async function logout() {
    try { await fetch('/api/logout', { method: 'POST' }); } catch (e) { /* noop */ }
    location.href = '/login';
  }

  // expose globally
  window.showToast = showToast;
  window.escapeHtml = escapeHtml;
  window.logout = logout;

  // Page entry stagger reveal
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.stagger').forEach(group => {
      group.classList.add('stagger-active');
    });
  });

  // Esc closes any modal with [data-modal-open]
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('[data-modal-open="true"]').forEach(el => {
      el.dispatchEvent(new CustomEvent('modal-escape'));
    });
  });
})();

// ---------- formatting helpers used inline by Alpine ----------

window.fmtTime = function (s) {
  if (!s) return '—';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};

window.fmtClock = function (s) {
  if (!s) return '—';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = n => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};

window.fmtPct = function (done, total) {
  if (!total) return 0;
  return Math.floor((done / total) * 100);
};

window.fmtAscii = function (done, total, width = 22) {
  if (!total) return '░'.repeat(width);
  const pct = Math.min(1, done / total);
  const filled = Math.round(pct * width);
  return '█'.repeat(filled) + '░'.repeat(width - filled);
};
