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
