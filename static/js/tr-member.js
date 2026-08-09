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
  const LOCKED_TABS   = [];
  let attendanceView  = { year: null, month: null };
  let attendanceBusy  = false;

  // Populated at init() from JSON the server embeds for the plans/services
  // that admin/staff manage under Settings → Manage Content.
  let PLAN_DATA    = {}; // keyed by plan name lowercased, e.g. 'monthly'
  let SERVICE_DATA = {}; // keyed by service id

  function _planActive() {
    const root = document.getElementById('member-dashboard-root');
    return root ? root.dataset.planActive === 'true' : true;
  }

  function init() {
    const session = Session.guardDashboard();
    if (!session) return;

    _injectSidebarUser(session);
    document.body.classList.add('role-member');

    // Populate member card
    const cardName = document.getElementById('member-card-name');
    if (cardName) cardName.textContent = session.name;

    const memberData = _parseMemberDashboardData();
    attendanceView = { year: memberData.year || null, month: memberData.month || null };
    buildAttGrid('att-grid-member', memberData.present_days || [], memberData.days_in_month || 30, memberData.today_day || null, memberData.no_plan_days || []);
    _updateAttendanceNav(memberData.today_day != null);
    _hydrateProgressBars();
    _bindModalBackdrops();
    _initStartDateField();
    _showApprovalNoticeIfAny(memberData.plan_approved_notice);
    _showPaymentApprovedNoticeIfAny(memberData.payment_verified_notice);
    _showDeclinedNoticeIfAny(memberData.plan_declined_notice);

    // Load membership-plan and service content managed by admin/staff
    // (Settings → Manage Content) — keeps prices, descriptions, and
    // inclusions here in sync with what they edit, instead of hardcoding it.
    (_parseJsonScript('member-plans-data', []) || []).forEach(p => { PLAN_DATA[p.key] = p; });
    (_parseJsonScript('member-services-data', []) || []).forEach(s => { SERVICE_DATA[s.id] = s; });
    _updatePlanPriceDisplays();

    // Members without an active plan land on Overview (which points them to
    // My Membership); everything else stays locked until they pay.
    Navigation.activateTab('member', 'overview', document.getElementById('nav-member-overview'));
  }

  /** Show the one-time "Congratulations! Your plan was approved" popup, if
   *  the server flagged this page load as the first one since approval. */
  function _showApprovalNoticeIfAny(notice) {
    if (!notice) return;
    const msgEl = document.getElementById('plan-approved-message');
    if (msgEl) {
      msgEl.textContent = `Congratulations! Your ${notice.plan_name} plan has been approved. Please proceed to payment.`;
    }
    openModal('plan-approved-modal');
  }

  /** "✕" or "Later" on the approval popup — just dismiss it. */
  function closePlanApprovedModal() {
    closeModal('plan-approved-modal');
  }

  /** "Proceed to Payment" on the approval popup — jump straight to the Payment tab. */
  function goToPaymentFromApproval() {
    closeModal('plan-approved-modal');
    Navigation.activateTab('member', 'payment', document.getElementById('nav-member-payment'));
  }

  /** Show the one-time "Congratulations! Your payment was approved" popup,
   *  including the date the (now-active) membership starts from. */
  function _showPaymentApprovedNoticeIfAny(notice) {
    if (!notice) return;
    const msgEl = document.getElementById('payment-approved-message');
    if (msgEl) {
      msgEl.textContent = `Congratulations! Your payment has been approved. Your ${notice.plan_name} membership plan starts on ${notice.start_date}.`;
    }
    openModal('payment-approved-modal');
  }

  /** "✕" or "OK" on the payment-approved popup — just dismiss it. */
  function closePaymentApprovedModal() {
    closeModal('payment-approved-modal');
  }

  /** Show the one-time "Your request was declined" popup, if the server
   *  flagged this page load as the first one since staff/admin rejected it. */
  function _showDeclinedNoticeIfAny(notice) {
    if (!notice) return;
    const msgEl = document.getElementById('plan-declined-message');
    if (msgEl) {
      msgEl.textContent = `Your ${notice.plan_name} plan request was declined. You can submit a new request from My Membership.`;
    }
    openModal('plan-declined-modal');
  }

  /** "✕" or "OK" on the declined popup — just dismiss it. */
  function closePlanDeclinedModal() {
    closeModal('plan-declined-modal');
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

  /** Parse a JSON <script> tag embedded by the server (e.g. plan/service
   *  content managed by admin/staff). Returns fallback on any failure. */
  function _parseJsonScript(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    try {
      return JSON.parse(el.textContent || el.innerText || 'null') ?? fallback;
    } catch (e) {
      return fallback;
    }
  }

  /** Enable/disable the "NEXT" arrow — members can't browse into the future. */
  function _updateAttendanceNav(isCurrentMonth) {
    const nextBtn = document.getElementById('attendance-next-month');
    if (nextBtn) nextBtn.disabled = !!isCurrentMonth;
  }

  function _escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }

  function _renderAttendanceSessionHistory(rows) {
    const body = document.getElementById('attendance-session-history-body');
    if (!body) return;
    if (!rows || !rows.length) {
      body.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--muted);">No sessions logged this month yet.</td></tr>';
      return;
    }
    body.innerHTML = rows.map(s => `<tr><td>${_escapeHtml(s.date)}</td><td>${_escapeHtml(s.check_in)}</td><td>${_escapeHtml(s.check_out)}</td><td>${_escapeHtml(s.duration)}</td></tr>`).join('');
  }

  /** Back/forward navigation for the "My Attendance" calendar — fetches that
   *  month's data from the server and re-renders in place, no page reload. */
  function changeAttendanceMonth(direction) {
    if (attendanceBusy) return;
    if (!attendanceView.year || !attendanceView.month) return;

    let { year, month } = attendanceView;
    month += direction;
    if (month < 1)  { month = 12; year -= 1; }
    if (month > 12) { month = 1;  year += 1; }

    attendanceBusy = true;
    const prevBtn = document.getElementById('attendance-prev-month');
    const nextBtn = document.getElementById('attendance-next-month');
    const nextWasDisabled = nextBtn ? nextBtn.disabled : false;
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;

    fetch(`/member/attendance-month?year=${year}&month=${month}`)
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.success) {
          showToast(data.error || 'Could not load that month.', 'error');
          if (prevBtn) prevBtn.disabled = false;
          if (nextBtn) nextBtn.disabled = nextWasDisabled;
          return;
        }
        attendanceView = { year: data.year, month: data.month };
        const label = document.getElementById('attendance-month-label');
        if (label) label.textContent = data.month_label;
        buildAttGrid('att-grid-member', data.present_days || [], data.days_in_month || 30, data.today_day || null, data.no_plan_days || []);
        _renderAttendanceSessionHistory(data.session_history);
        if (prevBtn) prevBtn.disabled = false;
        _updateAttendanceNav(data.is_current_month);
      })
      .catch(() => {
        showToast('Could not reach the server. Please try again.', 'error');
        if (prevBtn) prevBtn.disabled = false;
        if (nextBtn) nextBtn.disabled = nextWasDisabled;
      })
      .finally(() => { attendanceBusy = false; });
  }

  function _hydrateProgressBars() {
    document.querySelectorAll('.progress-fill[data-width]').forEach(el => {
      const width = Number(el.dataset.width);
      if (!Number.isNaN(width)) {
        el.style.width = `${width}%`;
      }
    });
  }

  /** Restrict the "when do you want to start?" picker to today or later,
   *  and default it to today so most members can just leave it as-is. */
  function _initStartDateField() {
    const input = document.getElementById('member-renew-start');
    if (!input) return;
    const todayStr = new Date().toISOString().slice(0, 10);
    input.min   = todayStr;
    input.value = todayStr;
  }

  function tab(tabName, navEl) {
    if (LOCKED_TABS.includes(tabName) && !_planActive()) {
      showToast('Activate your membership first to unlock this.', 'error');
      Navigation.activateTab('member', 'membership', document.getElementById('nav-member-membership'));
      return;
    }
    Navigation.activateTab('member', tabName, navEl);
  }

  /** Show/hide the school ID upload field based on the student Yes/No dropdown */
  function toggleStudentIdField(select) {
    const group = document.getElementById('member-student-id-group');
    if (group) {
      const isStudent = select?.value === 'yes';
      group.style.display = isStudent ? 'block' : 'none';
      if (!isStudent) {
        const input   = document.getElementById('member-student-id');
        const preview = document.getElementById('member-student-id-preview');
        if (input) input.value = '';
        if (preview) { preview.style.display = 'none'; preview.removeAttribute('src'); }
      }
    }
    _updatePlanPriceDisplays();
  }

  // ── Student discount pricing ──
  // This promo table is a fixed rate list (not part of the admin/staff
  // content editor) and mirrors the same STUDENT_PLAN_PRICES table the
  // server uses to compute the actual charge — so what's shown here always
  // matches what gets billed. Regular prices come from PLAN_DATA (admin/
  // staff editable). Daily has no listed student rate on purpose — it just
  // falls back to its normal price everywhere below.
  const STUDENT_PRICES = { weekly: 400, monthly: 800, yearly: 6000 };

  function _formatPeso(n) { return '₱' + Number(n).toLocaleString('en-US'); }

  function _isStudentSelected() {
    return document.getElementById('member-renew-student')?.value === 'yes';
  }

  /** Plain "₱N" text for a plan key, honoring student status. */
  function _planPriceText(key, isStudent) {
    const regular = PLAN_DATA[key] ? PLAN_DATA[key].price : 0;
    const price = (isStudent && STUDENT_PRICES[key] !== undefined) ? STUDENT_PRICES[key] : regular;
    return _formatPeso(price);
  }

  /** Refresh the price shown on each plan card — struck-through original
   *  plus the discounted rate when the student toggle is set to Yes. */
  function _updatePlanPriceDisplays() {
    const isStudent = _isStudentSelected();
    Object.keys(PLAN_DATA).forEach(key => {
      const el = document.querySelector(`#member-membership .plan-card[onclick*="'${key}'"] .plan-price`);
      if (!el) return;
      const regular = PLAN_DATA[key].price;
      const discounted = isStudent && STUDENT_PRICES[key] !== undefined;
      el.innerHTML = discounted
        ? `<span style="text-decoration:line-through;opacity:.55;font-size:0.65em;margin-right:4px;">${_formatPeso(regular)}</span>${_formatPeso(STUDENT_PRICES[key])}`
        : _formatPeso(regular);
    });
  }


  /** Preview uploaded school ID image */
  function previewStudentId(input) {
    const file    = input.files && input.files[0];
    const preview = document.getElementById('member-student-id-preview');
    if (!preview) return;
    if (!file) { preview.style.display = 'none'; preview.removeAttribute('src'); return; }
    preview.src           = URL.createObjectURL(file);
    preview.style.display = 'block';
  }

  /** Show/hide the coach selection dropdown based on the coach Yes/No dropdown */
  function toggleCoachField(select) {
    const group = document.getElementById('member-coach-name-group');
    if (!group) return;
    const wantsCoach = select?.value === 'yes';
    group.style.display = wantsCoach ? 'block' : 'none';
    if (!wantsCoach) {
      const coachSelect = document.getElementById('member-renew-coach-name');
      if (coachSelect) coachSelect.value = '';
    }
  }

  /** Show/hide the GCash reference + proof-of-payment fields based on the
   *  payment method dropdown (Payment tab). */
  function togglePaymentProofField(select) {
    const group = document.getElementById('payment-gcash-fields');
    if (!group) return;
    const isGcash = select?.value === 'gcash';
    group.style.display = isGcash ? 'block' : 'none';
    if (!isGcash) {
      const refEl     = document.getElementById('payment-gcash-reference');
      const proofEl   = document.getElementById('payment-gcash-proof');
      const previewEl = document.getElementById('payment-gcash-proof-preview');
      if (refEl) refEl.value = '';
      if (proofEl) proofEl.value = '';
      if (previewEl) { previewEl.style.display = 'none'; previewEl.removeAttribute('src'); }
    }
  }

  /** Preview the uploaded GCash proof-of-payment screenshot (Payment tab) */
  function previewGcashProof(input) {
    const file    = input.files && input.files[0];
    const preview = document.getElementById('payment-gcash-proof-preview');
    if (!preview) return;
    if (!file) { preview.style.display = 'none'; preview.removeAttribute('src'); return; }
    preview.src           = URL.createObjectURL(file);
    preview.style.display = 'block';
  }

  let _pendingPaymentSubmission = null;

  /** Validate the chosen payment method (Cash/GCash), then ask for
   *  confirmation before actually sending it — submitting fires off a real
   *  payment request that admin will act on, so we don't want an accidental
   *  click to submit it right away. */
  function submitPaymentMethod() {
    const methodEl       = document.getElementById('payment-method-select');
    const paymentMethod  = methodEl?.value || 'cash';
    const isGcash        = paymentMethod === 'gcash';
    const gcashRefEl     = document.getElementById('payment-gcash-reference');
    const gcashRef       = (gcashRefEl?.value || '').trim();
    const gcashProofEl   = document.getElementById('payment-gcash-proof');
    const gcashProofFile = gcashProofEl && gcashProofEl.files && gcashProofEl.files[0];

    if (isGcash && !gcashRef) {
      showToast('Please enter your GCash reference number', 'error');
      return;
    }
    if (isGcash && !gcashProofFile) {
      showToast('Please attach a screenshot of your GCash proof of payment', 'error');
      return;
    }

    _pendingPaymentSubmission = { paymentMethod, isGcash, gcashRef, gcashProofFile };
    openModal('confirm-payment-modal');
  }

  /** "Yes" button inside the payment confirmation modal — actually submits. */
  function confirmSubmitPayment() {
    closeModal('confirm-payment-modal');
    if (!_pendingPaymentSubmission) return;
    _doSubmitPaymentMethod(_pendingPaymentSubmission);
    _pendingPaymentSubmission = null;
  }

  /** "No" button inside the payment confirmation modal — discards it and
   *  returns to the Payment tab, no changes made. */
  function cancelSubmitPayment() {
    closeModal('confirm-payment-modal');
    _pendingPaymentSubmission = null;
  }

  /** Actually send the chosen payment method to the backend. */
  function _doSubmitPaymentMethod(p) {
    const { paymentMethod, isGcash, gcashRef, gcashProofFile } = p;

    const formData = new FormData();
    formData.append('payment_method', paymentMethod);
    if (isGcash) {
      formData.append('gcash_reference', gcashRef);
      if (gcashProofFile) formData.append('gcash_proof', gcashProofFile);
    }

    const btn = document.querySelector('#payment-submit-panel .btn-red');
    const originalLabel = btn ? btn.textContent : 'SUBMIT PAYMENT';
    if (btn) { btn.disabled = true; btn.textContent = 'SUBMITTING...'; }

    fetch('/member/submit-payment-method', { method: 'POST', body: formData })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
        if (!ok || !data.success) {
          showToast(data.error || 'Could not submit payment.', 'error');
          return;
        }
        const msgEl = document.getElementById('payment-submit-success-message');
        if (msgEl) {
          msgEl.textContent = isGcash
            ? "You have successfully submitted your payment. Please wait for admin's approval."
            : 'Please go to staff for your membership payment.';
        }
        openModal('payment-submit-success-modal');
      })
      .catch(() => {
        if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
        showToast('Could not reach the server. Please try again.', 'error');
      });
  }

  /** "OK" button on the payment success modal — reload so the dashboard
   *  reflects the newly-submitted payment status. */
  function closePaymentSubmitSuccessModal() {
    closeModal('payment-submit-success-modal');
    window.location.reload();
  }

  let _pendingSubmission = null;

  /** Validate the plan request, then ask for confirmation before actually
   *  sending it — submitting activates a real payment request that staff
   *  will act on, so we don't want an accidental click to fire it off. */
  function submitRenewalPayment() {
    const startEl        = document.getElementById('member-renew-start');
    const startDate      = startEl?.value || '';
    const studentEl      = document.getElementById('member-renew-student');
    const isStudent      = studentEl?.value === 'yes';
    const studentIdEl    = document.getElementById('member-student-id');
    const studentIdFile  = studentIdEl && studentIdEl.files && studentIdEl.files[0];
    const coachToggleEl  = document.getElementById('member-renew-coach-toggle');
    const wantsCoach     = coachToggleEl?.value === 'yes';
    const coachNameEl    = document.getElementById('member-renew-coach-name');
    const coachName      = coachNameEl?.value || '';

    if (!selectedPlan) { showToast('Please select a plan first', 'error'); return; }
    if (!startDate) { showToast('Please choose a start date', 'error'); return; }
    const todayStr = new Date().toISOString().slice(0, 10);
    if (startDate < todayStr) { showToast('Start date cannot be in the past', 'error'); return; }
    if (isStudent && !studentIdFile) {
      showToast('Please upload a photo of your school ID', 'error');
      return;
    }
    if (wantsCoach && !coachName) {
      showToast('Please choose a coach', 'error');
      return;
    }

    _pendingSubmission = {
      startEl, startDate, todayStr, studentEl, isStudent, studentIdEl, studentIdFile,
      coachToggleEl, wantsCoach, coachNameEl, coachName,
    };
    _openConfirmPlanModal();
  }

  /** Add one calendar month, landing on the same day-of-month when possible
   *  and clamping to the last valid day when the target month is shorter
   *  (e.g. Jan 31 -> Feb 28/29, not Mar 3). Mirrors the backend's logic so
   *  the preview here always matches what actually gets scheduled. */
  function _addCalendarMonth(d) {
    const day = d.getDate();
    const result = new Date(d.getFullYear(), d.getMonth() + 1, 1);
    const lastDayOfTargetMonth = new Date(result.getFullYear(), result.getMonth() + 1, 0).getDate();
    result.setDate(Math.min(day, lastDayOfTargetMonth));
    return result;
  }

  /** Compute a plan's end date from its start date, given the plan key.
   *  "Monthly" is always a real calendar month (mirrors the backend's
   *  _plan_expiry special-case); every other plan uses its duration_days
   *  from PLAN_DATA (admin/staff editable), falling back to 30. */
  function _planEndDate(planKey, start) {
    if (planKey === 'monthly') return _addCalendarMonth(start);
    const durationDays = (PLAN_DATA[planKey] && PLAN_DATA[planKey].duration_days) || 30;
    const end = new Date(start);
    end.setDate(end.getDate() + durationDays);
    return end;
  }

  /** Fill in and open the "are you sure?" modal for the pending request. */
  function _openConfirmPlanModal() {
    const p = _pendingSubmission;
    if (!p) return;

    const info = PLAN_DATA[selectedPlan];
    const nameEl  = document.getElementById('confirm-plan-name');
    const priceEl = document.getElementById('confirm-plan-price');
    const dateEl  = document.getElementById('confirm-plan-date');
    const endEl   = document.getElementById('confirm-plan-end-date');

    if (nameEl)  nameEl.textContent  = info ? info.name.toUpperCase() : selectedPlan.toUpperCase();
    if (priceEl) {
      priceEl.textContent = _planPriceText(selectedPlan, p.isStudent);
      priceEl.textContent += p.isStudent && STUDENT_PRICES[selectedPlan] !== undefined ? ' (student rate)' : '';
    }

    const start = new Date(p.startDate + 'T00:00:00');
    if (dateEl) {
      dateEl.textContent = start.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
    }
    if (endEl) {
      const end = _planEndDate(selectedPlan, start);
      endEl.textContent = end.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
    }

    openModal('confirm-plan-modal');
  }

  /** "Yes, request this plan" button inside the confirmation modal. */
  function confirmPlanRequest() {
    closeModal('confirm-plan-modal');
    if (!_pendingSubmission) return;
    _doSubmitRenewalPayment(_pendingSubmission);
    _pendingSubmission = null;
  }

  /** "Cancel" button inside the confirmation modal — just discards it. */
  function cancelPlanRequest() {
    closeModal('confirm-plan-modal');
    _pendingSubmission = null;
  }

  /** Actually send the plan request to the backend. No payment details are
   *  collected here — staff/admin confirm payment separately before
   *  approving. */
  function _doSubmitRenewalPayment(p) {
    const {
      startEl, startDate, todayStr, studentEl, isStudent, studentIdEl, studentIdFile,
      coachToggleEl, wantsCoach, coachNameEl, coachName,
    } = p;

    const formData = new FormData();
    formData.append('plan',        selectedPlan);
    formData.append('start_date',  startDate);
    formData.append('is_student',  isStudent ? '1' : '0');
    if (isStudent && studentIdFile) formData.append('student_id', studentIdFile);
    formData.append('wants_coach', wantsCoach ? '1' : '0');
    if (wantsCoach) formData.append('coach_name', coachName);

    const btn = document.querySelector('#member-membership .btn-red');
    const originalLabel = btn ? btn.textContent : 'REQUEST THIS PLAN';
    if (btn) { btn.disabled = true; btn.textContent = 'SUBMITTING...'; }

    fetch('/member/submit-payment', { method: 'POST', body: formData })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
        if (!ok || !data.success) {
          showToast(data.error || 'Could not submit request.', 'error');
          return;
        }

        if (studentEl) studentEl.value = 'no';
        if (startEl) startEl.value = todayStr;
        if (studentIdEl) studentIdEl.value = '';
        const studentGroup = document.getElementById('member-student-id-group');
        if (studentGroup) studentGroup.style.display = 'none';
        const studentPreview = document.getElementById('member-student-id-preview');
        if (studentPreview) { studentPreview.style.display = 'none'; studentPreview.removeAttribute('src'); }

        if (coachToggleEl) coachToggleEl.value = 'no';
        if (coachNameEl) coachNameEl.value = '';
        const coachGroup = document.getElementById('member-coach-name-group');
        if (coachGroup) coachGroup.style.display = 'none';

        _showPlanSuccessModal(data.message || 'Plan requested! Please wait for staff approval before proceeding to payment.');
      })
      .catch(() => {
        if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
        showToast('Could not reach the server. Please try again.', 'error');
      });
  }

  /** Show the "request successful" popup; reloads the page once it's dismissed. */
  function _showPlanSuccessModal(message) {
    const msgEl = document.getElementById('plan-success-message');
    if (msgEl) msgEl.textContent = message;
    openModal('plan-success-modal');
  }

  /** "OK" button (or ✕) on the success popup — closes it and refreshes the dashboard. */
  function closePlanSuccessModal() {
    closeModal('plan-success-modal');
    window.location.reload();
  }

  /* ── Withdraw a submitted plan request (Pending / Processing only) ── */
  let _pendingWithdrawId = null;

  /** Button on the "awaiting approval" banner — asks for confirmation
   *  before actually withdrawing the request. */
  function withdrawPlanRequest(paymentId) {
    _pendingWithdrawId = paymentId;
    openModal('withdraw-request-modal');
  }

  /** "No, keep it" — just discards, no changes made. */
  function cancelWithdrawRequest() {
    closeModal('withdraw-request-modal');
    _pendingWithdrawId = null;
  }

  /** "Yes, cancel it" — actually withdraws the request from the server. */
  function confirmWithdrawRequest() {
    closeModal('withdraw-request-modal');
    if (!_pendingWithdrawId) return;
    _pendingWithdrawId = null;

    const btn = document.querySelector('.withdraw-request-btn');
    const originalLabel = btn ? btn.textContent : 'CANCEL REQUEST';
    if (btn) { btn.disabled = true; btn.textContent = 'CANCELLING...'; }

    fetch('/member/cancel-plan-request', { method: 'POST' })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.success) {
          if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
          showToast(data.error || 'Could not cancel request.', 'error');
          return;
        }
        showToast(data.message || 'Plan request cancelled.', 'success');
        setTimeout(() => window.location.reload(), 700);
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
    const label = PLAN_DATA[plan] ? PLAN_DATA[plan].name : (plan.charAt(0).toUpperCase() + plan.slice(1));
    showToast('Plan selected: ' + label, 'success');
    openPlanModal(plan);
  }

  // ── Membership plans: "what's included" modal ──
  // Sourced from PLAN_DATA (built from #member-plans-data), which mirrors
  // whatever admin/staff have set for each plan's description and
  // inclusions under Settings → Manage Content.
  let planModalKey = null;

  function openPlanModal(key) {
    const info = PLAN_DATA[key];
    if (!info) return;
    planModalKey = key;

    const title    = document.getElementById('plan-modal-title');
    const price    = document.getElementById('plan-modal-price');
    const subtitle = document.getElementById('plan-modal-subtitle');
    const list     = document.getElementById('plan-modal-list');

    if (title)    title.textContent = info.name.toUpperCase();
    if (price)    price.textContent = _planPriceText(key, _isStudentSelected());
    if (subtitle) subtitle.textContent = info.description || '';
    if (list)     list.innerHTML = (info.inclusions || []).map(i => `<li>${_escapeHtml(i)}</li>`).join('');

    openModal('plan-modal');
  }

  /** "SELECT THIS PLAN" button inside the inclusions modal */
  function selectPlanFromModal() {
    if (!planModalKey) return;
    const card = document.querySelector(`#member-membership .plan-card[onclick*="'${planModalKey}'"]`);
    selectedPlan = planModalKey;
    document.querySelectorAll('#member-membership .plan-card').forEach(c => c.classList.remove('selected'));
    if (card) card.classList.add('selected');
    const label = PLAN_DATA[planModalKey] ? PLAN_DATA[planModalKey].name : (planModalKey.charAt(0).toUpperCase() + planModalKey.slice(1));
    showToast('Plan selected: ' + label, 'success');
    closeModal('plan-modal');
  }

  // ── Services tab: "what's included" modal ──
  // Sourced from SERVICE_DATA (built from #member-services-data), which
  // mirrors whatever admin/staff have set under Settings → Manage Content.
  // Services only carry a name + description (no per-line inclusions list
  // in the data model), so the "What's included" bullet list is hidden
  // when there's nothing to show.
  function openServiceModal(id) {
    const info = SERVICE_DATA[id];
    if (!info) return;

    const icon      = document.getElementById('service-modal-icon');
    const title     = document.getElementById('service-modal-title');
    const subtitle  = document.getElementById('service-modal-subtitle');
    const listTitle = document.getElementById('service-modal-list-title');
    const list      = document.getElementById('service-modal-list');

    if (icon)     icon.textContent = info.icon || '🛎️';
    if (title)    title.textContent = info.name.toUpperCase();
    if (subtitle) subtitle.textContent = info.description || '';
    if (list)     list.innerHTML = '';
    if (listTitle) listTitle.style.display = 'none';

    openModal('service-modal');
  }

  return {
    init, tab, submitRenewalPayment, confirmPlanRequest, cancelPlanRequest,
    closePlanSuccessModal, closePlanApprovedModal, goToPaymentFromApproval,
    closePaymentApprovedModal, closePlanDeclinedModal,
    selectRenewalPlan, openServiceModal,
    toggleStudentIdField, previewStudentId, toggleCoachField,
    togglePaymentProofField, previewGcashProof, submitPaymentMethod,
    confirmSubmitPayment, cancelSubmitPayment, closePaymentSubmitSuccessModal,
    openPlanModal, selectPlanFromModal, changeAttendanceMonth,
    withdrawPlanRequest, confirmWithdrawRequest, cancelWithdrawRequest
  };
})();


