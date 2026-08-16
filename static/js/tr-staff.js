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

    showNewAnnouncementNotices(_parseStaffDashboardData().new_announcements);

    _tickLiveDurations();
    setInterval(_tickLiveDurations, 1000);
  }

  /** Parse the staff-dashboard-data JSON <script> tag embedded by the server. */
  function _parseStaffDashboardData() {
    const el = document.getElementById('staff-dashboard-data');
    if (!el) return {};
    try {
      return JSON.parse(el.textContent || el.innerText || '{}');
    } catch (e) {
      return {};
    }
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
    if (tabName === 'settings') ContentManager.ensureLoaded();
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

  let _pendingRecordPayment = null;

  /** "RECORD PAYMENT" button — validate the form, then ask for confirmation
   *  before actually sending it (an accidental click shouldn't record a
   *  real payment and extend someone's membership). */
  function promptRecordPayment() {
    const memberIdentifier = _val('pay-member');
    const planText          = document.getElementById('pay-plan')?.value   || '';
    const method            = document.getElementById('pay-method')?.value || '';
    const planName          = planText.split('—')[0].trim();

    if (!memberIdentifier) {
      showToast('Please enter the member name, ID, or email', 'error');
      return;
    }

    _pendingRecordPayment = { memberIdentifier, planName, method };
    openModal('confirm-record-payment-modal');
  }

  /** "YES" inside the confirmation modal — actually records the payment. */
  function confirmRecordPayment() {
    closeModal('confirm-record-payment-modal');
    if (!_pendingRecordPayment) return;
    _doRecordPayment(_pendingRecordPayment);
    _pendingRecordPayment = null;
  }

  /** "NO" (or the ✕) inside the confirmation modal — discards it and
   *  returns to the Payment Record form, no changes made. */
  function cancelRecordPayment() {
    closeModal('confirm-record-payment-modal');
    _pendingRecordPayment = null;
  }

  /** Record a front-desk payment and persist it to the database */
  function _doRecordPayment({ memberIdentifier, planName, method }) {
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

        const msgEl = document.getElementById('payment-recorded-message');
        if (msgEl) {
          msgEl.textContent = `Payment successfully recorded for ${data.payment.member_name} — membership active until ${data.payment.expiry}.`;
        }
        openModal('payment-recorded-modal');
      })
      .catch(() => {
        if (btn) { btn.disabled = false; btn.textContent = 'RECORD PAYMENT'; }
        showToast('Could not reach the server. Please try again.', 'error');
      });
  }

  /** "OK" on the payment-recorded modal — reload so the Pending Requests
   *  list (shown here and on the Request tab) reflects the resolved request. */
  function closePaymentRecordedModal() {
    closeModal('payment-recorded-modal');
    window.location.reload();
  }

  /** Resolve an argument that may be an <input>/<select> element id, or a raw
   *  identifier (e.g. an email passed straight from a table row button). */
  function _resolveIdentifier(idOrValue) {
    const el = document.getElementById(idOrValue);
    return el ? (el.value || '').trim() : (idOrValue || '').trim();
  }

  /** Check a member in via the given input field's id, or a raw identifier */
  /** Save a coach's available days + max member capacity from the Coach tab.
   *  Called as the onsubmit handler of each coach card's form. */
  function submitCoachUpdate(event) {
    event.preventDefault();
    const form = event.target;
    const btn  = form.querySelector('button[type="submit"]');
    const originalLabel = btn ? btn.textContent : null;
    if (btn) { btn.disabled = true; btn.textContent = 'SAVING...'; }

    fetch('/staff/coach/update', { method: 'POST', body: new FormData(form) })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
        if (!ok || !data.success) {
          showToast(data.error || 'Could not update coach.', 'error');
          return;
        }
        showToast(data.message || 'Coach updated.', 'success');
        setTimeout(() => window.location.reload(), 700);
      })
      .catch(() => {
        if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
        showToast('Could not reach the server. Please try again.', 'error');
      });
    return false;
  }

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

  /** "GENERATE REPORT" buttons on the Analytics tab — download a CSV for the
   *  selected report type and date range (Revenue/Attendance offer a range
   *  picker; Membership is always a full current snapshot). */
  function generateReport(reportType) {
    const rangeEl = document.getElementById(`${reportType}-report-range`);
    const range   = rangeEl ? rangeEl.value : 'this_month';
    const query   = reportType === 'membership' ? '' : `?range=${encodeURIComponent(range)}`;
    window.location.href = `/staff/reports/${reportType}.csv${query}`;
  }

  // ── Live Analytics report generator (mirrors the admin dashboard's Report
  //    Generator panel) — staff can pull Membership, Revenue, and Attendance
  //    reports. Membership and Attendance are full snapshots just like Admin
  //    sees; Revenue is the one exception and the server restricts it to
  //    Cash payments only (GCash is Admin's to see). ──
  let staffReportChartInstance = null;
  let staffCurrentReportPayload = null;
  let staffCurrentReportType = null;

  function generateStaffAnalyticsReport(type) {
    const panel = document.getElementById('staff-report-output-panel');
    const title = document.getElementById('staff-report-output-title');
    const body  = document.getElementById('staff-report-output-body');
    if (!panel || !title || !body) return;

    staffCurrentReportType = type;

    const fromDate = document.getElementById('staff-report-from')?.value || '';
    const toDate   = document.getElementById('staff-report-to')?.value   || '';
    if ((fromDate && !toDate) || (!fromDate && toDate)) {
      showToast('Please set both From and To dates, or clear them to use this month.', 'error');
      return;
    }

    panel.style.display = 'block';
    title.textContent = 'Loading…';
    body.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:14px 0;">Generating report…</div>';

    const params = new URLSearchParams();
    if (fromDate && toDate) { params.set('from', fromDate); params.set('to', toDate); }

    fetch(`/api/staff/reports/${type}?${params.toString()}`)
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.success) {
          showToast((data && data.error) || 'Could not generate report.', 'error');
          title.textContent = 'Report';
          body.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:14px 0;">Could not load this report. Try Refresh.</div>';
          return;
        }

        const report = data.report;
        staffCurrentReportPayload = report;

        title.textContent = `${report.title} — ${report.range_label} — Generated ${new Date().toLocaleString()}`;
        body.innerHTML = `
          <div class="stats-grid" style="grid-template-columns:repeat(${report.stats.length},1fr);margin-bottom:14px;">
            ${report.stats.map(s => `<div class="stat-card"><div class="stat-value" style="font-size:24px;">${s.value}</div><div class="stat-label">${s.label}</div></div>`).join('')}
          </div>
          ${report.chart_series && report.chart_series.length ? `
          <div style="margin-bottom:16px;">
            <div style="font-size:12px;color:var(--muted);margin-bottom:8px;">${report.chart_label}</div>
            ${_renderStaffReportChartCanvas(type, report.chart_series)}
          </div>` : ''}
          ${_renderStaffRevenueBreakdowns(type, report)}
          ${report.rows.length ? `
          <table class="data-table">
            <thead><tr>${report.headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
            <tbody>${report.rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>
          </table>` : '<div style="color:var(--muted);font-size:13px;padding:14px 0;">No records found for this range.</div>'}`;

        if (report.chart_series && report.chart_series.length) _mountStaffReportChart(type, report.chart_series);

        showToast(report.title + ' generated successfully', 'success');
      })
      .catch(() => {
        showToast('Could not reach the server. Please try again.', 'error');
        title.textContent = 'Report';
        body.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:14px 0;">Could not load this report. Try Refresh.</div>';
      });
  }

  /** Cash Revenue Report: the server also returns a by-plan breakdown and a
   *  per-staff Cash breakdown (useful for front-desk oversight — who
   *  collected how much) that weren't being displayed anywhere. Surface
   *  them as two mini tables above the transaction list. */
  function _renderStaffRevenueBreakdowns(type, report) {
    if (type !== 'revenue') return '';
    const hasByPlan = report.by_plan && report.by_plan.length;
    const hasByStaff = report.cash_by_staff && report.cash_by_staff.length;
    if (!hasByPlan && !hasByStaff) return '';

    const byPlanTable = hasByPlan ? `
      <div style="flex:1;min-width:220px;">
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px;">Cash Revenue by Plan</div>
        <table class="data-table">
          <thead><tr><th>Plan</th><th>Total</th></tr></thead>
          <tbody>${report.by_plan.map(r => `<tr><td>${r.plan}</td><td>\u20b1${r.total}</td></tr>`).join('')}</tbody>
        </table>
      </div>` : '';

    const byStaffTable = hasByStaff ? `
      <div style="flex:1;min-width:220px;">
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px;">Cash Collected by Staff</div>
        <table class="data-table">
          <thead><tr><th>Staff</th><th>Total</th><th>Txns</th></tr></thead>
          <tbody>${report.cash_by_staff.map(r => `<tr><td>${r.staff}</td><td>\u20b1${r.total}</td><td>${r.count}</td></tr>`).join('')}</tbody>
        </table>
      </div>` : '';

    return `<div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:20px;">${byPlanTable}${byStaffTable}</div>`;
  }

  function clearStaffReportDateRange() {
    const fromEl = document.getElementById('staff-report-from');
    const toEl   = document.getElementById('staff-report-to');
    if (fromEl) fromEl.value = '';
    if (toEl)   toEl.value   = '';
    if (staffCurrentReportType) generateStaffAnalyticsReport(staffCurrentReportType);
  }

  function refreshStaffReport() {
    if (staffCurrentReportType) generateStaffAnalyticsReport(staffCurrentReportType);
    else showToast('Generate a report first', 'error');
  }

  function exportStaffReportPDF() {
    if (!staffCurrentReportPayload) { showToast('Generate a report first', 'error'); return; }
    window.print();
    showToast('Use Print dialog to save as PDF', 'success');
  }

  /** Meaningful, consistent bar colors per report type/label — mirrors the
   *  palette used on the admin dashboard's Analytics tab. */
  const _STAFF_MEMBERSHIP_BAR_COLORS = { Active: '#1baf7a', Pending: '#eda100', Expired: '#e34948', Declined: '#898781', 'No Plan': '#898781' };
  const _STAFF_ATTENDANCE_PALETTE    = ['#3d7dd4', '#1baf7a', '#eda100', '#e34948', '#4a3aa7', '#2fb5c9', '#d6689a', '#8c8c1a'];

  function _staffColorForBar(type, label, index) {
    if (type === 'membership') return _STAFF_MEMBERSHIP_BAR_COLORS[label] || '#2a78d6';
    if (type === 'attendance') return _STAFF_ATTENDANCE_PALETTE[index % _STAFF_ATTENDANCE_PALETTE.length];
    return '#2a78d6'; // revenue — always a single Cash bar
  }

  /** Renders the canvas + legend markup for a report's chart. Membership
   *  gets a pie (matches admin); Revenue and Attendance get a bar layout —
   *  Revenue is a single horizontal Cash bar, Attendance is a bar-per-day. */
  function _renderStaffReportChartCanvas(type, series) {
    const label = series.map(s => `${s.label}: ${s.value}`).join(', ');

    if (type === 'membership') {
      const legend = series.map(s => `
        <span style="display:flex;align-items:center;gap:6px;">
          <span style="width:10px;height:10px;border-radius:2px;background:${_staffColorForBar(type, s.label)};"></span>${s.label}
        </span>`).join('');
      return `
        <div style="display:flex;align-items:center;gap:18px;">
          <div style="position:relative;flex:0 0 auto;width:220px;height:220px;">
            <canvas id="staff-report-chart-canvas" role="img" aria-label="Pie chart — ${label}"></canvas>
          </div>
          <div style="display:flex;flex-direction:column;gap:8px;font-size:12px;color:var(--muted);white-space:nowrap;">${legend}</div>
        </div>`;
    }

    if (type === 'revenue') {
      const heightPx = Math.max(120, series.length * 50);
      const legend = series.map(s => `
        <span style="display:flex;align-items:center;gap:6px;">
          <span style="width:10px;height:10px;border-radius:2px;background:${_staffColorForBar(type, s.label)};"></span>${s.label}
        </span>`).join('');
      return `
        <div style="display:flex;align-items:center;gap:18px;">
          <div style="position:relative;flex:1;min-width:0;height:${heightPx}px;">
            <canvas id="staff-report-chart-canvas" role="img" aria-label="Horizontal bar chart — ${label}"></canvas>
          </div>
          <div style="display:flex;flex-direction:column;gap:8px;font-size:12px;color:var(--muted);white-space:nowrap;">${legend}</div>
        </div>`;
    }

    // attendance — vertical bars, one per day in range
    const heightPx = series.length > 10 ? 320 : 260;
    return `
      <div style="position:relative;width:100%;height:${heightPx}px;">
        <canvas id="staff-report-chart-canvas" role="img" aria-label="Bar chart — ${label}"></canvas>
      </div>`;
  }

  function _mountStaffReportChart(type, series) {
    if (staffReportChartInstance) { staffReportChartInstance.destroy(); staffReportChartInstance = null; }
    const canvas = document.getElementById('staff-report-chart-canvas');
    if (!canvas || typeof Chart === 'undefined') return;

    // This dashboard is always dark-themed — match its own palette rather
    // than the OS light/dark preference.
    const muted  = '#8b92a8';
    const grid   = '#2e3545';
    const ink    = '#f5f5f7';
    const rotate = series.length > 8;
    const horizontal = type === 'revenue';

    if (type === 'membership') {
      const total = series.reduce((sum, s) => sum + s.value, 0) || 1;
      staffReportChartInstance = new Chart(canvas, {
        type: 'pie',
        data: {
          labels: series.map(s => s.label),
          datasets: [{
            data: series.map(s => s.value),
            backgroundColor: series.map(s => _staffColorForBar(type, s.label)),
            borderColor: '#141820',
            borderWidth: 2
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            datalabels: typeof ChartDataLabels === 'undefined' ? undefined : {
              color: '#0b0b0b', font: { size: 11, weight: 600 },
              formatter: v => `${Math.round((v / total) * 100)}%`
            }
          }
        },
        plugins: typeof ChartDataLabels === 'undefined' ? [] : [ChartDataLabels]
      });
      return;
    }

    staffReportChartInstance = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: series.map(s => s.label),
        datasets: [{
          data: series.map(s => s.value),
          backgroundColor: series.map((s, i) => _staffColorForBar(type, s.label, i)),
          borderRadius: 3,
          maxBarThickness: 44,
          barPercentage: 0.6,
          categoryPercentage: 0.7
        }]
      },
      options: {
        indexAxis: horizontal ? 'y' : 'x',
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: horizontal ? { right: 20 } : { top: 20 } },
        plugins: {
          legend: { display: false },
          datalabels: typeof ChartDataLabels === 'undefined' ? undefined : {
            anchor: 'end', align: horizontal ? 'end' : 'top', color: ink, font: { size: 11, weight: 500 },
            formatter: v => v.toLocaleString()
          }
        },
        scales: horizontal ? {
          x: { beginAtZero: true, grid: { color: grid }, ticks: { color: muted, font: { size: 12 } } },
          y: { grid: { display: false }, ticks: { display: false } }
        } : {
          x: {
            grid: { display: false },
            ticks: { color: muted, font: { size: 12 }, autoSkip: false, maxRotation: rotate ? 45 : 0 }
          },
          y: { beginAtZero: true, grid: { color: grid }, ticks: { color: muted, font: { size: 11 } } }
        }
      },
      plugins: typeof ChartDataLabels === 'undefined' ? [] : [ChartDataLabels]
    });
  }

  return { init, tab, promptRecordPayment, confirmRecordPayment, cancelRecordPayment, closePaymentRecordedModal, checkInMember, checkOutMember, filterCheckinTable, filterMembersByStatus, filterMembersTable, viewPaymentProof, onPayMemberInput, generateReport, submitCoachUpdate,
           generateStaffAnalyticsReport, clearStaffReportDateRange, refreshStaffReport, exportStaffReportPDF };
})();


