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

    _tickLiveDurations();
    setInterval(_tickLiveDurations, 1000);
  }

  /** Update every "Ongoing" duration cell to show real elapsed time, ticking every second */
  function _tickLiveDurations() {
    const now = Date.now();
    document.querySelectorAll('.live-duration[data-checkin]').forEach(el => {
      const start = new Date(el.dataset.checkin).getTime();
      if (Number.isNaN(start)) return;
      const totalSeconds = Math.max(0, Math.floor((now - start) / 1000));
      const h = Math.floor(totalSeconds / 3600);
      const m = Math.floor((totalSeconds % 3600) / 60);
      const s = totalSeconds % 60;
      el.textContent = h > 0
        ? `${h}h ${m}m ${s}s`
        : `${m}m ${s}s`;
    });
  }

  function tab(tabName, navEl) {
    Navigation.activateTab('staff', tabName, navEl);
  }

  // ── Payment Record: member autocomplete + plan auto-fill ──
  let _paymentMembers = null;

  /** Lazily parse the JSON data block (id/name/email/plan/status per member),
   *  embedded server-side so the lookup works without an extra fetch. */
  function _loadPaymentMembers() {
    if (_paymentMembers) return _paymentMembers;
    const el = document.getElementById('staff-payment-members-data');
    try {
      _paymentMembers = el ? JSON.parse(el.textContent) : [];
    } catch (e) {
      _paymentMembers = [];
    }
    return _paymentMembers;
  }

  /** Select the <option> in #pay-plan whose plan name (text before the
   *  "—") matches the given plan name, case-insensitively. No-op if the
   *  plan isn't one of the listed options. */
  function _selectPlanByName(planName) {
    const select = document.getElementById('pay-plan');
    if (!select || !planName) return;
    const target = planName.trim().toLowerCase();
    for (const opt of select.options) {
      const optPlanName = opt.textContent.split('—')[0].trim().toLowerCase();
      if (optPlanName === target) {
        select.value = opt.value;
        return;
      }
    }
  }

  /** Fired on every keystroke / dropdown pick in the Member field. When the
   *  typed value exactly matches a known member's email (which is what the
   *  datalist option value is set to — picking a suggestion fills the input
   *  with it), auto-select that member's currently availed plan. */
  function onPayMemberInput(value) {
    const identifier = (value || '').trim().toLowerCase();
    if (!identifier) return;

    const members = _loadPaymentMembers();
    const match = members.find(m => (m.email || '').toLowerCase() === identifier);
    if (!match || !match.plan || match.plan === '—') return;

    _selectPlanByName(match.plan);
  }

  /** Record a front-desk payment and persist it to the database */
  function recordPayment() {
    const memberIdentifier = _val('pay-member');
    const planText          = document.getElementById('pay-plan')?.value   || '';
    const method            = document.getElementById('pay-method')?.value || '';
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
        method:  method
      })
    })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (btn) { btn.disabled = false; btn.textContent = 'RECORD PAYMENT'; }
        if (!ok || !data.success) {
          showToast(data.error || 'Failed to record payment.', 'error');
          return;
        }

        const memberEl = document.getElementById('pay-member');
        if (memberEl) memberEl.value = '';
        const planEl = document.getElementById('pay-plan');
        if (planEl) planEl.selectedIndex = 0;

        showToast(`Payment recorded for ${data.payment.member_name} — membership active until ${data.payment.expiry}`, 'success');
      })
      .catch(() => {
        if (btn) { btn.disabled = false; btn.textContent = 'RECORD PAYMENT'; }
        showToast('Could not reach the server. Please try again.', 'error');
      });
  }

  /** Resolve an argument that may be an <input>/<select> element id, or a raw
   *  identifier (e.g. an email passed straight from a table row button). */
  function _resolveIdentifier(idOrValue) {
    const el = document.getElementById(idOrValue);
    return el ? (el.value || '').trim() : (idOrValue || '').trim();
  }

  /** Check a member in via the given input field's id, or a raw identifier */
  function checkInMember(idOrValue) {
    const identifier = _resolveIdentifier(idOrValue);
    if (!identifier) { showToast('Please select a member', 'error'); return; }

    const row = _findCheckinRow(identifier);
    const btn = row ? row.querySelector('.cell-action button') : null;
    const originalLabel = btn ? btn.textContent : null;
    if (btn) { btn.disabled = true; btn.textContent = '...'; }

    fetch('/staff/checkin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ member_identifier: identifier })
    })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.success) {
          if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
          showToast(data.error || 'Check-in failed.', 'error');
          return;
        }
        showToast(`${data.member_name} checked in at ${data.time}`, 'success');
        _applyRowCheckIn(row, identifier, data.time);
      })
      .catch(() => {
        if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
        showToast('Could not reach the server. Please try again.', 'error');
      });
  }

  /** Check a member out via the given input field's id, or a raw identifier */
  function checkOutMember(idOrValue) {
    const identifier = _resolveIdentifier(idOrValue);
    if (!identifier) { showToast('Please select a member', 'error'); return; }

    const row = _findCheckinRow(identifier);
    const btn = row ? row.querySelector('.cell-action button') : null;
    const originalLabel = btn ? btn.textContent : null;
    if (btn) { btn.disabled = true; btn.textContent = '...'; }

    fetch('/staff/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ member_identifier: identifier })
    })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.success) {
          if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
          showToast(data.error || 'Check-out failed.', 'error');
          return;
        }
        showToast(`${data.member_name} checked out at ${data.time} (${data.duration})`, 'success');
        _applyRowCheckOut(row, identifier, data.time, data.duration);
      })
      .catch(() => {
        if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
        showToast('Could not reach the server. Please try again.', 'error');
      });
  }

  /** Find a member's row in the Check-in/Out table by their email (data-email) */
  function _findCheckinRow(email) {
    if (!email) return null;
    return document.querySelector(`#checkin-table tbody tr[data-email="${CSS.escape(email)}"]`) || null;
  }

  /** Patch a table row in place after a successful check-in — no page reload */
  function _applyRowCheckIn(row, email, timeText) {
    if (!row) return;
    const checkInCell  = row.querySelector('.cell-checkin');
    const durationCell = row.querySelector('.cell-duration');
    const actionCell   = row.querySelector('.cell-action');
    if (checkInCell)  checkInCell.textContent = timeText;
    if (durationCell) durationCell.innerHTML = `<span class="live-duration" data-checkin="${new Date().toISOString()}">Ongoing</span>`;
    if (actionCell)   actionCell.innerHTML = `<button class="btn btn-outline btn-sm" onclick="checkOutMember('${email}')">← CHECK OUT</button>`;
  }

  /** Patch a table row in place after a successful check-out — no page reload */
  function _applyRowCheckOut(row, email, timeText, durationText) {
    if (!row) return;
    const checkOutCell = row.querySelector('.cell-checkout');
    const durationCell  = row.querySelector('.cell-duration');
    const actionCell    = row.querySelector('.cell-action');
    if (checkOutCell) checkOutCell.textContent = timeText;
    if (durationCell) durationCell.textContent = durationText;
    if (actionCell)   actionCell.innerHTML = `<button class="btn btn-green btn-sm" onclick="checkInMember('${email}')">✓ CHECK IN</button>`;
  }

  /** Filter the Check-in/Out member table by name as the staff member types */
  function filterCheckinTable(term) {
    const search = (term || '').trim().toLowerCase();
    document.querySelectorAll('#checkin-table tbody tr[data-name]').forEach(row => {
      const match = row.dataset.name.includes(search);
      row.style.display = match ? '' : 'none';
    });
  }

  // ── Member Directory: status pill + search filtering ──
  let membersStatusFilter = 'all';

  /** Called when a status pill (All / Active / Pending / Expired / No Plan) is clicked */
  function filterMembersByStatus(status, pillEl) {
    membersStatusFilter = status;
    document.querySelectorAll('#staff-members .status-pill').forEach(p => p.classList.remove('active'));
    if (pillEl) pillEl.classList.add('active');
    _applyMembersFilter();
  }

  /** Called as the staff member types in the Member Directory search box */
  function filterMembersTable() {
    _applyMembersFilter();
  }

  function _applyMembersFilter() {
    const searchEl = document.getElementById('members-search');
    const search   = (searchEl?.value || '').trim().toLowerCase();
    const rows     = document.querySelectorAll('#members-table tbody tr[data-status]');
    let visibleCount = 0;

    rows.forEach(row => {
      const statusMatch = membersStatusFilter === 'all' || row.dataset.status === membersStatusFilter;
      const nameMatch    = !search || (row.dataset.name || '').includes(search);
      const show = statusMatch && nameMatch;
      row.style.display = show ? '' : 'none';
      if (show) visibleCount++;
    });

    const emptyState = document.getElementById('members-empty-state');
    if (emptyState) emptyState.style.display = (rows.length && visibleCount === 0) ? 'block' : 'none';
  }

  /** Show the uploaded payment proof (image or PDF) in a modal before approving/rejecting */
  function viewPaymentProof(url, title) {
    const img      = document.getElementById('proof-modal-img');
    const pdfNote  = document.getElementById('proof-modal-pdf-note');
    const pdfLink  = document.getElementById('proof-modal-pdf-link');
    const titleEl  = document.getElementById('proof-modal-title');
    if (!img || !pdfNote || !pdfLink) return;

    if (titleEl) titleEl.textContent = (title || 'Payment Proof').toUpperCase();

    const isPdf = /\.pdf($|\?)/i.test(url);

    if (isPdf) {
      img.style.display = 'none';
      img.removeAttribute('src');
      pdfLink.href = url;
      pdfNote.style.display = 'block';
    } else {
      pdfNote.style.display = 'none';
      img.src = url;
      img.style.display = 'block';
    }

    openModal('view-proof-modal');
  }

  return { init, tab, recordPayment, checkInMember, checkOutMember, filterCheckinTable, filterMembersByStatus, filterMembersTable, viewPaymentProof, onPayMemberInput };
})();


/* ════════════════════════════════════════════════
   INIT — DOMContentLoaded Bootstrap
════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('staff-dashboard-root')) return;

  StaffModule.init();

  window.staffTab       = (tab, el) => StaffModule.tab(tab, el);
  window.recordPayment  = () => StaffModule.recordPayment();
  window.checkInMember  = (idOrValue) => StaffModule.checkInMember(idOrValue);
  window.checkOutMember = (idOrValue) => StaffModule.checkOutMember(idOrValue);
  window.filterCheckinTable   = (term) => StaffModule.filterCheckinTable(term);
  window.filterMembersByStatus = (status, el) => StaffModule.filterMembersByStatus(status, el);
  window.filterMembersTable    = () => StaffModule.filterMembersTable();
  window.viewPaymentProof      = (url, title) => StaffModule.viewPaymentProof(url, title);
  window.onPayMemberInput      = (value) => StaffModule.onPayMemberInput(value);
});