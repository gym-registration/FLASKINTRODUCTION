/* ═══════════════════════════════════════════════════════════════
   POWER GYM — Member Dashboard
   tr-member.js  |  Runs on member-dashboard.html only

   Requires tr-common.js to be loaded first (Auth, Session, Navigation,
   showToast, buildAttGrid, _injectSidebarUser, _bindModalBackdrops).
   ═══════════════════════════════════════════════════════════════ */

'use strict';

/* ════════════════════════════════════════════════
   MEMBER MODULE
   Handles member dashboard functionality.
════════════════════════════════════════════════ */
const MemberModule = (() => {

  let selectedPlan    = null;

  function init() {
    const session = Session.guardDashboard();
    if (!session) return;

    _injectSidebarUser(session);
    document.body.classList.add('role-member');

    // Populate member card
    const cardName = document.getElementById('member-card-name');
    if (cardName) cardName.textContent = session.name;

    const memberData = _parseMemberDashboardData();
    buildAttGrid('att-grid-member', memberData.present_days || [], memberData.days_in_month || 30);
    _hydrateProgressBars();
    _bindModalBackdrops();
    Navigation.activateTab('member', 'overview', document.getElementById('nav-member-overview'));
  }

  function _parseMemberDashboardData() {
    const el = document.getElementById('member-dashboard-data');
    if (!el) return {};
    try {
      return JSON.parse(el.textContent || el.innerText || '{}');
    } catch (e) {
      return {};
    }
  }

  function _hydrateProgressBars() {
    document.querySelectorAll('.progress-fill[data-width]').forEach(el => {
      const width = Number(el.dataset.width);
      if (!Number.isNaN(width)) {
        el.style.width = `${width}%`;
      }
    });
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

  /** Submit plan payment (new plan or renewal) to the backend for admin verification */
  function submitRenewalPayment() {
    const methodEl = document.getElementById('member-renew-method');
    const refEl    = document.getElementById('member-renew-ref');
    const proofEl  = document.getElementById('member-renew-proof');
    const file     = proofEl && proofEl.files && proofEl.files[0];

    if (!selectedPlan) { showToast('Please select a plan first', 'error'); return; }
    if (!methodEl?.value) { showToast('Please choose a payment method', 'error'); return; }
    if (methodEl.value === 'GCash' && (!refEl?.value.trim() || !file)) {
      showToast('GCash payments need a reference number and screenshot proof', 'error');
      return;
    }

    const formData = new FormData();
    formData.append('plan',      selectedPlan);
    formData.append('method',    methodEl.value);
    formData.append('reference', refEl?.value.trim() || '');
    if (file) formData.append('proof', file);

    const btn = document.querySelector('#member-membership .btn-red');
    const originalLabel = btn ? btn.textContent : 'SUBMIT PAYMENT';
    if (btn) { btn.disabled = true; btn.textContent = 'SUBMITTING...'; }

    fetch('/member/submit-payment', { method: 'POST', body: formData })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
        if (!ok || !data.success) {
          showToast(data.error || 'Could not submit payment.', 'error');
          return;
        }

        methodEl.value = '';
        if (refEl) refEl.value = '';
        if (proofEl) proofEl.value = '';
        const preview = document.getElementById('member-renew-preview');
        if (preview) { preview.style.display = 'none'; preview.removeAttribute('src'); }

        showToast(data.message || 'Payment submitted! Awaiting admin verification.', 'success');
        setTimeout(() => window.location.reload(), 1200);
      })
      .catch(() => {
        if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
        showToast('Could not reach the server. Please try again.', 'error');
      });
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
   INIT — DOMContentLoaded Bootstrap
════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('member-dashboard-root')) return;

  MemberModule.init();

  window.memberTab            = (tab, el) => MemberModule.tab(tab, el);
  window.previewRenewProof    = MemberModule.previewRenewProof;
  window.submitRenewalPayment = MemberModule.submitRenewalPayment;
  window.selectPlan           = MemberModule.selectRenewalPlan;
});