/* ════════════════════════════════════════════════
   INIT — DOMContentLoaded Bootstrap
════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('staff-dashboard-root')) return;

  StaffModule.init();

  window.staffTab       = (tab, el) => StaffModule.tab(tab, el);
  window.promptRecordPayment  = () => StaffModule.promptRecordPayment();
  window.confirmRecordPayment = () => StaffModule.confirmRecordPayment();
  window.cancelRecordPayment  = () => StaffModule.cancelRecordPayment();
  window.closePaymentRecordedModal = () => StaffModule.closePaymentRecordedModal();
  window.checkInMember  = (idOrValue) => StaffModule.checkInMember(idOrValue);
  window.checkOutMember = (idOrValue) => StaffModule.checkOutMember(idOrValue);
  window.filterCheckinTable   = (term) => StaffModule.filterCheckinTable(term);
  window.filterMembersByStatus = (status, el) => StaffModule.filterMembersByStatus(status, el);
  window.filterMembersTable    = () => StaffModule.filterMembersTable();
  window.viewPaymentProof      = (url, title) => StaffModule.viewPaymentProof(url, title);
  window.onPayMemberInput      = (value) => StaffModule.onPayMemberInput(value);
  window.generateReport        = (reportType) => StaffModule.generateReport(reportType);
  window.submitCoachUpdate     = (event) => StaffModule.submitCoachUpdate(event);
  window.generateStaffAnalyticsReport = (type) => StaffModule.generateStaffAnalyticsReport(type);
  window.clearStaffReportDateRange    = () => StaffModule.clearStaffReportDateRange();
  window.refreshStaffReport           = () => StaffModule.refreshStaffReport();
  window.exportStaffReportPDF         = () => StaffModule.exportStaffReportPDF();
});