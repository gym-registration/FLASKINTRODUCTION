/* ═══════════════════════════════════════════════════════════════
   POWER GYM — Admin Dashboard
   tr-admin.js  |  Runs on admin-dashboard.html only

   Requires tr-common.js to be loaded first (Session, Navigation,
   showToast, buildAttGrid, closeModal, _injectSidebarUser,
   _bindModalBackdrops, _val).
   ═══════════════════════════════════════════════════════════════ */

'use strict';

/* ════════════════════════════════════════════════
   ADMIN MODULE
   Handles all admin dashboard functionality.
════════════════════════════════════════════════ */
const AdminModule = (() => {

  let memberIdCounter = 1005;
  let currentReportType = null;
  let currentReportPayload = null;

  /** Read the attendance_calendar JSON embedded in admin-dashboard.html */
  function _parseAdminDashboardData() {
    const el = document.getElementById('admin-dashboard-data');
    if (!el) return {};
    try {
      return JSON.parse(el.textContent || el.innerText || '{}');
    } catch (e) {
      return {};
    }
  }

  /** Initialize admin dashboard */
  function init() {
    const session = Session.guardDashboard();
    if (!session) return;

    // Inject user info into sidebar
    _injectSidebarUser(session);

    // Apply role-specific CSS class for sidebar tinting
    document.body.classList.add('role-admin');

    // Build attendance grids from real gym-wide data
    const calendarData = _parseAdminDashboardData();
    buildAttGrid('att-grid-admin', calendarData.present_days || [], calendarData.days_in_month || 30);
    buildAttGrid('att-grid-admin-full', calendarData.present_days || [], calendarData.days_in_month || 30);

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
    const middleInitial = _val('add-member-mi');
    const lastName  = _val('add-member-lname');
    const extensionName = _val('add-member-ext');
    const email     = _val('add-member-email');
    const phone     = _val('add-member-phone');
    const planText  = document.getElementById('add-member-plan')?.value || 'Monthly';
    const planName  = planText.split('—')[0].trim();

    if (!firstName || !lastName || !email) {
      showToast('Please fill first name, last name, and email', 'error');
      return;
    }
    if (!/^09\d{9}$/.test(phone)) {
      showToast('Phone number must start with 09 and be exactly 11 digits.', 'error');
      return;
    }

    const addBtn = document.querySelector('#add-member-modal .btn-red');
    if (addBtn) { addBtn.disabled = true; addBtn.textContent = 'ADDING...'; }

    fetch('/admin/add-member', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        first_name:     firstName,
        middle_initial: middleInitial,
        last_name:      lastName,
        extension_name: extensionName,
        email:          email,
        phone:          phone,
        plan:           planName
      })
    })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (addBtn) { addBtn.disabled = false; addBtn.textContent = 'ADD MEMBER'; }
        if (!ok || !data.success) {
          showToast(data.error || 'Failed to add member.', 'error');
          return;
        }

        const m = data.member;
        const tbody = document.querySelector('#members-table tbody');
        if (tbody) {
          const row = document.createElement('tr');
          row.dataset.id            = m.id;
          row.dataset.plan          = m.plan;
          row.dataset.phone         = m.phone || '';
          row.dataset.expiryIso     = m.expiry_iso || '';
          row.dataset.firstName     = m.first_name || '';
          row.dataset.middleInitial = m.middle_initial || '';
          row.dataset.lastName      = m.last_name || '';
          row.dataset.extensionName = m.extension_name || '';
          row.innerHTML = `
            <td>#${m.id}</td>
            <td>${m.name}</td>
            <td>${m.email}</td>
            <td>${m.plan}</td>
            <td>${m.expiry}</td>
            <td><span class="badge badge-green">Active</span></td>
            <td>
              <button class="btn btn-sm btn-outline" onclick="openEditMemberModal(this)">Edit</button>
              <button class="btn btn-sm" style="background:rgba(230,30,37,0.1);color:var(--red);border:1px solid rgba(230,30,37,0.2);" onclick="deleteMemberRow(this)">Del</button>
            </td>`;
          tbody.prepend(row);
        }

        closeModal('add-member-modal');
        ['add-member-fname', 'add-member-mi', 'add-member-lname', 'add-member-ext', 'add-member-email', 'add-member-phone'].forEach(id => {
          const el = document.getElementById(id);
          if (el) el.value = '';
        });
        const planEl = document.getElementById('add-member-plan');
        if (planEl) planEl.selectedIndex = 0;

        showToast(`Member added! Temporary password: ${m.temp_password}`, 'success');
      })
      .catch(() => {
        if (addBtn) { addBtn.disabled = false; addBtn.textContent = 'ADD MEMBER'; }
        showToast('Could not reach the server. Please try again.', 'error');
      });
  }

  let editingMemberId = null;

  /** Open the Edit Member modal, pre-filled from the row's data */
  function openEditMemberModal(btn) {
    const row = btn.closest('tr');
    if (!row) return;
    const cells = row.querySelectorAll('td');
    if (cells.length < 7) return;

    editingMemberId = row.dataset.id;
    document.getElementById('edit-member-fname').value  = row.dataset.firstName     || '';
    document.getElementById('edit-member-mi').value     = row.dataset.middleInitial || '';
    document.getElementById('edit-member-lname').value  = row.dataset.lastName      || '';
    document.getElementById('edit-member-ext').value    = row.dataset.extensionName || '';
    document.getElementById('edit-member-email').value  = cells[2].textContent.trim();
    document.getElementById('edit-member-phone').value  = row.dataset.phone || '';
    document.getElementById('edit-member-expiry').value = row.dataset.expiryIso || '';

    const planSelect = document.getElementById('edit-member-plan');
    if (planSelect) {
      [...planSelect.options].forEach(opt => {
        opt.selected = opt.value.split('—')[0].trim() === row.dataset.plan;
      });
    }

    openModal('edit-member-modal');
  }

  /** Save the Edit Member modal's fields to the server */
  function saveEditMember() {
    const firstName = _val('edit-member-fname');
    const middleInitial = _val('edit-member-mi');
    const lastName  = _val('edit-member-lname');
    const extensionName = _val('edit-member-ext');
    const email     = _val('edit-member-email');
    const phone     = _val('edit-member-phone');
    const planText  = document.getElementById('edit-member-plan')?.value || '';
    const planName  = planText.split('—')[0].trim();
    const expiry    = document.getElementById('edit-member-expiry')?.value || '';

    if (!firstName || !lastName || !email) {
      showToast('First name, last name, and email are required', 'error');
      return;
    }
    if (!/^09\d{9}$/.test(phone)) {
      showToast('Phone number must start with 09 and be exactly 11 digits.', 'error');
      return;
    }

    const saveBtn = document.querySelector('#edit-member-modal .btn-red');
    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'SAVING...'; }

    fetch(`/admin/edit-member/${editingMemberId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        first_name:     firstName,
        middle_initial: middleInitial,
        last_name:      lastName,
        extension_name: extensionName,
        email:          email,
        phone:          phone,
        plan:           planName,
        expiry:         expiry
      })
    })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'SAVE CHANGES'; }
        if (!ok || !data.success) {
          showToast(data.error || 'Failed to update member.', 'error');
          return;
        }

        const row = document.querySelector(`#members-table tr[data-id="${editingMemberId}"]`);
        if (row) {
          const m = data.member;
          const cells = row.querySelectorAll('td');
          cells[1].textContent = m.name;
          cells[2].textContent = m.email;
          cells[3].textContent = m.plan;
          cells[4].textContent = m.expiry;
          row.dataset.plan          = m.plan;
          row.dataset.expiryIso     = m.expiry_iso;
          row.dataset.phone         = m.phone || '';
          row.dataset.firstName     = m.first_name || '';
          row.dataset.middleInitial = m.middle_initial || '';
          row.dataset.lastName      = m.last_name || '';
          row.dataset.extensionName = m.extension_name || '';
        }

        closeModal('edit-member-modal');
        showToast('Member updated successfully', 'success');
      })
      .catch(() => {
        if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'SAVE CHANGES'; }
        showToast('Could not reach the server. Please try again.', 'error');
      });
  }

  /** Delete a member row from the server, then remove it from the table */
  function deleteMemberRow(btn) {
    const row  = btn.closest('tr');
    if (!row) return;
    const id   = row.dataset.id;
    const name = row.querySelectorAll('td')[1]?.textContent.trim() || 'this member';
    if (!confirm(`Delete ${name}? This cannot be undone.`)) return;

    fetch(`/admin/delete-member/${id}`, { method: 'POST' })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.success) {
          showToast(data.error || 'Failed to delete member.', 'error');
          return;
        }
        row.remove();
        showToast('Member deleted', 'success');
      })
      .catch(() => showToast('Could not reach the server. Please try again.', 'error'));
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
        rows: [['Maria Santos', 'Monthly', 'Active', 'May 10, 2026'], ['Jose Reyes', 'Yearly', 'Active', 'Apr 10, 2027'], ['Ana Cruz', 'Daily', 'Pending', 'Apr 10, 2026'], ['Carlo Dela Rosa', 'Weekly', 'Expired', 'Jan 5, 2026']],
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
    if (plan === 'yearly')       expiry.setDate(expiry.getDate() + 365);
    else if (plan === 'weekly')  expiry.setDate(expiry.getDate() + 7);
    else if (plan === 'daily')   expiry.setDate(expiry.getDate() + 1);
    else                          expiry.setDate(expiry.getDate() + 30);
    return expiry;
  }

  /** Show the uploaded payment proof (image or PDF) in a modal before approving/rejecting */
  function viewPaymentProof(url) {
    const img     = document.getElementById('proof-modal-img');
    const pdfNote = document.getElementById('proof-modal-pdf-note');
    const pdfLink = document.getElementById('proof-modal-pdf-link');
    if (!img || !pdfNote || !pdfLink) return;

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

  return {
    init, tab, addMember, openEditMemberModal, saveEditMember, deleteMemberRow,
    generateAnalyticsReport, refreshCurrentReport, exportReportCSV, exportReportPDF,
    viewPaymentProof
  };
})();


/* ════════════════════════════════════════════════
   INIT — DOMContentLoaded Bootstrap
════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('admin-dashboard-root')) return;

  AdminModule.init();

  window.adminTab                = (tab, el) => AdminModule.tab(tab, el);
  window.addMember               = AdminModule.addMember;
  window.openEditMemberModal     = AdminModule.openEditMemberModal;
  window.saveEditMember          = AdminModule.saveEditMember;
  window.deleteMemberRow         = AdminModule.deleteMemberRow;
  window.generateAnalyticsReport = AdminModule.generateAnalyticsReport;
  window.refreshCurrentReport    = AdminModule.refreshCurrentReport;
  window.exportCurrentReportCSV  = AdminModule.exportReportCSV;
  window.exportCurrentReportPDF  = AdminModule.exportReportPDF;
  window.viewPaymentProof        = AdminModule.viewPaymentProof;
});