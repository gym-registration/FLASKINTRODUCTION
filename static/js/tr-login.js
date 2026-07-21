/* ═══════════════════════════════════════════════════════════════
   POWER GYM — Login / Register Page
   tr-login.js  |  Runs on trmem.html only

   Requires tr-common.js to be loaded first (Auth, Session, Navigation,
   showToast, _bindModalBackdrops, _val).
   ═══════════════════════════════════════════════════════════════ */

'use strict';

/* ════════════════════════════════════════════════
   LOGIN PAGE MODULE
════════════════════════════════════════════════ */
const LoginPage = (() => {

  let selectedPlan    = null;
  let selectedPayment = null;
  let selectedProofFile = null;

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

  /** STEP 1 → 2: personal information must be complete and valid before continuing */
  function validateStep1() {
    const firstName = _val('reg-fname');
    const lastName  = _val('reg-lname');
    const email     = _val('reg-email');
    const phone     = _val('reg-phone');
    const password  = document.getElementById('reg-pass')?.value    || '';
    const confirm   = document.getElementById('reg-confirm')?.value || '';

    if (!firstName || !lastName || !email || !password || !confirm) {
      showToast('Please fill in all required fields.', 'error');
      return;
    }
    if (!/^\S+@\S+\.\S+$/.test(email)) {
      showToast('Please enter a valid email address.', 'error');
      return;
    }
    if (!/^09\d{9}$/.test(phone)) {
      showToast('Phone number must start with 09 and be exactly 11 digits.', 'error');
      return;
    }
    if (password.length < 8) {
      showToast('Password must be at least 8 characters.', 'error');
      return;
    }
    if (password !== confirm) {
      showToast('Passwords do not match.', 'error');
      return;
    }

    regNext(2);
  }

  /** STEP 2 → 3: a membership plan must be selected before continuing */
  function validateStep2() {
    if (!selectedPlan) {
      showToast('Please select a membership plan first.', 'error');
      return;
    }
    regNext(3);
  }

  function proceedToPayment() {
    const check = document.getElementById('reg-terms-check');
    if (!check || !check.checked) { showToast('Please agree to the Terms & Policy first', 'error'); return; }
    regNext(4);
  }

  /** Handle proof-of-payment file selection (GCash / PayMaya) */
  function handleProofUpload(input) {
    const file = input.files && input.files[0];
    if (!file) { selectedProofFile = null; return; }

    const maxBytes = 10 * 1024 * 1024;
    if (file.size > maxBytes) {
      showToast('File is too large. Maximum size is 10MB.', 'error');
      input.value = '';
      selectedProofFile = null;
      return;
    }

    selectedProofFile = file;

    const label = document.getElementById('reg-upload-label');
    const icon  = document.getElementById('reg-upload-icon');
    if (icon)  icon.textContent = '✅';
    if (label) label.innerHTML = `Selected: ${file.name}<br><span style="font-size:11px;">Click to change file</span>`;

    const preview = document.getElementById('reg-proof-preview');
    if (preview && file.type.startsWith('image/')) {
      preview.src = URL.createObjectURL(file);
      preview.style.display = 'block';
    } else if (preview) {
      preview.style.display = 'none';
      preview.removeAttribute('src');
    }
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

    // Switching methods resets any previously selected proof file
    if (method !== 'gcash') {
      selectedProofFile = null;
      const fileInput = document.getElementById('reg-proof-file');
      if (fileInput) fileInput.value = '';
      const preview = document.getElementById('reg-proof-preview');
      if (preview) { preview.style.display = 'none'; preview.removeAttribute('src'); }
      const label = document.getElementById('reg-upload-label');
      const icon  = document.getElementById('reg-upload-icon');
      if (icon)  icon.textContent = '📤';
      if (label) label.innerHTML = 'Upload Proof of Payment<br><span style="font-size:11px;">PNG, JPG, PDF up to 10MB</span>';
    }
  }

  function completeRegistration() {
    const firstName = _val('reg-fname');
    const middleInitial = _val('reg-mi');
    const lastName  = _val('reg-lname');
    const extensionName = _val('reg-ext');
    const email     = _val('reg-email');
    const phone     = _val('reg-phone');
    const birthday  = _val('reg-bday');
    const password  = document.getElementById('reg-pass')?.value    || '';
    const confirm   = document.getElementById('reg-confirm')?.value || '';

    // ── Client-side validation ──
    if (!firstName || !lastName || !email || !password) {
      showToast('Please fill in all required fields.', 'error');
      regNext(1);
      return;
    }
    if (!/^09\d{9}$/.test(phone)) {
      showToast('Phone number must start with 09 and be exactly 11 digits.', 'error');
      regNext(1);
      return;
    }
    if (password.length < 8) {
      showToast('Password must be at least 8 characters.', 'error');
      regNext(1);
      return;
    }
    if (password !== confirm) {
      showToast('Passwords do not match.', 'error');
      regNext(1);
      return;
    }
    if (!selectedPlan) {
      showToast('Please select a membership plan.', 'error');
      regNext(2);
      return;
    }
    if (!selectedPayment) {
      showToast('Please select a payment method.', 'error');
      regNext(4);
      return;
    }
    if (selectedPayment === 'gcash' && !selectedProofFile) {
      showToast('Please upload your proof of payment.', 'error');
      regNext(4);
      return;
    }

    const formData = new FormData();
    formData.append('first_name', firstName);
    formData.append('middle_initial', middleInitial);
    formData.append('last_name',  lastName);
    formData.append('extension_name', extensionName);
    formData.append('email',      email);
    formData.append('phone',      phone);
    formData.append('birthday',   birthday);
    formData.append('password',   password);
    formData.append('plan',       selectedPlan);
    formData.append('payment_method', selectedPayment);
    if (selectedProofFile) formData.append('proof', selectedProofFile);

    const submitBtn = document.querySelector('#reg-step-4 .btn-red');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'SUBMITTING...'; }

    fetch('/register', {
      method: 'POST',
      body: formData
    })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.success) {
          showToast(data.error || 'Registration failed. Please try again.', 'error');
          if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'SUBMIT REGISTRATION'; }
          return;
        }
        showToast(data.message || 'Registration submitted! Awaiting verification.', 'success');
        setTimeout(() => {
          Navigation.goToScreen('login');
          if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'SUBMIT REGISTRATION'; }
        }, 1200);
      })
      .catch(() => {
        showToast('Could not reach the server. Please try again.', 'error');
        if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'SUBMIT REGISTRATION'; }
      });
  }

  return { init, detectRoleHint, handleLogin, doLogout, regNext, validateStep1, validateStep2, proceedToPayment, selectPlan, selectPayment, handleProofUpload, completeRegistration };
})();


/* ════════════════════════════════════════════════
   INIT — DOMContentLoaded Bootstrap
════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('screen-login')) return;

  LoginPage.init();

  // Expose login-page functions to inline onclick handlers
  window.detectRoleHint       = LoginPage.detectRoleHint;
  window.handleLogin          = LoginPage.handleLogin;
  window.regNext              = LoginPage.regNext;
  window.validateStep1        = LoginPage.validateStep1;
  window.validateStep2        = LoginPage.validateStep2;
  window.proceedToPayment     = LoginPage.proceedToPayment;
  window.selectPlan           = LoginPage.selectPlan;
  window.selectPayment        = LoginPage.selectPayment;
  window.handleProofUpload    = LoginPage.handleProofUpload;
  window.completeRegistration = LoginPage.completeRegistration;
});