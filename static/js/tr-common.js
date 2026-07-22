/* ═══════════════════════════════════════════════════════════════
   POWER GYM — Common JavaScript
   tr-common.js  |  Shared across ALL pages

   Load this file FIRST, before any page-specific script
   (tr-login.js / tr-admin.js / tr-staff.js / tr-member.js).

   MODULE MAP:
   ─────────────────────────────────────────────────────────────
   1.  Auth      — login, logout, session, registration (localStorage)
   2.  Session   — guard on dashboards
   3.  Navigation — screen/tab switching, sidebar active state, role hint
   4.  Shared    — attendance grid, filter table, modals, logout
   5.  Toast     — toast notification system
   ═══════════════════════════════════════════════════════════════ */

'use strict';

/* ════════════════════════════════════════════════
   1. AUTH MODULE
   Handles login, logout, registration.
   Uses localStorage so session persists across pages.
════════════════════════════════════════════════ */
const Auth = (() => {

  const ADMIN_ACCOUNTS = {
    'admin@powergym.com': { password: 'admin123', role: 'admin', name: 'Administrator', initials: 'AD' }
  };

  const STAFF_ACCOUNTS = {
    'staff@powergym.com': { password: 'staff123', role: 'staff', name: 'Staff Member', initials: 'SF' }
  };

  // Member accounts also stored in localStorage for persistence
  let _memberAccounts = {};

  function _loadMembers() {
    try { _memberAccounts = JSON.parse(localStorage.getItem('trmem_members') || '{}'); }
    catch (e) { _memberAccounts = {}; }
    // Seed default demo member
    if (!_memberAccounts['maria@email.com']) {
      _memberAccounts['maria@email.com'] = { password: 'member123', role: 'member', name: 'Maria Santos', initials: 'MS' };
      _saveMembers();
    }
  }

  function _saveMembers() {
    try { localStorage.setItem('trmem_members', JSON.stringify(_memberAccounts)); }
    catch (e) { /* Storage unavailable */ }
  }

  function getAccount(email) {
    const e = email.toLowerCase().trim();
    return ADMIN_ACCOUNTS[e] || STAFF_ACCOUNTS[e] || _memberAccounts[e] || null;
  }

  function login(email, password) {
    _loadMembers();
    const account = getAccount(email);
    if (!account) return { success: false, error: 'Account not found. Please register first.' };
    if (account.password !== password) return { success: false, error: 'Incorrect password. Please try again.' };
    const session = {
      email: email.toLowerCase().trim(),
      role: account.role,
      name: account.name,
      initials: account.initials
    };
    try { localStorage.setItem('trmem_session', JSON.stringify(session)); }
    catch (e) { /* fallback: session only lives in memory */ }
    return { success: true, session };
  }

  function logout() {
    try { localStorage.removeItem('trmem_session'); }
    catch (e) { /* ignore */ }
  }

  function getSession() {
    try {
      const raw = localStorage.getItem('trmem_session');
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  function getRole() {
    const s = getSession();
    return s ? s.role : null;
  }

  function detectRole(email) {
    _loadMembers();
    const e = email.toLowerCase().trim();
    if (ADMIN_ACCOUNTS[e]) return 'admin';
    if (STAFF_ACCOUNTS[e]) return 'staff';
    if (e.length > 3 && e.includes('@')) return 'member';
    return null;
  }

  function registerMember(email, password, name) {
    _loadMembers();
    const e = email.toLowerCase().trim();
    if (ADMIN_ACCOUNTS[e] || STAFF_ACCOUNTS[e]) return false;
    const initials = name ? name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2) : 'MB';
    _memberAccounts[e] = { password, role: 'member', name: name || 'New Member', initials };
    _saveMembers();
    return true;
  }

  // Initialize member store
  _loadMembers();

  return { login, logout, getSession, getRole, detectRole, registerMember, getAccount };
})();


/* ════════════════════════════════════════════════
   2. SESSION MODULE
   Guards dashboard pages; redirects if role doesn't match.
   Called on each dashboard's DOMContentLoaded.
════════════════════════════════════════════════ */
const Session = (() => {

  const DASHBOARD_ROLES = {
    'admin-dashboard.html':  'admin',
    'staff-dashboard.html':  'staff',
    'member-dashboard.html': 'member'
  };

  /**
   * Called on a dashboard page.
   * If session is invalid or role doesn't match, redirect to login.
   * Returns the session if valid, null otherwise.
   */
  function guardDashboard() {
    // Flask handles authentication server-side.
    // Just read sidebar elements already rendered by Jinja and return a session-like object.
    const name     = document.getElementById('sidebar-user-name')?.textContent  || '';
    const email    = document.getElementById('sidebar-user-email')?.textContent || '';
    const initials = document.getElementById('sidebar-user-avatar')?.textContent || '';
    return { name, email, initials };
  }

  function redirectToLogin() {
    window.location.href = '/login';
  }

  function redirectToRole(role) {
    const map = { admin: '/admin', staff: '/staff', member: '/member' };
    window.location.href = map[role] || '/login';
  }

  return { guardDashboard, redirectToLogin, redirectToRole };
})();


/* ════════════════════════════════════════════════
   3. NAVIGATION MODULE
   Handles screen switching (login page) and
   sidebar tab activation (dashboard pages).
════════════════════════════════════════════════ */
const Navigation = (() => {

  /** Switch screens on trmem.html (login/register) */
  function goToScreen(screenId) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    const el = document.getElementById('screen-' + screenId);
    if (el) el.classList.add('active');
  }

  /** Activate a sub-panel and highlight nav item */
  function activateTab(prefix, tab, navEl) {
    // Hide all sub-panels
    document.querySelectorAll('.sub-panel').forEach(p => p.classList.remove('active'));
    // Deactivate all nav items
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    // Show target panel
    const panel = document.getElementById(prefix + '-' + tab);
    if (panel) panel.classList.add('active');
    // Highlight nav item
    if (navEl) navEl.classList.add('active');
    else {
      const autoNav = document.getElementById('nav-' + prefix + '-' + tab);
      if (autoNav) autoNav.classList.add('active');
    }
  }

  /** Show role hint bar on the login form */
  function showRoleHint(role) {
    const bar = document.getElementById('role-hint-bar');
    const tag = document.getElementById('login-role-tag');
    if (!bar || !tag) return;

    const configs = {
      admin:  { text: '🛡️ Admin Account Detected',  bg: 'rgba(230,30,37,0.12)',   color: 'var(--red)',   border: 'var(--red)',   tagText: 'ADMIN ACCESS' },
      staff:  { text: '👥 Staff Account Detected',  bg: 'rgba(26,71,138,0.2)',    color: '#8eb8ff',      border: '#8eb8ff',      tagText: 'STAFF ACCESS' },
      member: { text: '⚡ Member Login',             bg: 'rgba(255,171,64,0.1)',   color: 'var(--gold)',  border: 'rgba(255,171,64,0.3)', tagText: 'MEMBER ACCESS' }
    };

    if (role && configs[role]) {
      const cfg = configs[role];
      bar.style.cssText = `display:block;background:${cfg.bg};color:${cfg.color};border:1px solid ${cfg.border};margin-bottom:16px;padding:10px 14px;border-radius:4px;font-size:12px;font-weight:600;letter-spacing:1px;text-transform:uppercase;`;
      bar.textContent = cfg.text;
      tag.textContent = cfg.tagText;
    } else {
      bar.style.display = 'none';
      tag.textContent = '\u00a0';
    }
  }

  return { goToScreen, activateTab, showRoleHint };
})();


