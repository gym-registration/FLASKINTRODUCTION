/* ═══════════════════════════════════════════════════════════════
   POWER GYM — Staff Dashboard
   tr-staff.js  |  Runs on staff-dashboard.html only

   Requires tr-common.js to be loaded first (Session, Navigation,
   showToast, _injectSidebarUser, _bindModalBackdrops, _val).
   ═══════════════════════════════════════════════════════════════ */

'use strict';

/* ════════════════════════════════════════════════
   STAFF MODULE
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

  /** Record a front-desk payment and persist it to the database */
  function recordPayment() {
    const memberIdentifier = _val('pay-member');
    const planText          = document.getElementById('pay-plan')?.value   || '';
    const method            = document.getElementById('pay-method')?.value || '';
    const reference          = _val('pay-reference');
    const planName          = planText.split('—')[0].trim();

    if (!memberIdentifier) {
      showToast('Please enter the member name, ID, or email', 'error');
      return;
    }

    const btn = document.querySelector('#staff-payments .btn-red');
    if (btn) { btn.disabled = true; btn.textContent = 'RECORDING...'; }

    fetch('/staff/record-payment', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        member_identifier: memberIdentifier,
        plan:    planName,
        method:  method,
        reference: reference
      })
    })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (btn) { btn.disabled = false; btn.textContent = 'RECORD PAYMENT'; }
        if (!ok || !data.success) {
          showToast(data.error || 'Failed to record payment.', 'error');
          return;
        }

        ['pay-member', 'pay-reference'].forEach(id => {
          const el = document.getElementById(id);
          if (el) el.value = '';
        });
        const planEl = document.getElementById('pay-plan');
        if (planEl) planEl.selectedIndex = 0;

        showToast(`Payment recorded for ${data.payment.member_name} — membership active until ${data.payment.expiry}`, 'success');
      })
      .catch(() => {
        if (btn) { btn.disabled = false; btn.textContent = 'RECORD PAYMENT'; }
        showToast('Could not reach the server. Please try again.', 'error');
      });
  }

  /** Check a member in via the given input field's id */
  function checkInMember(inputId) {
    const identifier = _val(inputId);
    if (!identifier) { showToast('Please enter a member name, ID, or email', 'error'); return; }

    fetch('/staff/checkin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ member_identifier: identifier })
    })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.success) {
          showToast(data.error || 'Check-in failed.', 'error');
          return;
        }
        showToast(`${data.member_name} checked in at ${data.time}`, 'success');
        setTimeout(() => window.location.reload(), 900);
      })
      .catch(() => showToast('Could not reach the server. Please try again.', 'error'));
  }

  /** Check a member out via the given input field's id */
  function checkOutMember(inputId) {
    const identifier = _val(inputId);
    if (!identifier) { showToast('Please enter a member name, ID, or email', 'error'); return; }

    fetch('/staff/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ member_identifier: identifier })
    })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.success) {
          showToast(data.error || 'Check-out failed.', 'error');
          return;
        }
        showToast(`${data.member_name} checked out at ${data.time} (${data.duration})`, 'success');
        setTimeout(() => window.location.reload(), 900);
      })
      .catch(() => showToast('Could not reach the server. Please try again.', 'error'));
  }

  return { init, tab, recordPayment, checkInMember, checkOutMember };
})();


/* ════════════════════════════════════════════════
   INIT — DOMContentLoaded Bootstrap
════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('staff-dashboard-root')) return;

  StaffModule.init();

  window.staffTab       = (tab, el) => StaffModule.tab(tab, el);
  window.recordPayment  = () => StaffModule.recordPayment();
  window.checkInMember  = (inputId) => StaffModule.checkInMember(inputId);
  window.checkOutMember = (inputId) => StaffModule.checkOutMember(inputId);
});