/* ════════════════════════════════════════════════
   INIT — DOMContentLoaded Bootstrap
════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('member-dashboard-root')) return;

  MemberModule.init();

  window.memberTab            = (tab, el) => MemberModule.tab(tab, el);
  window.openServiceModal     = MemberModule.openServiceModal;
  window.submitRenewalPayment = MemberModule.submitRenewalPayment;
  window.confirmPlanRequest   = MemberModule.confirmPlanRequest;
  window.cancelPlanRequest    = MemberModule.cancelPlanRequest;
  window.closePlanSuccessModal = MemberModule.closePlanSuccessModal;
  window.closePlanApprovedModal = MemberModule.closePlanApprovedModal;
  window.closePaymentApprovedModal = MemberModule.closePaymentApprovedModal;
  window.closePlanDeclinedModal = MemberModule.closePlanDeclinedModal;
  window.goToPaymentFromApproval = MemberModule.goToPaymentFromApproval;
  window.selectPlan           = MemberModule.selectRenewalPlan;
  window.toggleStudentIdField = MemberModule.toggleStudentIdField;
  window.previewStudentId     = MemberModule.previewStudentId;
  window.toggleCoachField     = MemberModule.toggleCoachField;
  window.togglePaymentProofField = MemberModule.togglePaymentProofField;
  window.previewGcashProof       = MemberModule.previewGcashProof;
  window.submitPaymentMethod     = MemberModule.submitPaymentMethod;
  window.confirmSubmitPayment    = MemberModule.confirmSubmitPayment;
  window.cancelSubmitPayment     = MemberModule.cancelSubmitPayment;
  window.closePaymentSubmitSuccessModal = MemberModule.closePaymentSubmitSuccessModal;
  window.withdrawPlanRequest     = MemberModule.withdrawPlanRequest;
  window.confirmWithdrawRequest  = MemberModule.confirmWithdrawRequest;
  window.cancelWithdrawRequest   = MemberModule.cancelWithdrawRequest;
  window.openPlanModal        = MemberModule.openPlanModal;
  window.selectPlanFromModal  = MemberModule.selectPlanFromModal;
  window.changeAttendanceMonth = MemberModule.changeAttendanceMonth;
});