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
function buildAttGrid(elId, presentDays, totalDays = 30, todayDay = null, noPlanDays = []) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.innerHTML = '';
  const noPlanSet = new Set(noPlanDays || []);
  for (let d = 1; d <= totalDays; d++) {
    const dot = document.createElement('div');
    // A day with no active membership plan at all stays neutral — there was
    // nothing to check in for, so it shouldn't read as a missed day (red).
    // Otherwise: a day that hasn't happened yet is neither "present" nor
    // "absent" — it just hasn't occurred, so it gets its own neutral state
    // instead of being lumped in with real absences.
    let state;
    if (noPlanSet.has(d)) state = 'no-plan';
    else if (presentDays.includes(d)) state = 'present';
    else if (todayDay && d >= todayDay) state = 'upcoming';
    else state = 'absent';
    dot.className  = 'att-dot ' + state;
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

  // If this card has a staff-facing "confirm student discount" checkbox
  // (shown for Weekly/Monthly/Yearly plan requests awaiting plan approval),
  // send its checked state so the backend can (re)apply the discount based
  // on what staff actually confirmed, not just the member's self-report.
  const studentCheck = card.querySelector('.verify-student-check');
  const body = { action };
  if (studentCheck) body.is_student = studentCheck.checked ? '1' : '0';

  fetch(`/admin/verify-payment/${paymentId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || !data.success) {
        if (data.stale) {
          // This card is out of date (already handled by someone else, or
          // already moved on to the next stage) — just remove it quietly
          // rather than leaving disabled buttons and a scary red toast.
          showToast(data.error || 'This request has already moved on.', 'info');
          card.style.transition = 'opacity 0.25s ease';
          card.style.opacity = '0';
          setTimeout(() => card.remove(), 250);
          return;
        }
        showToast(data.error || 'Failed to process payment.', 'error');
        buttons.forEach(b => b.disabled = false);
        return;
      }

      const badge = card.querySelector('.badge');
      if (badge) {
        if (data.status === 'verified') {
          badge.className   = 'badge badge-green';
          badge.textContent = 'Approved ✓';
        } else if (data.status === 'approved') {
          badge.className   = 'badge badge-gold';
          badge.textContent = 'Plan Approved — Awaiting Payment';
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

  const btn = document.getElementById('cp-submit-btn');
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
      showToast(data.message || 'Password changed successfully.', 'success');
    })
    .catch(() => {
      if (btn) { btn.disabled = false; btn.textContent = 'SAVE PASSWORD'; }
      showToast('Could not reach the server. Please try again.', 'error');
    });
}

/** Update personal information (used by the Settings tab on admin/staff/member dashboards)
 *  — calls the real backend endpoint and refreshes the sidebar on success. */
function submitProfileUpdate() {
  const first_name     = _val('pi-fname');
  const middle_initial = _val('pi-mi');
  const last_name      = _val('pi-lname');
  const extension_name = _val('pi-ext');
  const email          = _val('pi-email');
  const phone          = _val('pi-phone');
  const birthday       = document.getElementById('pi-bday')?.value || '';

  if (!first_name || !last_name || !email) {
    showToast('First name, last name, and email are required.', 'error');
    return;
  }
  if (!/^[A-Za-z\s'-]+$/.test(first_name) || !/^[A-Za-z\s'-]+$/.test(last_name)) {
    showToast('Names can only contain letters — no numbers.', 'error');
    return;
  }
  if (phone && !/^09\d{9}$/.test(phone)) {
    showToast('Phone number must start with 09 and be exactly 11 digits.', 'error');
    return;
  }

  const btn = document.querySelector('#pi-fname')?.closest('.panel')?.querySelector('.btn-red');
  if (btn) { btn.disabled = true; btn.textContent = 'SAVING...'; }

  fetch('/update-profile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ first_name, middle_initial, last_name, extension_name, email, phone, birthday })
  })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then(({ ok, data }) => {
      if (btn) { btn.disabled = false; btn.textContent = 'SAVE CHANGES'; }
      if (!ok || !data.success) {
        showToast(data.error || 'Failed to update profile.', 'error');
        return;
      }

      const nameEl   = document.getElementById('sidebar-user-name');
      const emailEl  = document.getElementById('sidebar-user-email');
      const avatarEl = document.getElementById('sidebar-user-avatar');
      if (nameEl)   nameEl.textContent   = data.user.name;
      if (emailEl)  emailEl.textContent  = data.user.email;
      if (avatarEl) avatarEl.textContent = data.user.initials;

      showToast(data.message || 'Profile updated successfully.', 'success');
    })
    .catch(() => {
      if (btn) { btn.disabled = false; btn.textContent = 'SAVE CHANGES'; }
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
   4b. CONTENT MANAGER — Manage Gym Content
   Shared by staff-dashboard.html and admin-dashboard.html.
   Lets staff/admin add/edit/delete membership plans, services,
   and equipment (name, price, description, inclusions, picture).
════════════════════════════════════════════════ */
const ContentManager = (() => {

  const TYPES = ['plans', 'services', 'equipment'];
  const ENDPOINTS = {
    plans:     { list: '/api/content/plans',     save: '/api/content/plans/save',     del: id => `/api/content/plans/${id}/delete` },
    services:  { list: '/api/content/services',  save: '/api/content/services/save',  del: id => `/api/content/services/${id}/delete` },
    equipment: { list: '/api/content/equipment', save: '/api/content/equipment/save', del: id => `/api/content/equipment/${id}/delete` },
  };
  const LABELS = { plans: 'Membership Plan', services: 'Service', equipment: 'Equipment' };

  let currentType = 'plans';
  let cache = { plans: null, services: null, equipment: null };
  let pendingDelete = null; // { type, id }
  let loaded = false;

  function ensureLoaded() {
    if (loaded) return;
    loaded = true;
    showType('plans');
  }

  function showType(type) {
    currentType = type;
    document.querySelectorAll('.content-subtab').forEach(el => {
      el.classList.toggle('active', el.dataset.contentType === type);
    });
    TYPES.forEach(t => {
      const grid = document.getElementById('content-grid-' + t);
      if (grid) grid.style.display = (t === type) ? 'grid' : 'none';
    });
    if (cache[type] === null) {
      _fetchType(type);
    } else {
      _renderGrid(type, cache[type]);
    }
  }

  function _fetchType(type) {
    const grid = document.getElementById('content-grid-' + type);
    if (grid) grid.innerHTML = '<div class="content-empty">Loading…</div>';
    fetch(ENDPOINTS[type].list)
      .then(res => res.json())
      .then(data => {
        if (!data.success) { showToast(data.error || 'Could not load content.', 'error'); return; }
        cache[type] = data.items;
        if (currentType === type) _renderGrid(type, data.items);
      })
      .catch(() => showToast('Could not reach the server.', 'error'));
  }

  function refresh(type) {
    cache[type] = null;
    if (currentType === type) _fetchType(type);
  }

  function _renderGrid(type, items) {
    const grid = document.getElementById('content-grid-' + type);
    if (!grid) return;
    if (!items.length) {
      grid.innerHTML = `<div class="content-empty">No ${LABELS[type].toLowerCase()}s yet. Click "+ Add New" to create one.</div>`;
      return;
    }
    grid.innerHTML = items.map(item => _cardHtml(type, item)).join('');
  }

  function _esc(s) {
    return (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function _cardHtml(type, item) {
    const img = item.image_path
      ? `background-image:url('/static/${item.image_path}')`
      : '';
    const icon = item.image_path ? '' : (type === 'plans' ? '💳' : type === 'services' ? '🛎️' : '🏋️');
    const priceLine = type === 'plans'
      ? `<div class="content-card-price">₱${Number(item.price).toLocaleString()} / ${item.duration_days} day${item.duration_days == 1 ? '' : 's'}</div>`
      : '';
    let inclusionsHtml = '';
    if (type === 'plans' && item.inclusions) {
      const lines = item.inclusions.split('\n').map(l => l.trim()).filter(Boolean).slice(0, 4);
      if (lines.length) inclusionsHtml = `<ul class="content-card-inclusions">${lines.map(l => `<li>${_esc(l)}</li>`).join('')}</ul>`;
    }
    const statusBadge = item.is_active
      ? '<span class="badge badge-green">ACTIVE</span>'
      : '<span class="badge badge-muted">HIDDEN</span>';
    return `
      <div class="content-card" data-id="${item.id}">
        <div class="content-card-img" style="${img}">${img ? '' : icon}${statusBadge}</div>
        <div class="content-card-body">
          <div class="content-card-name">${_esc(item.name)}</div>
          ${priceLine}
          ${item.description ? `<div class="content-card-desc">${_esc(item.description)}</div>` : ''}
          ${inclusionsHtml}
          <div class="content-card-actions">
            <button class="btn btn-outline" onclick='ContentManager.openForm("${type}", ${JSON.stringify(item).replace(/'/g, "&#39;")})'>EDIT</button>
            <button class="btn btn-outline" style="color:var(--red);border-color:rgba(230,30,37,0.4);" onclick="ContentManager.confirmDelete('${type}', ${item.id}, '${_esc(item.name).replace(/'/g, "\\'")}')">DELETE</button>
          </div>
        </div>
      </div>`;
  }

  function openForm(type, item) {
    currentType = type;
    document.getElementById('cf-type').value = type;
    document.getElementById('cf-id').value = item ? item.id : '';
    document.getElementById('content-form-title').textContent = item ? `EDIT ${LABELS[type].toUpperCase()}` : `ADD ${LABELS[type].toUpperCase()}`;
    document.getElementById('cf-name').value = item ? item.name : '';
    document.getElementById('cf-description').value = item ? item.description : '';
    document.getElementById('cf-sort-order').value = item ? item.sort_order : 0;
    document.getElementById('cf-active').checked = item ? !!item.is_active : true;
    document.getElementById('cf-image-input').value = '';
    document.getElementById('cf-remove-image').checked = false;

    const isPlan = type === 'plans';
    document.getElementById('cf-plan-fields').style.display = isPlan ? 'grid' : 'none';
    document.getElementById('cf-inclusions-wrap').style.display = isPlan ? 'block' : 'none';
    if (isPlan) {
      document.getElementById('cf-price').value = item ? item.price : '';
      document.getElementById('cf-duration').value = item ? item.duration_days : '';
      document.getElementById('cf-inclusions').value = item ? item.inclusions : '';
    }

    const preview = document.getElementById('cf-image-preview');
    const removeWrap = document.getElementById('cf-remove-image-wrap');
    if (item && item.image_path) {
      preview.style.backgroundImage = `url('/static/${item.image_path}')`;
      preview.textContent = '';
      removeWrap.style.display = 'block';
    } else {
      preview.style.backgroundImage = '';
      preview.textContent = '🖼️';
      removeWrap.style.display = 'none';
    }

    openModal('content-form-modal');
  }

  function previewImage(input) {
    const preview = document.getElementById('cf-image-preview');
    const file = input.files && input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
      preview.style.backgroundImage = `url('${e.target.result}')`;
      preview.textContent = '';
    };
    reader.readAsDataURL(file);
  }

  function submit() {
    const type = document.getElementById('cf-type').value;
    const id = document.getElementById('cf-id').value;
    const name = _val('cf-name');
    if (!name) { showToast('Name is required.', 'error'); return; }

    const fd = new FormData();
    if (id) fd.append('id', id);
    fd.append('name', name);
    fd.append('description', document.getElementById('cf-description').value.trim());
    fd.append('sort_order', document.getElementById('cf-sort-order').value || '0');
    fd.append('is_active', document.getElementById('cf-active').checked ? 'true' : 'false');
    fd.append('remove_image', document.getElementById('cf-remove-image').checked ? 'true' : 'false');
    const file = document.getElementById('cf-image-input').files[0];
    if (file) fd.append('image', file);

    if (type === 'plans') {
      const price = document.getElementById('cf-price').value;
      const duration = document.getElementById('cf-duration').value;
      if (!price || Number(price) < 0) { showToast('Enter a valid price.', 'error'); return; }
      if (!duration || Number(duration) <= 0) { showToast('Enter a valid duration in days.', 'error'); return; }
      fd.append('price', price);
      fd.append('duration_days', duration);
      fd.append('inclusions', document.getElementById('cf-inclusions').value);
    }

    fetch(ENDPOINTS[type].save, { method: 'POST', body: fd })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.success) { showToast(data.error || 'Could not save.', 'error'); return; }
        showToast(data.message || 'Saved.', 'success');
        closeModal('content-form-modal');
        refresh(type);
      })
      .catch(() => showToast('Could not reach the server.', 'error'));
  }

  function confirmDelete(type, id, name) {
    pendingDelete = { type, id };
    const msgEl = document.getElementById('content-delete-message');
    if (msgEl) msgEl.textContent = `Are you sure you want to delete "${name}"? This cannot be undone.`;
    openModal('content-delete-modal');
  }

  function cancelDelete() {
    pendingDelete = null;
    closeModal('content-delete-modal');
  }

  function performDelete() {
    if (!pendingDelete) return;
    const { type, id } = pendingDelete;
    fetch(ENDPOINTS[type].del(id), { method: 'POST' })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.success) { showToast((data && data.error) || 'Could not delete.', 'error'); return; }
        showToast(data.message || 'Deleted.', 'success');
        refresh(type);
      })
      .catch(() => showToast('Could not reach the server.', 'error'))
      .finally(() => { pendingDelete = null; closeModal('content-delete-modal'); });
  }

  return { ensureLoaded, showType, openForm, previewImage, submit, confirmDelete, cancelDelete, performDelete, refresh };
})();


/* ════════════════════════════════════════════════
   5. TOAST SYSTEM
════════════════════════════════════════════════ */
function showToast(msg, type = 'success') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast       = document.createElement('div');
  toast.className   = 'toast' + (type === 'error' ? ' error' : type === 'info' ? ' info' : '');
  toast.innerHTML   = (type === 'success' ? '✓ ' : type === 'info' ? 'ℹ ' : '✗ ') + msg;
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
  window.submitProfileUpdate  = submitProfileUpdate;
  window.filterTable   = filterTable;
  window.togglePasswordVisibility = togglePasswordVisibility;
  window.ContentManager = ContentManager;
  window.goTo          = (screen) => Navigation.goToScreen(screen);
  // selectPlan is re-assigned per page (login/member) where relevant; keep a fallback
  if (!window.selectPlan) window.selectPlan = selectPlan;
});