/* ════════════════════════════════════════════════
   4. SHARED UTILITIES
   Functions used across multiple modules / pages.
════════════════════════════════════════════════ */

/** Build an attendance dot grid. totalDays defaults to 30 if not given
 *  (kept for backward compatibility with pages that don't pass it yet). */
function buildAttGrid(elId, presentDays, totalDays = 30) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.innerHTML = '';
  for (let d = 1; d <= totalDays; d++) {
    const dot = document.createElement('div');
    dot.className  = 'att-dot ' + (presentDays.includes(d) ? 'present' : 'absent');
    dot.textContent = d;
    el.appendChild(dot);
  }
}

/** Filter a data table by search string */
function filterTable(input) {
  const val = input.value.toLowerCase();
  document.querySelectorAll('#members-table tbody tr').forEach(r => {
    r.style.display = r.textContent.toLowerCase().includes(val) ? '' : 'none';
  });
}

/** Open a modal overlay */
function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('open');
}

/** Close a modal overlay */
function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('open');
}

/** Payment verification (used by admin) — calls the real backend endpoint */
function verifyPayment(btn, action) {
  const card = btn.closest('.verify-card');
  if (!card) return;

  const paymentId = card.dataset.paymentId;
  if (!paymentId) { showToast('Missing payment reference — cannot verify.', 'error'); return; }

  const buttons = card.querySelectorAll('button');
  buttons.forEach(b => b.disabled = true);

  fetch(`/admin/verify-payment/${paymentId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action })
  })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || !data.success) {
        showToast(data.error || 'Failed to process payment.', 'error');
        buttons.forEach(b => b.disabled = false);
        return;
      }

      const badge = card.querySelector('.badge');
      if (badge) {
        if (data.status === 'verified') {
          badge.className   = 'badge badge-green';
          badge.textContent = 'Approved ✓';
        } else {
          badge.className   = 'badge badge-red';
          badge.textContent = 'Rejected ✗';
        }
      }
      buttons.forEach(b => b.remove());
      showToast(data.message, data.status === 'rejected' ? 'error' : 'success');
      // Reload so the card moves out of Pending Verifications and the
      // newly-verified payment appears in Payment History below.
      setTimeout(() => window.location.reload(), 900);
    })
    .catch(() => {
      showToast('Could not reach the server. Please try again.', 'error');
      buttons.forEach(b => b.disabled = false);
    });
}

/** Change password (used by admin/staff/member sidebars) — calls the real backend endpoint */
function submitChangePassword() {
  const currentEl = document.getElementById('cp-current');
  const newEl     = document.getElementById('cp-new');
  const confirmEl = document.getElementById('cp-confirm');

  const current_password = currentEl?.value || '';
  const new_password     = newEl?.value     || '';
  const confirm_password = confirmEl?.value || '';

  if (!current_password || !new_password || !confirm_password) {
    showToast('Please fill in all fields.', 'error');
    return;
  }
  if (new_password.length < 8) {
    showToast('New password must be at least 8 characters.', 'error');
    return;
  }
  if (new_password !== confirm_password) {
    showToast('New password and confirmation do not match.', 'error');
    return;
  }

  const btn = document.querySelector('#change-password-modal .btn-red');
  if (btn) { btn.disabled = true; btn.textContent = 'SAVING...'; }

  fetch('/change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password, new_password, confirm_password })
  })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then(({ ok, data }) => {
      if (btn) { btn.disabled = false; btn.textContent = 'SAVE PASSWORD'; }
      if (!ok || !data.success) {
        showToast(data.error || 'Failed to change password.', 'error');
        return;
      }
      [currentEl, newEl, confirmEl].forEach(el => { if (el) el.value = ''; });
      closeModal('change-password-modal');
      showToast(data.message || 'Password changed successfully.', 'success');
    })
    .catch(() => {
      if (btn) { btn.disabled = false; btn.textContent = 'SAVE PASSWORD'; }
      showToast('Could not reach the server. Please try again.', 'error');
    });
}

/** Plan card selection (generic — used on register page and member renewal) */
function selectPlan(card, plan) {
  // Scope to the nearest plan-grid parent to avoid cross-section conflicts
  const grid = card.closest('.plan-grid');
  if (grid) grid.querySelectorAll('.plan-card').forEach(c => c.classList.remove('selected'));
  card.classList.add('selected');
}

/** Toggle a password input between hidden (••••) and visible (plain text).
 *  Expects the button to live inside a .password-field wrapper alongside the input. */
function togglePasswordVisibility(btn) {
  const wrapper = btn.closest('.password-field');
  if (!wrapper) return;
  const input = wrapper.querySelector('input');
  if (!input) return;

  const willShow = input.type === 'password';
  input.type = willShow ? 'text' : 'password';
  wrapper.classList.toggle('revealed', willShow);
  btn.setAttribute('aria-label', willShow ? 'Hide password' : 'Show password');
}
function doLogout() {
  window.location.href = '/logout';
}

// ── Private shared helpers (not exported globally) ──
function _injectSidebarUser(session) {
  if (!session) return;
  const nameEl   = document.getElementById('sidebar-user-name');
  const emailEl  = document.getElementById('sidebar-user-email');
  const avatarEl = document.getElementById('sidebar-user-avatar');

  if (nameEl)   nameEl.textContent   = session.name;
  if (emailEl)  emailEl.textContent  = session.email;
  if (avatarEl) avatarEl.textContent = session.initials;
}

function _bindModalBackdrops() {
  document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', e => {
      if (e.target === overlay) overlay.classList.remove('open');
    });
  });
}

function _val(id) {
  return document.getElementById(id)?.value.trim() || '';
}


/* ════════════════════════════════════════════════
   5. TOAST SYSTEM
════════════════════════════════════════════════ */
function showToast(msg, type = 'success') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast       = document.createElement('div');
  toast.className   = 'toast' + (type === 'error' ? ' error' : '');
  toast.innerHTML   = (type === 'success' ? '✓ ' : '✗ ') + msg;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3100);
}


/* ════════════════════════════════════════════════
   6. COMMON INIT — always-available globals
   Page-specific scripts (tr-login.js / tr-admin.js /
   tr-staff.js / tr-member.js) add their own
   DOMContentLoaded listeners on top of this one.
════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  window.openModal     = openModal;
  window.closeModal    = closeModal;
  window.showToast     = showToast;
  window.buildAttGrid  = buildAttGrid;
  window.doLogout      = doLogout;
  window.verifyPayment = verifyPayment;
  window.submitChangePassword = submitChangePassword;
  window.filterTable   = filterTable;
  window.togglePasswordVisibility = togglePasswordVisibility;
  window.goTo          = (screen) => Navigation.goToScreen(screen);
  // selectPlan is re-assigned per page (login/member) where relevant; keep a fallback
  if (!window.selectPlan) window.selectPlan = selectPlan;
});