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
    try {
      await apiFetch('/api/logout', { method: 'POST' });
      location.href = '/login';
    } catch (error) { window.showToast(error.message, 'danger'); }
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
      el.dispatchEvent(new CustomEvent('modal-escape', {bubbles: true}));
    });
  });
})();

// Reject HTTP and business failures before an action can report success.
window.apiFetch = async function (url, options) {
  let response;
  try { response = await fetch(url, options); }
  catch (_) { throw new Error('连接失败，请检查服务状态和操作结果'); }
  if (response.status === 204) return response;
  let body;
  try { body = await response.clone().json(); }
  catch (_) { throw new Error('服务返回了无效响应，请刷新后重试'); }
  if (!response.ok || body?.status === false) {
    const detail = Array.isArray(body?.detail)
      ? body.detail.map(item => item.msg).join('；') : body?.detail;
    const error = new Error(detail || body?.msg || body?.error || `操作失败（${response.status}）`);
    error.details = body;
    throw error;
  }
  return response;
};

// Alpine does not surface rejected event promises to the user. Report once at
// the outermost action, and let nested failures stop the remaining workflow.
window.actionPage = function (page) {
  page.actionError = '';
  const pending = new Set();
  const methods = new Map();
  for (const [name, descriptor] of Object.entries(Object.getOwnPropertyDescriptors(page))) {
    const method = descriptor.value;
    if (typeof method !== 'function' || method.constructor.name !== 'AsyncFunction') continue;
    methods.set(name, method);
  }
  for (const [name, method] of methods) {
    page[name] = async function (...args) {
      if (pending.has(name)) return;
      pending.add(name); this.actionError = '';
      // Nested actions use the original methods so failures propagate to this
      // boundary. Writes still pass through Alpine's reactive component proxy.
      const internal = new Proxy(this, {get(target, key, receiver) {
        if (key === 'runAction') return target.runAction.bind(target);
        if (methods.has(key)) return methods.get(key).bind(receiver);
        return Reflect.get(target, key, receiver);
      }});
      try { return await method.apply(internal, args); }
      catch (error) {
        this.actionError = error.message || '操作失败，请重试';
        if ('error' in this) this.error = error.message;
        if ('updateOutput' in this && error.details) {
          this.updateOutput = [error.details.stdout, error.details.stderr].filter(Boolean).join('\n');
        }
        showToast(error.message || '操作失败，请重试', 'danger');
      } finally { pending.delete(name); }
    };
  }
  page.runAction = function (name, ...args) { return this[name](...args); };
  return page;
};

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

window.taskStatusLabel = function (status) {
  return ({
    pending: '等待执行', running: '运行中', completed: '已完成',
    partial: '未全部完成', failed: '失败', cancelled: '已取消',
  })[status] || status;
};

window.taskResultSummary = function (task) {
  const count = key => Math.max(0, Number(task[key]) || 0);
  const succeeded = count('succeeded_chapters');
  const failed = count('failed_chapters');
  const skipped = count('skipped_chapters');
  const unknown = Math.max(0, count('done_chapters') - succeeded - failed - skipped);
  return `${succeeded} 成功 · ${failed} 失败 · ${skipped} 跳过`
    + (unknown ? ` · ${unknown} 历史未分类` : '');
};
