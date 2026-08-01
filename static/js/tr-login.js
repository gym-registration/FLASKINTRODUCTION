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

  let termsRead        = false;

  function init() {
    // Always show the login/register page first when opening the root URL.
    // Existing session remains in storage, but we do not automatically redirect.
    const params = new URLSearchParams(window.location.search);
    if (params.get('screen') === 'register') Navigation.goToScreen('register');

    // Modal backdrop binding
    _bindModalBackdrops();

    // Track whether the user has scrolled through the Terms & Policy content
    const termsBody = document.getElementById('terms-modal-body');
    if (termsBody) termsBody.addEventListener('scroll', _onTermsScroll);
  }

  /** Open the Terms & Policy modal; if the content already fits without scrolling
   *  (nothing left to read), unlock the agreement checkbox immediately. */
  function openTermsModal() {
    openModal('terms-modal');
    const body = document.getElementById('terms-modal-body');
    if (!body) return;
    requestAnimationFrame(() => {
      if (body.scrollHeight <= body.clientHeight + 4) _markTermsRead();
    });
  }

  function _onTermsScroll(e) {
    const body = e.target;
    if (body.scrollTop + body.clientHeight >= body.scrollHeight - 8) _markTermsRead();
  }

  function _markTermsRead() {
    if (termsRead) return;
    termsRead = true;
    const check = document.getElementById('reg-terms-check');
    const hint  = document.getElementById('reg-terms-hint');
    if (check) check.disabled = false;
    if (hint)  hint.style.display = 'none';
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

  // ── Registration flow (single step: personal info only) ──
  /** Membership plan & payment are now chosen later, from the member dashboard. */
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
      return;
    }
    if (!/^[A-Z][a-z'-]*(\s[A-Z][a-z'-]*)*$/.test(firstName) || !/^[A-Z][a-z'-]*(\s[A-Z][a-z'-]*)*$/.test(lastName)) {
      showToast('Each word in the name must start with a capital letter, with the rest in lowercase.', 'error');
      return;
    }
    if (middleInitial && !/^[A-Z][A-Za-z]*$/.test(middleInitial)) {
      showToast('Middle initial can only contain letters and must start with a capital letter.', 'error');
      return;
    }
    if (extensionName && !/^[A-Za-z.\s]+$/.test(extensionName)) {
      showToast('Extension name can only contain letters.', 'error');
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
    const termsCheck = document.getElementById('reg-terms-check');
    if (!termsCheck || !termsCheck.checked) {
      showToast('Please agree to the Terms & Policy.', 'error');
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

    const submitBtn = document.getElementById('reg-submit-btn');
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
        showToast(data.message || 'Account created! Sign in and pick a plan from your dashboard.', 'success');
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

  return { init, detectRoleHint, handleLogin, doLogout, completeRegistration, openTermsModal };
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
  window.openTermsModal       = LoginPage.openTermsModal;
  window.completeRegistration = LoginPage.completeRegistration;
});