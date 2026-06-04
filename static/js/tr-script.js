/* ═══════════════════════════════════════════════════════════════
   POWER GYM — Unified JavaScript System
   tr-script.js  |  Modular · Clean · Scalable

   MODULE MAP:
   ─────────────────────────────────────────────────────────────
   1.  Auth         — login, logout, session, registration
   2.  Session      — localStorage persistence, guard on dashboards
   3.  Navigation   — screen/tab switching, sidebar active state
   4.  AdminModule  — tabs, member CRUD, analytics, verify payment
   5.  StaffModule  — tabs, check-in, payment recording
   6.  MemberModule — tabs, renewal, body goals, attendance
   7.  Shared       — attendance grid, filter table, modals
   8.  Toast        — toast notification system
   9.  Init         — DOMContentLoaded bootstrap
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
   4. ADMIN MODULE
   Handles all admin dashboard functionality.
════════════════════════════════════════════════ */
const AdminModule = (() => {

  let memberIdCounter = 1005;
  let currentReportType = null;
  let currentReportPayload = null;

  /** Initialize admin dashboard */
  function init() {
    const session = Session.guardDashboard();
    if (!session) return;

    // Inject user info into sidebar
    _injectSidebarUser(session);

    // Apply role-specific CSS class for sidebar tinting
    document.body.classList.add('role-admin');

    // Build attendance grids
    buildAttGrid('att-grid-admin', [1, 2, 3, 4, 5, 7, 8, 9, 10]);
    buildAttGrid('att-grid-admin-full', [1, 2, 3, 4, 5, 7, 8, 9, 10]);

    // Wire member table CRUD buttons
    wireManageMemberButtons();

    // Modal close on backdrop click
    _bindModalBackdrops();

    // Show overview tab by default
    Navigation.activateTab('admin', 'overview', document.getElementById('nav-admin-overview'));
  }

  /** Switch admin sub-panel */
  function tab(tabName, navEl) {
    Navigation.activateTab('admin', tabName, navEl);
  }

  /** Add a new member row from modal form */
  function addMember() {
    const firstName = _val('add-member-fname');
    const lastName  = _val('add-member-lname');
    const email     = _val('add-member-email');
    const phone     = _val('add-member-phone');
    const planText  = document.getElementById('add-member-plan')?.value || 'Monthly';

    if (!firstName || !lastName || !email) {
      showToast('Please fill first name, last name, and email', 'error');
      return;
    }

    const planName = planText.split('—')[0].trim();
    const expiry   = _calculateExpiry(planName);
    memberIdCounter++;
    const id         = '#' + memberIdCounter;
    const expiryText = expiry.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    const tbody      = document.querySelector('#members-table tbody');
    if (!tbody) return;

    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${id}</td>
      <td>${firstName} ${lastName}</td>
      <td>${email}</td>
      <td>${planName}</td>
      <td>${expiryText}</td>
      <td><span class="badge badge-green">Active</span></td>
      <td>
        <button class="btn btn-sm btn-outline">Edit</button>
        <button class="btn btn-sm" style="background:rgba(230,30,37,0.1);color:var(--red);border:1px solid rgba(230,30,37,0.2);">Del</button>
      </td>`;
    tbody.prepend(row);
    wireManageMemberButtons();
    closeModal('add-member-modal');

    ['add-member-fname', 'add-member-lname', 'add-member-email', 'add-member-phone'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    const planEl = document.getElementById('add-member-plan');
    if (planEl) planEl.selectedIndex = 0;

    showToast(`New member added${phone ? ' (' + phone + ')' : ''}!`, 'success');
  }

  function editMemberRow(btn) {
    const row = btn.closest('tr');
    if (!row) return;
    const cells = row.querySelectorAll('td');
    if (cells.length < 7) return;
    const newName   = prompt('Edit member name:',   cells[1].textContent.trim());
    if (newName === null) return;
    const newPlan   = prompt('Edit member plan:',   cells[3].textContent.trim());
    if (newPlan === null) return;
    const newExpiry = prompt('Edit expiry date:',   cells[4].textContent.trim());
    if (newExpiry === null) return;
    if (newName.trim())   cells[1].textContent = newName.trim();
    if (newPlan.trim())   cells[3].textContent = newPlan.trim();
    if (newExpiry.trim()) cells[4].textContent = newExpiry.trim();
    showToast('Member updated successfully', 'success');
  }

  function deleteMemberRow(btn) {
    const row  = btn.closest('tr');
    if (!row) return;
    const name = row.querySelectorAll('td')[1]?.textContent.trim() || 'this member';
    if (!confirm(`Delete ${name}?`)) return;
    row.remove();
    showToast('Member deleted', 'success');
  }

  function wireManageMemberButtons() {
    document.querySelectorAll('#members-table tbody tr').forEach(row => {
      const buttons = row.querySelectorAll('td:last-child button');
      if (buttons.length < 2) return;
      buttons[0].onclick = function () { editMemberRow(this); };
      buttons[1].onclick = function () { deleteMemberRow(this); };
    });
  }

  /** Analytics report generator */
  function generateAnalyticsReport(type) {
    const panel = document.getElementById('report-output-panel');
    const title = document.getElementById('report-output-title');
    const body  = document.getElementById('report-output-body');
    if (!panel || !title || !body) return;

    const data = {
      membership: {
        title: 'Membership Report',
        stats: [{ label: 'Active Members', value: '193' }, { label: 'Expired Members', value: '41' }, { label: 'Pending Members', value: '14' }],
        headers: ['Member', 'Plan', 'Status', 'Expiry'],
        rows: [['Maria Santos', 'Monthly', 'Active', 'May 10, 2026'], ['Jose Reyes', 'Annual', 'Active', 'Apr 10, 2027'], ['Ana Cruz', 'Daily', 'Pending', 'Apr 10, 2026'], ['Carlo Dela Rosa', 'Quarterly', 'Expired', 'Jan 5, 2026']],
        chartLabel: 'Membership Status',
        chartSeries: [{ label: 'Active', value: 193 }, { label: 'Expired', value: 41 }, { label: 'Pending', value: 14 }]
      },
      revenue: {
        title: 'Revenue Report',
        stats: [{ label: 'Daily Revenue', value: 'PHP 12,450' }, { label: 'Monthly Revenue', value: 'PHP 86,320' }, { label: 'Collection Rate', value: '97%' }],
        headers: ['Date', 'Transactions', 'Method Split', 'Total Revenue'],
        rows: [['Apr 10, 2026', '42', 'GCash 52% / Cash 35% / Maya 13%', 'PHP 12,450'], ['Apr 09, 2026', '38', 'GCash 48% / Cash 39% / Maya 13%', 'PHP 10,980'], ['Apr 08, 2026', '36', 'GCash 50% / Cash 37% / Maya 13%', 'PHP 10,350']],
        chartLabel: 'Revenue Trend (PHP)',
        chartSeries: [{ label: 'Week 1', value: 72300 }, { label: 'Week 2', value: 81200 }, { label: 'Week 3', value: 86320 }, { label: 'Week 4', value: 79880 }]
      },
      attendance: {
        title: 'Attendance Report',
        stats: [{ label: "Today's Check-ins", value: '67' }, { label: 'Avg Daily Attendance', value: '61' }, { label: 'Peak Hour', value: '6–8 AM' }],
        headers: ['Member', 'Visits This Month', 'Last Visit', 'Trend'],
        rows: [['Maria Santos', '18', 'Apr 10, 2026', '↑ Up'], ['Jose Reyes', '22', 'Apr 10, 2026', '→ Steady'], ['Pia Magno', '15', 'Apr 9, 2026', '↑ Up'], ['Ben Torres', '9', 'Apr 10, 2026', '↓ Down']],
        chartLabel: 'Weekly Check-ins',
        chartSeries: [{ label: 'Mon', value: 54 }, { label: 'Tue', value: 61 }, { label: 'Wed', value: 58 }, { label: 'Thu', value: 63 }, { label: 'Fri', value: 67 }]
      }
    };

    const report = JSON.parse(JSON.stringify(data[type]));
    if (!report) return;

    const range = document.getElementById('report-range')?.value || 'monthly';
    const multipliers = { daily: 0.3, weekly: 0.7, monthly: 1, yearly: 2.8 };
    const multiplier  = multipliers[range] || 1;
    report.chartSeries = report.chartSeries.map(item => ({ label: item.label, value: Math.max(1, Math.round(item.value * multiplier)) }));

    currentReportType    = type;
    currentReportPayload = report;

    const fromDate = document.getElementById('report-from')?.value;
    const toDate   = document.getElementById('report-to')?.value;
    const dateTxt  = (fromDate && toDate) ? ` | ${fromDate} to ${toDate}` : '';

    title.textContent = report.title + ' — Generated ' + new Date().toLocaleString() + dateTxt;
    body.innerHTML = `
      <div class="stats-grid" style="grid-template-columns:repeat(3,1fr);margin-bottom:14px;">
        ${report.stats.map(s => `<div class="stat-card"><div class="stat-value" style="font-size:24px;">${s.value}</div><div class="stat-label">${s.label}</div></div>`).join('')}
      </div>
      <div style="margin-bottom:16px;">
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px;">${report.chartLabel}</div>
        ${_renderReportBars(report.chartSeries)}
      </div>
      <table class="data-table">
        <thead><tr>${report.headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
        <tbody>${report.rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>
      </table>`;

    panel.style.display = 'block';
    showToast(report.title + ' generated successfully', 'success');
  }

  function refreshCurrentReport() {
    if (currentReportType) generateAnalyticsReport(currentReportType);
  }

  function exportReportCSV() {
    if (!currentReportPayload) { showToast('Generate a report first', 'error'); return; }
    const headers = currentReportPayload.headers.join(',');
    const rows    = currentReportPayload.rows.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n');
    const csv     = `${headers}\n${rows}`;
    const blob    = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link    = document.createElement('a');
    link.href     = URL.createObjectURL(blob);
    link.download = `${currentReportType}-report.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
    showToast('CSV exported', 'success');
  }

  function exportReportPDF() {
    if (!currentReportPayload) { showToast('Generate a report first', 'error'); return; }
    window.print();
    showToast('Use Print dialog to save as PDF', 'success');
  }

  // ── Private helpers ──────────────────────────
  function _renderReportBars(series) {
    const maxVal = Math.max(...series.map(s => s.value));
    return series.map(s => {
      const width = Math.max(6, Math.round((s.value / maxVal) * 100));
      return `<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
        <div style="width:68px;font-size:12px;color:var(--muted);">${s.label}</div>
        <div style="flex:1;background:rgba(255,255,255,0.06);border-radius:999px;height:10px;overflow:hidden;">
          <div style="width:${width}%;height:100%;background:linear-gradient(90deg,var(--red),var(--navy-light));transition:width 0.6s ease;"></div>
        </div>
        <div style="width:80px;text-align:right;font-size:12px;">${s.value}</div>
      </div>`;
    }).join('');
  }

  function _calculateExpiry(planName) {
    const expiry = new Date();
    const plan   = planName.toLowerCase();
    if (plan === 'annual')        expiry.setDate(expiry.getDate() + 365);
    else if (plan === 'quarterly') expiry.setDate(expiry.getDate() + 90);
    else                           expiry.setDate(expiry.getDate() + 30);
    return expiry;
  }

  return {
    init, tab, addMember, editMemberRow, deleteMemberRow, wireManageMemberButtons,
    generateAnalyticsReport, refreshCurrentReport, exportReportCSV, exportReportPDF
  };
})();


/* ════════════════════════════════════════════════
   5. STAFF MODULE
   Handles staff dashboard functionality.
════════════════════════════════════════════════ */
const StaffModule = (() => {

  function init() {
    const session = Session.guardDashboard();
    if (!session) return;

    _injectSidebarUser(session);
    document.body.classList.add('role-staff');
    _bindModalBackdrops();
    Navigation.activateTab('staff', 'overview', document.getElementById('nav-staff-overview'));
  }

  function tab(tabName, navEl) {
    Navigation.activateTab('staff', tabName, navEl);
  }

  return { init, tab };
})();


/* ════════════════════════════════════════════════
   6. MEMBER MODULE
   Handles member dashboard functionality.
════════════════════════════════════════════════ */
const MemberModule = (() => {

  let selectedPlan    = null;
  let renewalTxnCnt   = 9000;

  function init() {
    const session = Session.guardDashboard();
    if (!session) return;

    _injectSidebarUser(session);
    document.body.classList.add('role-member');

    // Populate member card
    const cardName = document.getElementById('member-card-name');
    if (cardName) cardName.textContent = session.name;

    buildAttGrid('att-grid-member', [1, 3, 4, 6, 7, 8, 9, 10]);
    _bindModalBackdrops();
    Navigation.activateTab('member', 'overview', document.getElementById('nav-member-overview'));
  }

  function tab(tabName, navEl) {
    Navigation.activateTab('member', tabName, navEl);
  }

  /** Preview uploaded renewal proof image */
  function previewRenewProof(input) {
    const file    = input.files && input.files[0];
    const preview = document.getElementById('member-renew-preview');
    if (!preview) return;
    if (!file) { preview.style.display = 'none'; preview.removeAttribute('src'); return; }
    preview.src           = URL.createObjectURL(file);
    preview.style.display = 'block';
  }

  /** Submit renewal payment and push to admin queue */
  function submitRenewalPayment() {
    const methodEl = document.getElementById('member-renew-method');
    const refEl    = document.getElementById('member-renew-ref');
    const proofEl  = document.getElementById('member-renew-proof');
    const file     = proofEl && proofEl.files && proofEl.files[0];

    if (!selectedPlan)           { showToast('Please select a plan first', 'error'); return; }
    if (!methodEl?.value || !refEl?.value.trim() || !file) {
      showToast('Complete method, reference number, and screenshot proof', 'error');
      return;
    }

    renewalTxnCnt++;
    // Persist pending payment in localStorage so admin page can pick it up
    try {
      const pending = JSON.parse(localStorage.getItem('trmem_pending_payments') || '[]');
      const session = Auth.getSession();
      pending.push({
        name:     session ? session.name : 'Member',
        method:   methodEl.value,
        ref:      refEl.value.trim(),
        txn:      '#TXN-' + renewalTxnCnt,
        plan:     selectedPlan,
        ts:       new Date().toISOString()
      });
      localStorage.setItem('trmem_pending_payments', JSON.stringify(pending));
    } catch (e) { /* ignore */ }

    methodEl.value = '';
    refEl.value    = '';
    proofEl.value  = '';
    const preview = document.getElementById('member-renew-preview');
    if (preview) { preview.style.display = 'none'; preview.removeAttribute('src'); }
    showToast('Payment proof submitted! Awaiting admin verification.', 'success');
  }

  /** Expose plan selection for the renewal grid */
  function selectRenewalPlan(card, plan) {
    selectedPlan = plan;
    document.querySelectorAll('#member-membership .plan-card').forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');
    showToast('Plan selected: ' + plan.charAt(0).toUpperCase() + plan.slice(1), 'success');
  }

  return { init, tab, previewRenewProof, submitRenewalPayment, selectRenewalPlan };
})();


/* ════════════════════════════════════════════════
   7. LOGIN PAGE MODULE
   Runs on trmem.html only.
════════════════════════════════════════════════ */
const LoginPage = (() => {

  let selectedPlan    = null;
  let selectedPayment = null;

  function init() {
    // Always show the login/register page first when opening the root URL.
    // Existing session remains in storage, but we do not automatically redirect.
    const params = new URLSearchParams(window.location.search);
    if (params.get('screen') === 'register') Navigation.goToScreen('register');

    // Modal backdrop binding
    _bindModalBackdrops();
  }

  function detectRoleHint() {
    const email = document.getElementById('login-email')?.value || '';
    Navigation.showRoleHint(Auth.detectRole(email));
  }

  function handleLogin() {
    const email = (document.getElementById('login-email')?.value || '').trim();
    const pass  = (document.getElementById('login-pass')?.value  || '');

    if (!email || !pass) { showToast('Please enter your email and password', 'error'); return; }

    const result = Auth.login(email, pass);
    if (!result.success) {
      showToast(result.error, 'error');
      const box = document.querySelector('.login-box');
      if (box) { box.classList.add('shake'); setTimeout(() => box.classList.remove('shake'), 500); }
      return;
    }

    showToast(`Welcome back, ${result.session.name}! Redirecting...`, 'success');
    setTimeout(() => Session.redirectToRole(result.session.role), 700);
  }

  function doLogout() {
    Auth.logout();
    const emailEl = document.getElementById('login-email');
    const passEl  = document.getElementById('login-pass');
    if (emailEl) emailEl.value = '';
    if (passEl)  passEl.value  = '';
    const bar = document.getElementById('role-hint-bar');
    if (bar) bar.style.display = 'none';
    const tag = document.getElementById('login-role-tag');
    if (tag) tag.textContent = '\u00a0';
    Navigation.goToScreen('login');
    showToast('Signed out successfully', 'success');
  }

  // ── Registration flow ──
  function regNext(step) {
    for (let i = 1; i <= 4; i++) {
      const s = document.getElementById('reg-step-' + i);
      const d = document.getElementById('sdot-' + i);
      if (s) s.style.display = 'none';
      if (d) d.classList.remove('active');
    }
    const target = document.getElementById('reg-step-' + step);
    if (target) target.style.display = 'block';
    for (let i = 1; i <= step; i++) {
      const dot = document.getElementById('sdot-' + i);
      if (dot) dot.classList.add('active');
    }
  }

  function proceedToPayment() {
    const check = document.getElementById('reg-terms-check');
    if (!check || !check.checked) { showToast('Please agree to the Terms & Policy first', 'error'); return; }
    regNext(4);
  }

  function selectPlan(card, plan) {
    document.querySelectorAll('.plan-card').forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');
    selectedPlan = plan;
  }

  function selectPayment(opt, method) {
    document.querySelectorAll('.pay-opt').forEach(o => o.classList.remove('selected'));
    opt.classList.add('selected');
    selectedPayment = method;
    const upload   = document.getElementById('pay-upload');
    const cashNote = document.getElementById('pay-cash-note');
    if (upload)   upload.style.display   = method === 'gcash' ? 'block' : 'none';
    if (cashNote) cashNote.style.display = method === 'cash'  ? 'block' : 'none';
  }

  function completeRegistration() {
    const emailInput = document.querySelector('#reg-step-1 input[type="email"]');
    const passInput  = document.querySelector('#reg-step-1 input[type="password"]');
    const fnInput    = document.querySelector('#reg-step-1 input[placeholder="Juan"]');
    const lnInput    = document.querySelector('#reg-step-1 input[placeholder="Dela Cruz"]');

    const email    = emailInput ? emailInput.value.trim() : '';
    const password = passInput  ? passInput.value         : '';
    const name     = ((fnInput ? fnInput.value.trim() : '') + ' ' + (lnInput ? lnInput.value.trim() : '')).trim() || 'New Member';

    if (email && password) Auth.registerMember(email, password, name);

    showToast('Registration submitted! Awaiting verification.', 'success');
    setTimeout(() => Navigation.goToScreen('login'), 1200);
  }

  return { init, detectRoleHint, handleLogin, doLogout, regNext, proceedToPayment, selectPlan, selectPayment, completeRegistration };
})();


/* ════════════════════════════════════════════════
   8. SHARED UTILITIES
   Functions used across multiple modules / pages.
════════════════════════════════════════════════ */

/** Build a 30-day attendance dot grid */
function buildAttGrid(elId, presentDays) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.innerHTML = '';
  for (let d = 1; d <= 30; d++) {
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

/** Payment verification (used by admin) */
function verifyPayment(btn, action) {
  const card  = btn.closest('.verify-card');
  const badge = card ? card.querySelector('.badge') : null;
  if (!badge) return;
  if (action === 'approve') {
    badge.className   = 'badge badge-green';
    badge.textContent = 'Approved ✓';
    showToast('Payment approved — Membership activated!', 'success');
  } else {
    badge.className   = 'badge badge-red';
    badge.textContent = 'Rejected ✗';
    showToast('Payment rejected', 'error');
  }
  card.querySelectorAll('button').forEach(b => b.remove());
}

/** Plan card selection (generic — used on register page and member renewal) */
function selectPlan(card, plan) {
  // Scope to the nearest plan-grid parent to avoid cross-section conflicts
  const grid = card.closest('.plan-grid');
  if (grid) grid.querySelectorAll('.plan-card').forEach(c => c.classList.remove('selected'));
  card.classList.add('selected');
}

/** Logout — works from any dashboard page */
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
   9. TOAST SYSTEM
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
   10. INIT — DOMContentLoaded Bootstrap
   Each page calls its own module init based on
   the presence of a page-identifying element.
════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {

  // ── Login / Register page ──
  if (document.getElementById('screen-login')) {
    LoginPage.init();

    // Expose login-page functions to inline onclick handlers
    window.detectRoleHint      = LoginPage.detectRoleHint;
    window.handleLogin         = LoginPage.handleLogin;
    window.regNext             = LoginPage.regNext;
    window.proceedToPayment    = LoginPage.proceedToPayment;
    window.selectPlan          = LoginPage.selectPlan;
    window.selectPayment       = LoginPage.selectPayment;
    window.completeRegistration = LoginPage.completeRegistration;
    window.goTo = (screen) => Navigation.goToScreen(screen);
  }

  // ── Admin Dashboard ──
  if (document.getElementById('admin-dashboard-root')) {
    AdminModule.init();

    window.adminTab                = (tab, el) => AdminModule.tab(tab, el);
    window.addMember               = AdminModule.addMember;
    window.generateAnalyticsReport = AdminModule.generateAnalyticsReport;
    window.refreshCurrentReport    = AdminModule.refreshCurrentReport;
    window.exportCurrentReportCSV  = AdminModule.exportReportCSV;
    window.exportCurrentReportPDF  = AdminModule.exportReportPDF;
    window.filterTable             = filterTable;
    window.verifyPayment           = verifyPayment;
  }

  // ── Staff Dashboard ──
  if (document.getElementById('staff-dashboard-root')) {
    StaffModule.init();
    window.staffTab = (tab, el) => StaffModule.tab(tab, el);
  }

  // ── Member Dashboard ──
  if (document.getElementById('member-dashboard-root')) {
    MemberModule.init();
    window.memberTab             = (tab, el) => MemberModule.tab(tab, el);
    window.previewRenewProof     = MemberModule.previewRenewProof;
    window.submitRenewalPayment  = MemberModule.submitRenewalPayment;
    window.selectPlan            = MemberModule.selectRenewalPlan;
  }

  // ── Global always-available functions ──
  window.openModal     = openModal;
  window.closeModal    = closeModal;
  window.showToast     = showToast;
  window.buildAttGrid  = buildAttGrid;
  window.doLogout      = doLogout;
  window.verifyPayment = verifyPayment;
  // selectPlan is re-assigned per page above but keep a fallback
  if (!window.selectPlan) window.selectPlan = selectPlan;
});
