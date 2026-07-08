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
    const firstName = _val('reg-fname');
    const lastName  = _val('reg-lname');
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

    const payload = {
      first_name: firstName,
      last_name:  lastName,
      email:      email,
      phone:      phone,
      birthday:   birthday,
      password:   password,
      plan:       selectedPlan,
      payment_method: selectedPayment
    };

    const submitBtn = document.querySelector('#reg-step-4 .btn-red');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'SUBMITTING...'; }

    fetch('/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
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

  return { init, detectRoleHint, handleLogin, doLogout, regNext, proceedToPayment, selectPlan, selectPayment, completeRegistration };
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
  window.proceedToPayment     = LoginPage.proceedToPayment;
  window.selectPlan           = LoginPage.selectPlan;
  window.selectPayment        = LoginPage.selectPayment;
  window.completeRegistration = LoginPage.completeRegistration;
});
