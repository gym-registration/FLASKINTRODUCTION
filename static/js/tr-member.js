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
    buildAttGrid('att-grid-member', memberData.present_days || [], memberData.days_in_month || 30, memberData.today_day || null);
    _updateAttendanceNav(memberData.today_day != null);
    _hydrateProgressBars();
    _bindModalBackdrops();
    _initStartDateField();
    _showApprovalNoticeIfAny(memberData.plan_approved_notice);
    _showPaymentApprovedNoticeIfAny(memberData.payment_verified_notice);
    _showDeclinedNoticeIfAny(memberData.plan_declined_notice);

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
      msgEl.textContent = `You have successfully paid your ${notice.plan_name} membership plan! Your membership starts on ${notice.start_date}.`;
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
        buildAttGrid('att-grid-member', data.present_days || [], data.days_in_month || 30, data.today_day || null);
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
    if (!group) return;
    const isStudent = select?.value === 'yes';
    group.style.display = isStudent ? 'block' : 'none';
    if (!isStudent) {
      const input   = document.getElementById('member-student-id');
      const preview = document.getElementById('member-student-id-preview');
      if (input) input.value = '';
      if (preview) { preview.style.display = 'none'; preview.removeAttribute('src'); }
    }
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

  const PLAN_DURATION_DAYS = { daily: 1, weekly: 14, yearly: 365 };

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

  /** Compute a plan's end date from its start date, given the plan key. */
  function _planEndDate(planKey, start) {
    if (planKey === 'monthly') return _addCalendarMonth(start);
    const durationDays = PLAN_DURATION_DAYS[planKey] || 30;
    const end = new Date(start);
    end.setDate(end.getDate() + durationDays);
    return end;
  }

  /** Fill in and open the "are you sure?" modal for the pending request. */
  function _openConfirmPlanModal() {
    const p = _pendingSubmission;
    if (!p) return;

    const info = PLAN_INFO[selectedPlan];
    const nameEl  = document.getElementById('confirm-plan-name');
    const priceEl = document.getElementById('confirm-plan-price');
    const dateEl  = document.getElementById('confirm-plan-date');
    const endEl   = document.getElementById('confirm-plan-end-date');

    if (nameEl)  nameEl.textContent  = info ? info.title.toUpperCase() : selectedPlan.toUpperCase();
    if (priceEl) priceEl.textContent = info ? info.price : '';

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

  /** Expose plan selection for the renewal grid */
  function selectRenewalPlan(card, plan) {
    selectedPlan = plan;
    document.querySelectorAll('#member-membership .plan-card').forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');
    showToast('Plan selected: ' + plan.charAt(0).toUpperCase() + plan.slice(1), 'success');
    openPlanModal(plan);
  }

  // ── Membership plans: "what's included" modal ──
  const PLAN_INFO = {
    daily: {
      title: 'Daily',
      price: '₱100',
      subtitle: 'Single-day access — up to 2 hours, great for drop-ins',
      items: [
        'Gym floor access for up to 2 hours on your visit day',
        'Use of all cardio and strength equipment',
        'Locker use during your visit',
        'Access to Boxing, Strengthening, Weight Loss & Cardio Zone',
        'No long-term commitment',
      ],
    },
    weekly: {
      title: 'Weekly',
      price: '₱450',
      subtitle: '2 weeks of unlimited access, any time, every day',
      items: [
        'Unlimited-length visits, any time, every day for 14 days',
        'No 2-hour cap — stay as long as you like per visit',
        'Attendance tracking in your dashboard',
        'Access to Boxing, Strengthening, Weight Loss & Cardio Zone',
        'Great for short-term visitors or a trial run',
      ],
    },
    monthly: {
      title: 'Monthly',
      price: '₱900',
      subtitle: '1 month of unlimited access, any time, every day',
      items: [
        'Unlimited-length visits, any time, every day for 30 days',
        'Attendance & Body Goals tracking in your dashboard',
        'Eligible to request a coach (Ronel Samar or Jonathan Natividad)',
        'Student discount available with a valid school ID',
        'Auto-renewal reminders before your plan expires',
      ],
    },
    yearly: {
      title: 'Yearly ⭐',
      price: '₱7,000',
      subtitle: '1 year of unlimited access, any time, every day',
      items: [
        'Unlimited-length visits, any time, every day for 365 days',
        '2 free personal coaching sessions',
        'Members-only yearly gear kit',
        'Price locked for the entire year — no rate increases',
        'Attendance & Body Goals tracking in your dashboard',
      ],
    },
  };

  let planModalKey = null;

  function openPlanModal(key) {
    const info = PLAN_INFO[key];
    if (!info) return;
    planModalKey = key;

    const title    = document.getElementById('plan-modal-title');
    const price    = document.getElementById('plan-modal-price');
    const subtitle = document.getElementById('plan-modal-subtitle');
    const list     = document.getElementById('plan-modal-list');

    if (title)    title.textContent = info.title.toUpperCase();
    if (price)    price.textContent = info.price;
    if (subtitle) subtitle.textContent = info.subtitle;
    if (list)     list.innerHTML = info.items.map(i => `<li>${i}</li>`).join('');

    openModal('plan-modal');
  }

  /** "SELECT THIS PLAN" button inside the inclusions modal */
  function selectPlanFromModal() {
    if (!planModalKey) return;
    const card = document.querySelector(`#member-membership .plan-card[onclick*="'${planModalKey}'"]`);
    selectedPlan = planModalKey;
    document.querySelectorAll('#member-membership .plan-card').forEach(c => c.classList.remove('selected'));
    if (card) card.classList.add('selected');
    showToast('Plan selected: ' + planModalKey.charAt(0).toUpperCase() + planModalKey.slice(1), 'success');
    closeModal('plan-modal');
  }

  // ── Services tab: "what's included" modal ──
  const SERVICE_INFO = {
    boxing: {
      icon: '🥊',
      title: 'Boxing',
      subtitle: 'Gloves, punching bags, sparring',
      items: [
        'Access to boxing area',
        ' Boxing gloves (rental or bring your own)',
        ' Hand wraps (optional purchase)',
        ' Punching bags & speed bags',
        ' Group boxing classes or personal coaching',
        ' Sparring sessions (for advanced members)',
        ' Locker room access',
        ' Showers and changing rooms'
      ],
    },
    strengthening: {
      icon: '💪',
      title: 'Strengthening',
      subtitle: 'Free weights, machines, resistance training',
      items: [
        ' Access to all strength equipment',
        ' Free weights (dumbbells & barbells)',
        ' Resistance machines',
        ' Bench press & squat racks',
        ' Locker room access',
        ' Showers and changing rooms'
      ],
    },
    weightloss: {
      icon: '🔥',
      title: 'Weight Loss',
      subtitle: 'Fat-burning circuits and programs',
      items: [
        ' Cardio equipment access',
        ' Fat-burning workout programs',
        ' Circuit training sessions',
        ' Functional training equipment',
        ' Body composition assessment',
        ' Locker room access',
        ' Showers and changing rooms'
      ],
    },
    cardio: {
      icon: '🚴',
      title: 'Cardio Zone',
      subtitle: 'Bikes, treadmills, rowing',
      items: [
        ' Unlimited use of cardio machines',
        ' Treadmills, bikes, ellipticals, rowers',
        ' Heart rate monitoring (if available)',
        ' Warm-up & cool-down area',
        ' Locker room access',
        ' Showers and changing rooms'
      ],
    },
  };

  function openServiceModal(key) {
    const info = SERVICE_INFO[key];
    if (!info) return;

    const icon     = document.getElementById('service-modal-icon');
    const title    = document.getElementById('service-modal-title');
    const subtitle = document.getElementById('service-modal-subtitle');
    const list     = document.getElementById('service-modal-list');

    if (icon)     icon.textContent = info.icon;
    if (title)    title.textContent = info.title.toUpperCase();
    if (subtitle) subtitle.textContent = info.subtitle;
    if (list)     list.innerHTML = info.items.map(i => `<li>${i}</li>`).join('');

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
    openPlanModal, selectPlanFromModal, changeAttendanceMonth
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
  window.openPlanModal        = MemberModule.openPlanModal;
  window.selectPlanFromModal  = MemberModule.selectPlanFromModal;
  window.changeAttendanceMonth = MemberModule.changeAttendanceMonth;
});