import os
import secrets
import string
import calendar
from dotenv import load_dotenv
load_dotenv()  # Reads variables from a .env file in the project root, if present

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timezone, date, timedelta


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_secret_key')

# ── Timezone: the gym operates on Philippines time, but all timestamps are
#    stored in the database as naive UTC. Convert to Manila only for display. ──
MANILA_TZ = timezone(timedelta(hours=8))


def _to_manila(dt):
    """Convert a naive UTC datetime (as stored in the DB) to an aware
    Philippines-time datetime. Returns None if dt is None."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(MANILA_TZ)


def _now_manila():
    """Current date/time in Philippines time (aware)."""
    return datetime.now(timezone.utc).astimezone(MANILA_TZ)


def _today_manila():
    """Today's calendar date in Philippines time (so date boundaries — e.g.
    'today's attendance' — line up with the actual local day, not UTC's)."""
    return _now_manila().date()


def _add_calendar_month(d, months=1):
    """Add whole calendar month(s) to a date, landing on the same day-of-month
    when possible (e.g. Jan 15 -> Feb 15) and clamping to the last valid day
    when the target month is shorter (e.g. Jan 31 -> Feb 28/29, not Mar 3)."""
    month_index = d.month - 1 + months
    year  = d.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(d.day, last_day)
    return date(year, month, day)


def _plan_expiry(plan, start_date):
    """Compute a plan's expiry date from its start date. Monthly plans track
    real calendar months (28-31 days) instead of a flat 30 days, so 'Feb 1 to
    Mar 1' and 'Jan 1 to Feb 1' both count as one full month."""
    if plan and plan.name == 'Monthly':
        return _add_calendar_month(start_date, 1)
    duration_days = plan.duration_days if plan else 30
    return start_date + timedelta(days=duration_days)


def _manila_day_bounds_utc(day):
    """Given a Philippines calendar date, return the (start, end) naive UTC
    datetimes bounding that local day — for filtering DB columns that are
    stored in UTC (e.g. Attendance.check_in)."""
    start_manila = datetime.combine(day, datetime.min.time()).replace(tzinfo=MANILA_TZ)
    end_manila   = datetime.combine(day, datetime.max.time()).replace(tzinfo=MANILA_TZ)
    return (
        start_manila.astimezone(timezone.utc).replace(tzinfo=None),
        end_manila.astimezone(timezone.utc).replace(tzinfo=None),
    )


DB_USER = os.environ.get('DB_USER', 'root')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
DB_HOST = os.environ.get('DB_HOST', '127.0.0.1')
DB_PORT = os.environ.get('DB_PORT', '3306')
DB_NAME = os.environ.get('DB_NAME', 'gym_db')

app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ── Payment proof uploads ────────────────────────────────────
PROOF_UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads', 'payment_proofs')
PROOF_ALLOWED_EXT   = {'png', 'jpg', 'jpeg', 'pdf'}
PROOF_MAX_BYTES     = 10 * 1024 * 1024  # 10MB
os.makedirs(PROOF_UPLOAD_FOLDER, exist_ok=True)

# ── Flask-Mail configuration ─────────────────────────────────
# Set these as real environment variables (don't hardcode credentials here).
# For Gmail: MAIL_USERNAME is your Gmail address, MAIL_PASSWORD is a 16-char
# "App Password" (not your normal Gmail password) — generate one at
# https://myaccount.google.com/apppasswords (requires 2-Step Verification on).
app.config['MAIL_SERVER']          = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT']            = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS']         = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USE_SSL']         = os.environ.get('MAIL_USE_SSL', 'false').lower() == 'true'
app.config['MAIL_USERNAME']        = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD']        = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER']  = os.environ.get('MAIL_DEFAULT_SENDER', app.config['MAIL_USERNAME'])

mail = Mail(app)

db = SQLAlchemy(app)


def _format_full_name(first_name, last_name, middle_initial=None, extension_name=None):
    """Build 'First M.I. Last Ext.' from parts, skipping any that are blank."""
    parts = [first_name]
    if middle_initial:
        mi = middle_initial.strip().rstrip('.')
        if mi:
            parts.append(f'{mi}.')
    parts.append(last_name)
    full = ' '.join(p for p in parts if p)
    if extension_name and extension_name.strip():
        full += f' {extension_name.strip()}'
    return full



class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    first_name = db.Column(db.String(80), nullable=False)
    middle_initial = db.Column(db.String(5), nullable=True)
    last_name = db.Column(db.String(80), nullable=False)
    extension_name = db.Column(db.String(10), nullable=True)
    email = db.Column(db.String(120), nullable=False, unique=True, index=True)
    phone = db.Column(db.String(20), nullable=True)
    birthday = db.Column(db.Date, nullable=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default='member')
    status = db.Column(db.String(15), nullable=False, default='pending')
    reset_token         = db.Column(db.String(64), nullable=True, unique=True, index=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)
    reset_otp            = db.Column(db.String(255), nullable=True)
    reset_otp_expires    = db.Column(db.DateTime, nullable=True)
    reset_otp_attempts   = db.Column(db.Integer, nullable=False, default=0)
    reset_otp_locked_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    membership       = db.relationship('Membership', back_populates='member', uselist=False, cascade='all, delete-orphan')
    payments         = db.relationship('Payment', foreign_keys='Payment.member_id', back_populates='member', cascade='all, delete-orphan')
    recorded_payments= db.relationship('Payment', foreign_keys='Payment.recorded_by_id', back_populates='recorded_by')
    attendance       = db.relationship('Attendance', foreign_keys='Attendance.member_id', back_populates='member', cascade='all, delete-orphan')
    body_goals       = db.relationship('BodyGoal', back_populates='member', cascade='all, delete-orphan')

    @property
    def full_name(self):
        return _format_full_name(self.first_name, self.last_name, self.middle_initial, self.extension_name)

    def __repr__(self):
        return f"<User {self.id} {self.email} [{self.role}]>"


class MembershipPlan(db.Model):
    __tablename__ = 'membership_plans'
    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name          = db.Column(db.String(50), nullable=False, unique=True)
    duration_days = db.Column(db.Integer, nullable=False)
    price         = db.Column(db.Float, nullable=False)
    is_active     = db.Column(db.Boolean, nullable=False, default=True)

    memberships   = db.relationship('Membership', back_populates='plan')
    payments      = db.relationship('Payment', back_populates='plan')

    def __repr__(self):
        return f"<MembershipPlan {self.name} ₱{self.price}>"


class Membership(db.Model):
    __tablename__ = 'memberships'
    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    member_id   = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                            nullable=False, unique=True, index=True)
    plan_id     = db.Column(db.Integer, db.ForeignKey('membership_plans.id', ondelete='SET NULL'), nullable=True)
    start_date  = db.Column(db.Date, nullable=False)
    expiry_date = db.Column(db.Date, nullable=False)
    status      = db.Column(db.String(10), nullable=False, default='pending')
    created_at  = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at  = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    member = db.relationship('User', back_populates='membership')
    plan   = db.relationship('MembershipPlan', back_populates='memberships')


class Payment(db.Model):
    __tablename__    = 'payments'
    id               = db.Column(db.Integer, primary_key=True, autoincrement=True)
    member_id        = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    plan_id          = db.Column(db.Integer, db.ForeignKey('membership_plans.id'), nullable=True)
    amount           = db.Column(db.Numeric(10, 2), nullable=False)
    method           = db.Column(db.String(32), nullable=False)
    reference_number = db.Column(db.String(60), nullable=True)
    proof_image_path = db.Column(db.String(255), nullable=True)
    is_student            = db.Column(db.Boolean, nullable=False, default=False)
    student_id_image_path = db.Column(db.String(255), nullable=True)
    wants_coach           = db.Column(db.Boolean, nullable=False, default=False)
    coach_name             = db.Column(db.String(60), nullable=True)
    requested_start_date  = db.Column(db.Date, nullable=True)
    status           = db.Column(db.String(10), nullable=False, default='pending')
    recorded_by_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    notes            = db.Column(db.Text, nullable=True)
    paid_at          = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    verified_at      = db.Column(db.DateTime, nullable=True)
    notified         = db.Column(db.Boolean, nullable=False, default=False)

    member      = db.relationship('User', foreign_keys=[member_id], back_populates='payments')
    plan        = db.relationship('MembershipPlan', back_populates='payments')
    recorded_by = db.relationship('User', foreign_keys=[recorded_by_id], back_populates='recorded_payments')


class Attendance(db.Model):
    __tablename__ = 'attendance'
    id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    member_id    = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    check_in     = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    check_out    = db.Column(db.DateTime, nullable=True)
    duration_min = db.Column(db.Integer, nullable=True)
    logged_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    member    = db.relationship('User', foreign_keys=[member_id], back_populates='attendance')
    logged_by = db.relationship('User', foreign_keys=[logged_by_id])


class BodyGoal(db.Model):
    __tablename__    = 'body_goals'
    id               = db.Column(db.Integer, primary_key=True, autoincrement=True)
    member_id        = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    current_weight   = db.Column(db.Numeric(5, 2), nullable=True)
    current_body_fat = db.Column(db.Numeric(5, 2), nullable=True)
    current_muscle   = db.Column(db.Numeric(5, 2), nullable=True)
    current_bmi      = db.Column(db.Numeric(5, 2), nullable=True)
    goal_weight      = db.Column(db.Numeric(5, 2), nullable=True)
    goal_body_fat    = db.Column(db.Numeric(5, 2), nullable=True)
    goal_muscle      = db.Column(db.Numeric(5, 2), nullable=True)
    notes            = db.Column(db.Text, nullable=True)
    recorded_at      = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    member = db.relationship('User', back_populates='body_goals')


class Announcement(db.Model):
    __tablename__ = 'announcements'
    id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title        = db.Column(db.String(120), nullable=False)
    body         = db.Column(db.Text, nullable=False)
    target       = db.Column(db.String(20), nullable=False, default='all')
    posted_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    is_active    = db.Column(db.Boolean, nullable=False, default=True)
    created_at   = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at   = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                             onupdate=lambda: datetime.now(timezone.utc))

    posted_by = db.relationship('User', foreign_keys=[posted_by_id])


# ── Routes ────────────────────────────────────────────────────
@app.route('/')
@app.route('/home')
def home():
    return render_template('home.html')

@app.route('/trmem')
@app.route('/trmem.html')
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Please enter both email and password.', 'error')
            return render_template('trmem.html')

        user = User.query.filter_by(email=email).first()
        if user is None or not check_password_hash(user.password, password):
            flash('Invalid credentials.', 'error')
            return render_template('trmem.html')

        session['user_id'] = user.id
        session['role']    = user.role
        session['email']   = user.email
        session['name']    = user.full_name
        return redirect(url_for(user.role))

    return render_template('trmem.html')


# ── Forgot / Reset Password (OTP-based) ──────────────────────
OTP_LENGTH           = 6
OTP_VALID_MINUTES    = 10
OTP_MAX_ATTEMPTS     = 3
OTP_LOCKOUT_MINUTES  = 30


def _generate_otp():
    return ''.join(secrets.choice(string.digits) for _ in range(OTP_LENGTH))


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        user  = User.query.filter_by(email=email).first() if email else None
        now   = _now()

        if user:
            # Still locked out from too many failed OTP attempts — don't send a new code yet.
            if user.reset_otp_locked_until and user.reset_otp_locked_until > now:
                remaining = int((user.reset_otp_locked_until - now).total_seconds() // 60) + 1
                flash(f'Too many incorrect attempts. Please wait {remaining} more minute(s) before requesting a new code.', 'error')
                return render_template('forgot-password.html')

            otp = _generate_otp()
            user.reset_otp              = generate_password_hash(otp)
            user.reset_otp_expires      = now + timedelta(minutes=OTP_VALID_MINUTES)
            user.reset_otp_attempts     = 0
            user.reset_otp_locked_until = None
            db.session.commit()

            session['otp_email'] = email

            if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD'):
                try:
                    msg = Message(
                        subject='POWER GYM — Your Password Reset Code',
                        recipients=[email],
                        body=(
                            f"Hi {user.first_name},\n\n"
                            f"Your POWER GYM password reset code is: {otp}\n\n"
                            f"This code expires in {OTP_VALID_MINUTES} minutes and can be entered up to "
                            f"{OTP_MAX_ATTEMPTS} times before you'll need to request a new one.\n\n"
                            f"If you didn't request this, you can safely ignore this email."
                        ),
                    )
                    mail.send(msg)
                    flash('A verification code has been sent to your email.', 'success')
                except Exception as e:
                    print(f"[MAIL ERROR] Could not send OTP email to {email}: {e}")
                    flash('Could not send the verification code right now. Please try again shortly.', 'error')
                    return render_template('forgot-password.html')
            else:
                # No MAIL_USERNAME/MAIL_PASSWORD configured — dev fallback so the
                # flow stays testable without SMTP credentials set up yet.
                print(f"[DEV] Email not configured. OTP for {email}: {otp}")
                flash(f'Email is not configured yet — here is your code (dev mode): {otp}', 'success')

            return redirect(url_for('verify_otp'))
        else:
            # Same message whether or not the email exists, so we don't leak
            # which addresses are registered.
            flash('If an account with that email exists, a verification code has been sent.', 'success')

        return render_template('forgot-password.html')

    return render_template('forgot-password.html')


@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    email = session.get('otp_email')
    if not email:
        flash('Please request a verification code first.', 'error')
        return redirect(url_for('forgot_password'))

    user = User.query.filter_by(email=email).first()
    now  = _now()

    if not user:
        session.pop('otp_email', None)
        return redirect(url_for('forgot_password'))

    if user.reset_otp_locked_until and user.reset_otp_locked_until > now:
        remaining = int((user.reset_otp_locked_until - now).total_seconds() // 60) + 1
        flash(f'Too many incorrect attempts. Please wait {remaining} more minute(s) and request a new code.', 'error')
        session.pop('otp_email', None)
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        code = (request.form.get('otp') or '').strip()

        if not user.reset_otp or not user.reset_otp_expires or user.reset_otp_expires <= now:
            flash('Your verification code has expired. Please request a new one.', 'error')
            session.pop('otp_email', None)
            return redirect(url_for('forgot_password'))

        if check_password_hash(user.reset_otp, code):
            # Correct code — issue a short-lived token for the actual password-change screen.
            token = secrets.token_urlsafe(32)
            user.reset_token            = token
            user.reset_token_expires    = now + timedelta(minutes=15)
            user.reset_otp              = None
            user.reset_otp_expires      = None
            user.reset_otp_attempts     = 0
            user.reset_otp_locked_until = None
            db.session.commit()
            session.pop('otp_email', None)
            return redirect(url_for('reset_password', token=token))

        # Wrong code — count the attempt, lock out after 3 in a row.
        user.reset_otp_attempts = (user.reset_otp_attempts or 0) + 1
        if user.reset_otp_attempts >= OTP_MAX_ATTEMPTS:
            user.reset_otp_locked_until = now + timedelta(minutes=OTP_LOCKOUT_MINUTES)
            user.reset_otp             = None
            user.reset_otp_expires     = None
            db.session.commit()
            session.pop('otp_email', None)
            flash(f'Too many incorrect attempts. Please wait {OTP_LOCKOUT_MINUTES} minutes before requesting a new code.', 'error')
            return redirect(url_for('forgot_password'))

        db.session.commit()
        remaining_attempts = OTP_MAX_ATTEMPTS - user.reset_otp_attempts
        flash(f'Incorrect code. {remaining_attempts} attempt(s) remaining.', 'error')
        return render_template('verify-otp.html', email=email)

    return render_template('verify-otp.html', email=email)


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    now = _now()
    user = User.query.filter_by(reset_token=token).first()
    token_valid = user is not None and user.reset_token_expires is not None and user.reset_token_expires > now

    if not token_valid:
        flash('This reset link is invalid or has expired. Please request a new code.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password') or ''
        confirm  = request.form.get('confirm')  or ''

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('reset-password.html', token=token)

        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('reset-password.html', token=token)

        user.password             = generate_password_hash(password)
        user.reset_token          = None
        user.reset_token_expires  = None
        db.session.commit()

        flash('Password reset successfully. Please sign in with your new password.', 'success')
        return redirect(url_for('login'))

    return render_template('reset-password.html', token=token)


@app.route('/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or request.form

    first_name = (data.get('first_name') or '').strip()
    middle_initial = (data.get('middle_initial') or '').strip()
    last_name  = (data.get('last_name')  or '').strip()
    extension_name = (data.get('extension_name') or '').strip()
    email      = (data.get('email')      or '').strip().lower()
    phone      = (data.get('phone')      or '').strip()
    birthday   = (data.get('birthday')   or '').strip()
    password   = data.get('password')    or ''

    # ── Validation ──
    if not first_name or not last_name or not email or not password:
        return jsonify(success=False, error='Please fill in all required fields.'), 400

    if not _valid_name(first_name, require_capital=True, lowercase_rest=True) or not _valid_name(last_name, require_capital=True, lowercase_rest=True):
        return jsonify(success=False, error='First and last name must start with a capital letter, with the rest in lowercase.'), 400

    if not _valid_name(middle_initial, extra_chars='', require_capital=True):
        return jsonify(success=False, error='Middle initial can only contain letters and must start with a capital letter.'), 400

    if not _valid_name(extension_name, extra_chars=' .'):
        return jsonify(success=False, error='Extension name can only contain letters.'), 400

    if len(password) < 8:
        return jsonify(success=False, error='Password must be at least 8 characters.'), 400

    if not _valid_phone(phone):
        return jsonify(success=False, error='Phone number must start with 09 and be exactly 11 digits.'), 400

    if User.query.filter_by(email=email).first() is not None:
        return jsonify(success=False, error='An account with this email already exists.'), 409

    birthday_date = None
    if birthday:
        try:
            birthday_date = datetime.strptime(birthday, '%Y-%m-%d').date()
        except ValueError:
            birthday_date = None

    # ── Create the user ──
    new_user = User(
        first_name=first_name,
        middle_initial=middle_initial or None,
        last_name=last_name,
        extension_name=extension_name or None,
        email=email,
        phone=phone or None,
        birthday=birthday_date,
        password=generate_password_hash(password),
        role='member',
        status='pending',
    )
    db.session.add(new_user)
    db.session.commit()

    return jsonify(success=True, message='Account created! Sign in and pick a plan from your dashboard.')


def _generate_temp_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _valid_phone(phone):
    """Phone number must be exactly 11 digits and start with '09' (PH mobile format)."""
    return phone.isdigit() and len(phone) == 11 and phone.startswith('09')


def _valid_name(name, extra_chars=" '-", require_capital=False, lowercase_rest=False):
    """Name fields must contain only letters (plus a few allowed punctuation chars) — no digits.
    When require_capital is True, the first character must also be an uppercase letter.
    When lowercase_rest is True, each space-separated word must be in Title Case
    (first letter capitalized, remaining letters in that word lowercase) — e.g. 'Dela Cruz'."""
    if not name:
        return True
    if not all(ch.isalpha() or ch in extra_chars for ch in name):
        return False
    if require_capital and not name[0].isupper():
        return False
    if lowercase_rest:
        for word in name.split():
            if not word[0].isupper():
                return False
            if any(ch.isalpha() and ch.isupper() for ch in word[1:]):
                return False
    return True


@app.route('/admin/add-member', methods=['POST'])
def admin_add_member():
    if session.get('role') != 'admin':
        return jsonify(success=False, error='Unauthorized.'), 403

    data = request.get_json(silent=True) or request.form

    first_name = (data.get('first_name') or '').strip()
    middle_initial = (data.get('middle_initial') or '').strip()
    last_name  = (data.get('last_name')  or '').strip()
    extension_name = (data.get('extension_name') or '').strip()
    email      = (data.get('email')      or '').strip().lower()
    phone      = (data.get('phone')      or '').strip()
    plan_name  = (data.get('plan')       or '').strip()

    if not first_name or not last_name or not email:
        return jsonify(success=False, error='Please fill in first name, last name, and email.'), 400

    if not _valid_phone(phone):
        return jsonify(success=False, error='Phone number must start with 09 and be exactly 11 digits.'), 400

    if User.query.filter_by(email=email).first() is not None:
        return jsonify(success=False, error='A user with this email already exists.'), 409

    plan = MembershipPlan.query.filter_by(name=plan_name).first()
    if plan is None:
        return jsonify(success=False, error='Please select a valid membership plan.'), 400

    # Admin-added members are walk-ins who already paid at the desk,
    # so they're activated immediately (unlike self-registration, which is 'pending').
    temp_password = _generate_temp_password()
    new_user = User(
        first_name=first_name,
        middle_initial=middle_initial or None,
        last_name=last_name,
        extension_name=extension_name or None,
        email=email,
        phone=phone or None,
        password=generate_password_hash(temp_password),
        role='member',
        status='active',
    )
    db.session.add(new_user)
    db.session.flush()

    start = _today_manila()
    expiry = _plan_expiry(plan, start)
    new_membership = Membership(
        member_id=new_user.id,
        plan_id=plan.id,
        start_date=start,
        expiry_date=expiry,
        status='active',
    )
    db.session.add(new_membership)
    db.session.commit()

    return jsonify(
        success=True,
        message='Member added successfully.',
        member={
            'id': new_user.id,
            'name': new_user.full_name,
            'first_name': new_user.first_name,
            'middle_initial': new_user.middle_initial or '',
            'last_name': new_user.last_name,
            'extension_name': new_user.extension_name or '',
            'email': email,
            'phone': new_user.phone or '',
            'plan': plan.name,
            'expiry': expiry.strftime('%b %d, %Y'),
            'temp_password': temp_password,
        }
    )


@app.route('/admin/edit-member/<int:member_id>', methods=['POST'])
def admin_edit_member(member_id):
    if session.get('role') != 'admin':
        return jsonify(success=False, error='Unauthorized.'), 403

    user = User.query.filter_by(id=member_id, role='member').first()
    if user is None:
        return jsonify(success=False, error='Member not found.'), 404

    data = request.get_json(silent=True) or request.form

    first_name = (data.get('first_name') or '').strip()
    middle_initial = (data.get('middle_initial') or '').strip()
    last_name  = (data.get('last_name')  or '').strip()
    extension_name = (data.get('extension_name') or '').strip()
    email      = (data.get('email')      or '').strip().lower()
    phone      = (data.get('phone')      or '').strip()
    plan_name  = (data.get('plan')       or '').strip()
    expiry_str = (data.get('expiry')     or '').strip()

    if not first_name or not last_name or not email:
        return jsonify(success=False, error='First name, last name, and email are required.'), 400

    if not _valid_phone(phone):
        return jsonify(success=False, error='Phone number must start with 09 and be exactly 11 digits.'), 400

    # Check email isn't taken by someone else
    existing = User.query.filter(User.email == email, User.id != member_id).first()
    if existing is not None:
        return jsonify(success=False, error='Another account already uses this email.'), 409

    user.first_name = first_name
    user.middle_initial = middle_initial or None
    user.last_name  = last_name
    user.extension_name = extension_name or None
    user.email      = email
    user.phone      = phone or None

    membership = Membership.query.filter_by(member_id=user.id).first()

    plan = MembershipPlan.query.filter_by(name=plan_name).first() if plan_name else None
    expiry_date = None
    if expiry_str:
        try:
            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
        except ValueError:
            expiry_date = None

    if membership is None and (plan is not None or expiry_date is not None):
        membership = Membership(
            member_id=user.id,
            plan_id=plan.id if plan else None,
            start_date=_today_manila(),
            expiry_date=expiry_date or _today_manila(),
            status='active',
        )
        db.session.add(membership)
    elif membership is not None:
        if plan is not None:
            membership.plan_id = plan.id
        if expiry_date is not None:
            membership.expiry_date = expiry_date

    db.session.commit()

    plan_display   = membership.plan.name if (membership and membership.plan) else '—'
    expiry_display = membership.expiry_date.strftime('%b %d, %Y') if (membership and membership.expiry_date) else '—'
    expiry_iso     = membership.expiry_date.isoformat() if (membership and membership.expiry_date) else ''

    return jsonify(
        success=True,
        member={
            'name': user.full_name,
            'first_name': user.first_name,
            'middle_initial': user.middle_initial or '',
            'last_name': user.last_name,
            'extension_name': user.extension_name or '',
            'email': email,
            'phone': user.phone or '',
            'plan': plan_display,
            'expiry': expiry_display,
            'expiry_iso': expiry_iso,
        }
    )


@app.route('/admin/delete-member/<int:member_id>', methods=['POST'])
def admin_delete_member(member_id):
    if session.get('role') != 'admin':
        return jsonify(success=False, error='Unauthorized.'), 403

    user = User.query.filter_by(id=member_id, role='member').first()
    if user is None:
        return jsonify(success=False, error='Member not found.'), 404

    db.session.delete(user)
    db.session.commit()

    return jsonify(success=True, message='Member deleted successfully.')


@app.route('/admin/verify-payment/<int:payment_id>', methods=['POST'])
def admin_verify_payment(payment_id):
    if session.get('role') not in ('admin', 'staff'):
        return jsonify(success=False, error='Unauthorized.'), 403

    data = request.get_json(silent=True) or request.form
    action = (data.get('action') or '').strip().lower()
    if action not in ('approve', 'reject'):
        return jsonify(success=False, error='Invalid action.'), 400

    payment = Payment.query.get(payment_id)
    if payment is None:
        return jsonify(success=False, error='Payment not found.'), 404
    if payment.status != 'pending':
        return jsonify(success=False, error='This payment has already been processed.'), 409

    if action == 'reject':
        payment.status = 'rejected'
        payment.verified_at = datetime.now(timezone.utc)
        db.session.commit()
        return jsonify(success=True, message='Payment rejected.', status='rejected')

    # ── Approve: activate/extend membership (same logic as staff_record_payment) ──
    payment.status = 'verified'
    payment.verified_at = datetime.now(timezone.utc)

    member = payment.member
    plan   = payment.plan
    today  = _today_manila()

    membership = Membership.query.filter_by(member_id=member.id).first()
    if membership is None:
        membership = Membership(
            member_id=member.id,
            plan_id=plan.id if plan else None,
            start_date=today,
            expiry_date=today,
            status='active',
        )
        db.session.add(membership)

    # If the member requested a future start date and doesn't already have
    # unexpired time on their account, honor that date instead of "today".
    requested_start = payment.requested_start_date
    if requested_start and (not membership.expiry_date or membership.expiry_date <= today):
        base_date = requested_start if requested_start > today else today
        membership.start_date = base_date
    else:
        base_date = membership.expiry_date if membership.expiry_date and membership.expiry_date > today else today

    membership.expiry_date = _plan_expiry(plan, base_date)
    if plan is not None:
        membership.plan_id = plan.id
    membership.status = 'active'

    if member.status != 'active':
        member.status = 'active'

    db.session.commit()

    return jsonify(
        success=True,
        message='Payment approved — membership activated!',
        status='verified',
        expiry=membership.expiry_date.strftime('%b %d, %Y'),
    )


def _find_member(identifier):
    """Resolve a member from a name, email, or #id string. Returns (user, error_message)."""
    identifier = (identifier or '').strip()
    if not identifier:
        return None, 'Please enter a member name, ID, or email.'

    # #1001 or plain numeric id
    numeric = identifier.lstrip('#')
    if numeric.isdigit():
        user = User.query.filter_by(id=int(numeric), role='member').first()
        return (user, None) if user else (None, 'No member found with that ID.')

    # email
    if '@' in identifier:
        user = User.query.filter_by(email=identifier.lower(), role='member').first()
        return (user, None) if user else (None, 'No member found with that email.')

    # full name match (case-insensitive)
    matches = User.query.filter(
        db.func.lower(db.func.concat(User.first_name, ' ', User.last_name)) == identifier.lower(),
        User.role == 'member'
    ).all()
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, 'Multiple members share that name — please use their ID or email instead.'
    return None, 'No member found with that name.'


@app.route('/member/submit-payment', methods=['POST'])
def member_submit_payment():
    if session.get('role') != 'member':
        return jsonify(success=False, error='Unauthorized.'), 403

    user = User.query.get(session.get('user_id'))
    if user is None:
        return jsonify(success=False, error='Unauthorized.'), 403

    today = _today_manila()

    existing_membership = Membership.query.filter_by(member_id=user.id).first()
    if (existing_membership and existing_membership.status == 'active'
            and existing_membership.expiry_date and existing_membership.expiry_date >= today):
        plan_name = existing_membership.plan.name if existing_membership.plan else 'a plan'
        return jsonify(
            success=False,
            error=f'You are currently registered to the {plan_name} plan (active until '
                  f'{existing_membership.expiry_date.strftime("%b %d, %Y")}). '
                  f'You can request a new plan once it expires.'
        ), 409

    data = request.form
    plan_key    = (data.get('plan')        or '').strip().lower()
    is_student  = (data.get('is_student')  or '').strip().lower() in ('1', 'true', 'yes')
    wants_coach = (data.get('wants_coach') or '').strip().lower() in ('1', 'true', 'yes')
    coach_name  = (data.get('coach_name')  or '').strip()
    start_date_raw = (data.get('start_date') or '').strip()

    if not start_date_raw:
        return jsonify(success=False, error='Please choose a start date for your plan.'), 400
    try:
        requested_start = date.fromisoformat(start_date_raw)
    except ValueError:
        return jsonify(success=False, error='Invalid start date.'), 400
    if requested_start < today:
        return jsonify(success=False, error='Start date cannot be in the past.'), 400

    plan_name_map = {
        'daily': 'Daily',
        'weekly': 'Weekly',
        'monthly': 'Monthly',
        'yearly': 'Yearly',
    }
    if plan_key not in plan_name_map:
        return jsonify(success=False, error='Please select a membership plan.'), 400

    plan = MembershipPlan.query.filter_by(name=plan_name_map[plan_key]).first()
    if plan is None:
        return jsonify(success=False, error='Selected plan is not available.'), 400

    valid_coaches = {'Ronel Samar', 'Jonathan Natividad'}
    if wants_coach and coach_name not in valid_coaches:
        return jsonify(success=False, error='Please select a coach.'), 400
    if not wants_coach:
        coach_name = None

    # ── Student ID proof (required only if the member says they're a student) ──
    student_id_relative_path = None
    if is_student:
        student_id_file = request.files.get('student_id')
        if not student_id_file or not student_id_file.filename:
            return jsonify(success=False, error='Please upload a photo of your school ID.'), 400

        ext = student_id_file.filename.rsplit('.', 1)[-1].lower() if '.' in student_id_file.filename else ''
        if ext not in PROOF_ALLOWED_EXT:
            return jsonify(success=False, error='School ID must be a PNG, JPG, or PDF file.'), 400

        student_id_file.seek(0, os.SEEK_END)
        size = student_id_file.tell()
        student_id_file.seek(0)
        if size > PROOF_MAX_BYTES:
            return jsonify(success=False, error='School ID file is too large (max 10MB).'), 400

        safe_name = secure_filename(f"{secrets.token_hex(8)}_{student_id_file.filename}")
        student_id_file.save(os.path.join(PROOF_UPLOAD_FOLDER, safe_name))
        student_id_relative_path = f"uploads/payment_proofs/{safe_name}"

    # ── Record the plan request as pending — no payment details are collected
    #    here. Payment method/reference/proof are submitted separately from
    #    the Payment tab (see /member/submit-payment-method below), then
    #    staff/admin verify and approve. ──
    new_payment = Payment(
        member_id=user.id,
        plan_id=plan.id,
        amount=plan.price,
        method='Pending — to be confirmed by staff',
        reference_number=None,
        proof_image_path=None,
        is_student=is_student,
        student_id_image_path=student_id_relative_path,
        wants_coach=wants_coach,
        coach_name=coach_name,
        requested_start_date=requested_start,
        status='pending',
    )
    db.session.add(new_payment)

    # ── Reflect the pending selection on the membership record so admin/staff
    #    can see what's awaiting verification. Don't touch an already-active
    #    membership — that stays active until the renewal is approved.
    membership = Membership.query.filter_by(member_id=user.id).first()
    if membership is None:
        membership = Membership(
            member_id=user.id,
            plan_id=plan.id,
            start_date=requested_start,
            expiry_date=_plan_expiry(plan, requested_start),
            status='pending',
        )
        db.session.add(membership)
    elif membership.status != 'active':
        membership.plan_id     = plan.id
        membership.start_date  = requested_start
        membership.expiry_date = _plan_expiry(plan, requested_start)
        membership.status      = 'pending'

    db.session.commit()

    return jsonify(success=True, message=f'Plan requested to start {requested_start.strftime("%b %d, %Y")}. Please wait for staff approval before proceeding to payment.')


@app.route('/member/submit-payment-method', methods=['POST'])
def member_submit_payment_method():
    """Called from the Payment tab: attaches the chosen payment method
    (Cash or GCash) — plus reference number and proof screenshot for GCash —
    to the member's current pending plan request."""
    if session.get('role') != 'member':
        return jsonify(success=False, error='Unauthorized.'), 403

    user = User.query.get(session.get('user_id'))
    if user is None:
        return jsonify(success=False, error='Unauthorized.'), 403

    payment = (
        Payment.query
        .filter_by(member_id=user.id, status='pending')
        .order_by(Payment.paid_at.desc())
        .first()
    )
    if payment is None:
        return jsonify(success=False, error='No pending plan request found. Please request a plan first.'), 400
    if not payment.method.startswith('Pending'):
        return jsonify(success=False, error='Payment already submitted for this request.'), 400

    data = request.form
    payment_method  = (data.get('payment_method')  or '').strip().lower()
    gcash_reference = (data.get('gcash_reference') or '').strip()

    if payment_method not in ('cash', 'gcash'):
        return jsonify(success=False, error='Please select a payment method.'), 400

    if payment_method == 'gcash':
        if not gcash_reference:
            return jsonify(success=False, error='Please enter your GCash reference number.'), 400

        gcash_proof_file = request.files.get('gcash_proof')
        if not gcash_proof_file or not gcash_proof_file.filename:
            return jsonify(success=False, error='Please attach a screenshot of your GCash proof of payment.'), 400

        ext = gcash_proof_file.filename.rsplit('.', 1)[-1].lower() if '.' in gcash_proof_file.filename else ''
        if ext not in PROOF_ALLOWED_EXT:
            return jsonify(success=False, error='Proof of payment must be a PNG, JPG, or PDF file.'), 400

        gcash_proof_file.seek(0, os.SEEK_END)
        size = gcash_proof_file.tell()
        gcash_proof_file.seek(0)
        if size > PROOF_MAX_BYTES:
            return jsonify(success=False, error='Proof of payment file is too large (max 10MB).'), 400

        safe_name = secure_filename(f"{secrets.token_hex(8)}_{gcash_proof_file.filename}")
        gcash_proof_file.save(os.path.join(PROOF_UPLOAD_FOLDER, safe_name))
        proof_relative_path = f"uploads/payment_proofs/{safe_name}"

        payment.method            = 'GCash'
        payment.reference_number  = gcash_reference
        payment.proof_image_path  = proof_relative_path
    else:
        payment.method            = 'Cash — to be confirmed by staff'
        payment.reference_number  = None
        payment.proof_image_path  = None

    db.session.commit()

    if payment_method == 'gcash':
        message = 'GCash payment submitted! Awaiting verification by staff or admin.'
    else:
        message = 'Got it — please settle your Cash payment at the front desk with staff.'

    return jsonify(success=True, message=message)


@app.route('/staff/record-payment', methods=['POST'])
def staff_record_payment():
    if session.get('role') not in ('staff', 'admin'):
        return jsonify(success=False, error='Unauthorized.'), 403

    data = request.get_json(silent=True) or request.form
    member_identifier = data.get('member_identifier') or ''
    plan_name          = (data.get('plan')      or '').strip()
    method              = (data.get('method')    or '').strip()
    reference           = (data.get('reference') or '').strip()

    member, error = _find_member(member_identifier)
    if error:
        return jsonify(success=False, error=error), 404

    plan = MembershipPlan.query.filter_by(name=plan_name).first()
    if plan is None:
        return jsonify(success=False, error='Please select a valid membership plan.'), 400

    if not method:
        return jsonify(success=False, error='Please select a payment method.'), 400

    new_payment = Payment(
        member_id=member.id,
        plan_id=plan.id,
        amount=plan.price,
        method=method,
        reference_number=reference or None,
        status='verified',
        recorded_by_id=session.get('user_id'),
        verified_at=datetime.now(timezone.utc),
    )
    db.session.add(new_payment)

    # Extend (or create) the member's membership, starting from whichever is later:
    # today, or their current expiry date (so early renewals stack on top of remaining time).
    today = _today_manila()
    membership = Membership.query.filter_by(member_id=member.id).first()
    if membership is None:
        membership = Membership(member_id=member.id, plan_id=plan.id, start_date=today,
                                 expiry_date=today, status='active')
        db.session.add(membership)

    base_date = membership.expiry_date if membership.expiry_date and membership.expiry_date > today else today
    membership.plan_id     = plan.id
    membership.expiry_date = _plan_expiry(plan, base_date)
    membership.status      = 'active'
    if member.status != 'active':
        member.status = 'active'

    db.session.commit()

    return jsonify(
        success=True,
        message='Payment recorded successfully.',
        payment={
            'member_name': member.full_name,
            'plan': plan.name,
            'amount': str(plan.price),
            'expiry': membership.expiry_date.strftime('%b %d, %Y'),
        }
    )


@app.route('/staff/checkin', methods=['POST'])
def staff_checkin():
    if session.get('role') not in ('staff', 'admin'):
        return jsonify(success=False, error='Unauthorized.'), 403

    data = request.get_json(silent=True) or request.form
    member, error = _find_member(data.get('member_identifier'))
    if error:
        return jsonify(success=False, error=error), 404

    open_entry = Attendance.query.filter_by(member_id=member.id, check_out=None).first()
    if open_entry is not None:
        return jsonify(success=False, error=f'{member.first_name} is already checked in.'), 409

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    entry = Attendance(member_id=member.id, check_in=now, logged_by_id=session.get('user_id'))
    db.session.add(entry)
    db.session.commit()

    return jsonify(
        success=True,
        message='Check-in recorded.',
        member_name=member.full_name,
        time=_to_manila(now).strftime('%I:%M %p').lstrip('0'),
    )


@app.route('/staff/checkout', methods=['POST'])
def staff_checkout():
    if session.get('role') not in ('staff', 'admin'):
        return jsonify(success=False, error='Unauthorized.'), 403

    data = request.get_json(silent=True) or request.form
    member, error = _find_member(data.get('member_identifier'))
    if error:
        return jsonify(success=False, error=error), 404

    entry = (
        Attendance.query
        .filter_by(member_id=member.id, check_out=None)
        .order_by(Attendance.check_in.desc())
        .first()
    )
    if entry is None:
        return jsonify(success=False, error=f'{member.first_name} has no open check-in to close.'), 409

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    entry.check_out = now
    entry.duration_min = int((now - entry.check_in).total_seconds() // 60)
    db.session.commit()

    h, m = divmod(entry.duration_min, 60)
    duration_text = f'{h}h {m}m' if h else f'{m}m'

    return jsonify(
        success=True,
        message='Check-out recorded.',
        member_name=member.full_name,
        time=_to_manila(now).strftime('%I:%M %p').lstrip('0'),
        duration=duration_text,
    )


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Account Settings (Personal Info + Change Password) ───────
# Shared across all three roles — admin, staff, and member all edit the
# same `users` row, so one pair of routes serves every dashboard's
# Settings tab.

@app.route('/update-profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return jsonify(success=False, error='Not logged in.'), 401

    user = User.query.get(session['user_id'])
    if user is None:
        session.clear()
        return jsonify(success=False, error='User not found.'), 404

    data = request.get_json(silent=True) or {}
    first_name     = (data.get('first_name') or '').strip()
    middle_initial = (data.get('middle_initial') or '').strip()
    last_name      = (data.get('last_name') or '').strip()
    extension_name = (data.get('extension_name') or '').strip()
    email          = (data.get('email') or '').strip()
    phone          = (data.get('phone') or '').strip()
    birthday_str   = (data.get('birthday') or '').strip()

    if not first_name or not last_name or not email:
        return jsonify(success=False, error='First name, last name, and email are required.'), 400
    if not _valid_name(first_name, require_capital=True, lowercase_rest=True) or not _valid_name(last_name, require_capital=True, lowercase_rest=True):
        return jsonify(success=False, error='Names must start with a capital letter, with the rest in lowercase.'), 400
    if middle_initial and not _valid_name(middle_initial, extra_chars='', require_capital=True):
        return jsonify(success=False, error='Middle initial can only contain letters and must start with a capital letter.'), 400
    if extension_name and not _valid_name(extension_name, extra_chars='. '):
        return jsonify(success=False, error='Extension name can only contain letters.'), 400
    if phone and not _valid_phone(phone):
        return jsonify(success=False, error='Phone number must start with 09 and be exactly 11 digits.'), 400

    existing = User.query.filter(User.email == email, User.id != user.id).first()
    if existing:
        return jsonify(success=False, error='That email is already in use by another account.'), 400

    birthday = user.birthday
    if birthday_str:
        try:
            birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify(success=False, error='Invalid birthday format.'), 400

    user.first_name     = first_name
    user.middle_initial = middle_initial or None
    user.last_name      = last_name
    user.extension_name = extension_name or None
    user.email          = email
    user.phone          = phone or None
    user.birthday       = birthday
    db.session.commit()

    # Keep the session in sync so the sidebar/header reflect the change
    # immediately without requiring a fresh login.
    session['name']  = user.full_name
    session['email'] = user.email

    initials = (user.first_name[0] + user.last_name[0]).upper() if user.first_name and user.last_name else ''

    return jsonify(success=True, message='Profile updated successfully.', user={
        'name':     user.full_name,
        'email':    user.email,
        'initials': initials,
        'phone':    user.phone or '',
        'birthday': user.birthday.isoformat() if user.birthday else '',
    })


@app.route('/change-password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        return jsonify(success=False, error='Not logged in.'), 401

    user = User.query.get(session['user_id'])
    if user is None:
        session.clear()
        return jsonify(success=False, error='User not found.'), 404

    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password', '')
    new_password     = data.get('new_password', '')
    confirm_password = data.get('confirm_password', '')

    if not current_password or not new_password or not confirm_password:
        return jsonify(success=False, error='Please fill in all fields.'), 400
    if not check_password_hash(user.password, current_password):
        return jsonify(success=False, error='Current password is incorrect.'), 400
    if len(new_password) < 8:
        return jsonify(success=False, error='New password must be at least 8 characters.'), 400
    if new_password != confirm_password:
        return jsonify(success=False, error='New password and confirmation do not match.'), 400

    user.password = generate_password_hash(new_password)
    db.session.commit()

    return jsonify(success=True, message='Password changed successfully.')


@app.route('/member')
def member():
    if 'role' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'member':
        return redirect(url_for(session.get('role', 'login')))

    user = User.query.get(session['user_id'])
    if user is None:
        session.clear()
        return redirect(url_for('login'))

    today = _today_manila()

    # ── Current plan / membership ──
    membership = Membership.query.filter_by(member_id=user.id).first()
    plan_obj   = membership.plan if membership else None

    current_plan = None
    if membership and plan_obj:
        days_total = max((membership.expiry_date - membership.start_date).days, 1)
        days_left  = max((membership.expiry_date - today).days, 0)
        days_used  = max(min(days_total - days_left, days_total), 0)
        percent_used = int((days_used / days_total) * 100) if days_total else 0

        if membership.expiry_date < today:
            plan_status = 'Expired'
        elif membership.status == 'pending':
            plan_status = 'Pending'
        else:
            plan_status = 'Active'

        current_plan = {
            'name': plan_obj.name,
            'price': plan_obj.price,
            'start_date': membership.start_date.strftime('%B %d, %Y'),
            'expiry_date': membership.expiry_date.strftime('%B %d, %Y'),
            'days_left': days_left,
            'days_total': days_total,
            'days_used': days_used,
            'percent_used': percent_used,
            'status': plan_status,
        }

    # ── Attendance (current month) ──
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    month_start    = today.replace(day=1)
    month_start_dt, _ = _manila_day_bounds_utc(month_start)

    attendance_rows = (
        Attendance.query
        .filter(Attendance.member_id == user.id, Attendance.check_in >= month_start_dt)
        .order_by(Attendance.check_in.desc())
        .all()
    )

    present_days = sorted({_to_manila(a.check_in).day for a in attendance_rows})

    session_history = []
    for a in attendance_rows[:10]:
        duration_text = '—'
        if a.check_out:
            mins = a.duration_min if a.duration_min is not None else int((a.check_out - a.check_in).total_seconds() // 60)
            h, m = divmod(mins, 60)
            duration_text = f'{h}h {m}m' if h else f'{m}m'
        check_in_manila  = _to_manila(a.check_in)
        check_out_manila = _to_manila(a.check_out)
        session_history.append({
            'date':      check_in_manila.strftime('%b %d, %Y'),
            'check_in':  check_in_manila.strftime('%I:%M %p').lstrip('0'),
            'check_out': check_out_manila.strftime('%I:%M %p').lstrip('0') if check_out_manila else '—',
            'duration':  duration_text,
        })

    days_elapsed    = today.day
    attendance_rate = int((len(present_days) / days_elapsed) * 100) if days_elapsed else 0

    # ── Body goals (most recent entry) ──
    goal = (
        BodyGoal.query
        .filter_by(member_id=user.id)
        .order_by(BodyGoal.recorded_at.desc())
        .first()
    )

    # ── Payment history (this member's own submissions) ──
    payment_rows = (
        Payment.query
        .filter_by(member_id=user.id)
        .order_by(Payment.paid_at.desc())
        .all()
    )
    payment_history = [{
        'date':      _to_manila(p.paid_at).strftime('%b %d, %Y'),
        'plan':      p.plan.name if p.plan else '—',
        'amount':    f'{float(p.amount):,.2f}',
        'method':    p.method,
        'reference': p.reference_number or '—',
        'status':    p.status,
    } for p in payment_rows]

    # ── Pending payment (drives the "Submit Payment" panel on the Payment tab) ──
    pending_payment_row = (
        Payment.query
        .filter_by(member_id=user.id, status='pending')
        .order_by(Payment.paid_at.desc())
        .first()
    )
    pending_payment = None
    if pending_payment_row is not None:
        pending_payment = {
            'plan_name':   pending_payment_row.plan.name if pending_payment_row.plan else '—',
            'amount':      f'{float(pending_payment_row.amount):,.2f}',
            'start_date':  pending_payment_row.requested_start_date.strftime('%b %d, %Y') if pending_payment_row.requested_start_date else '—',
            'needs_method': pending_payment_row.method.startswith('Pending'),
            'method':      pending_payment_row.method,
            'reference':   pending_payment_row.reference_number,
        }

    # ── Just-approved notice (shown once as a "Congratulations!" popup) ──
    just_approved_row = (
        Payment.query
        .filter_by(member_id=user.id, status='verified', notified=False)
        .order_by(Payment.verified_at.desc())
        .first()
    )
    plan_approved_notice = None
    if just_approved_row is not None:
        plan_approved_notice = {
            'plan_name': just_approved_row.plan.name if just_approved_row.plan else 'membership',
        }
        just_approved_row.notified = True
        db.session.commit()

    # Members without a paid, active membership only get Overview + My
    # Membership — everything else (attendance history, goals, services) is
    # locked behind an active plan.
    plan_active = bool(current_plan and current_plan['status'] == 'Active')

    return render_template(
        'member-dashboard.html',
        member=user,
        plan=current_plan,
        plan_active=plan_active,
        present_days=present_days,
        days_in_month=days_in_month,
        month_label=today.strftime('%B %Y'),
        session_history=session_history,
        attendance_rate=attendance_rate,
        goal=goal,
        payment_history=payment_history,
        pending_payment=pending_payment,
        plan_approved_notice=plan_approved_notice,
    )


def _get_attendance_calendar():
    """Gym-wide attendance for the current month (which days had at least one
    check-in). Used to drive the Admin dashboard's attendance grid."""
    today = _today_manila()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    month_start_dt, _ = _manila_day_bounds_utc(today.replace(day=1))

    attendance_rows = (
        Attendance.query
        .filter(Attendance.check_in >= month_start_dt)
        .all()
    )
    present_days = sorted({_to_manila(a.check_in).day for a in attendance_rows})

    return {
        'present_days': present_days,
        'days_in_month': days_in_month,
        'month_label': today.strftime('%B %Y'),
    }


def _get_attendance_today():
    """Return today's attendance rows (member name, check-in/out, duration, status),
    newest first. Shared by the Staff dashboard and Admin's Attendance tab so both
    show the exact same live data instead of drifting out of sync."""
    today = _today_manila()
    today_start, today_end = _manila_day_bounds_utc(today)

    rows = (
        Attendance.query
        .filter(Attendance.check_in >= today_start, Attendance.check_in <= today_end)
        .order_by(Attendance.check_in.desc())
        .all()
    )

    attendance_today = []
    for a in rows:
        duration_text = '—'
        if a.check_out:
            mins = a.duration_min if a.duration_min is not None else int((a.check_out - a.check_in).total_seconds() // 60)
            h, m = divmod(mins, 60)
            duration_text = f'{h}h {m}m' if h else f'{m}m'
        check_in_manila  = _to_manila(a.check_in)
        check_out_manila = _to_manila(a.check_out)
        attendance_today.append({
            'member_name': a.member.full_name,
            'check_in': check_in_manila.strftime('%I:%M %p').lstrip('0'),
            'check_out': check_out_manila.strftime('%I:%M %p').lstrip('0') if check_out_manila else '—',
            'duration': duration_text,
            'status': 'Out' if a.check_out else 'In',
        })
    return attendance_today


def _get_members_checkin_status():
    """Members who have a membership plan, with today's check-in status, times,
    and duration — powers the staff Check-in/Out table (one row per member)."""
    today = _today_manila()
    today_start, today_end = _manila_day_bounds_utc(today)

    members = [m for m in _get_members_with_plans() if m['plan'] != '—']

    result = []
    for m in members:
        entries = (
            Attendance.query
            .filter(
                Attendance.member_id == m['id'],
                Attendance.check_in >= today_start,
                Attendance.check_in <= today_end,
            )
            .order_by(Attendance.check_in.desc())
            .all()
        )
        latest     = entries[0] if entries else None
        open_entry = next((e for e in entries if e.check_out is None), None)

        if open_entry is not None:
            checkin_status = 'in'
        elif latest is not None:
            checkin_status = 'out'
        else:
            checkin_status = 'none'

        latest_check_in_manila  = _to_manila(latest.check_in) if latest else None
        latest_check_out_manila = _to_manila(latest.check_out) if latest and latest.check_out else None

        check_in_text  = latest_check_in_manila.strftime('%I:%M %p').lstrip('0') if latest_check_in_manila else '—'
        check_out_text = latest_check_out_manila.strftime('%I:%M %p').lstrip('0') if latest_check_out_manila else '—'

        duration_text = '—'
        if latest and latest.check_out:
            mins = latest.duration_min if latest.duration_min is not None else int((latest.check_out - latest.check_in).total_seconds() // 60)
            h, mnt = divmod(mins, 60)
            duration_text = f'{h}h {mnt}m' if h else f'{mnt}m'
        elif open_entry is not None:
            duration_text = 'Ongoing'

        # check_in is stored as a naive UTC datetime; append 'Z' so the browser
        # parses it as UTC and converts to the staff member's local clock.
        check_in_iso = (open_entry.check_in.isoformat() + 'Z') if open_entry is not None else None

        result.append({
            'id': m['id'],
            'name': m['name'],
            'email': m['email'],
            'plan': m['plan'],
            'plan_status': m['status'],
            'checkin_status': checkin_status,   # 'in' | 'out' | 'none'
            'check_in': check_in_text,
            'check_out': check_out_text,
            'duration': duration_text,
            'check_in_iso': check_in_iso,
        })
    return result


@app.route('/staff')
def staff():
    if 'role' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'staff':
        return redirect(url_for(session.get('role', 'login')))

    today = _today_manila()
    attendance_today = _get_attendance_today()

    members = _get_members_with_plans()
    active_members       = [m for m in members if m['status'] == 'Active']
    pending_status_members = [m for m in members if m['status'] == 'Pending']
    expiring_soon    = [
        m for m in members
        if m['status'] == 'Active' and m['expiry_date'] and 0 <= (m['expiry_date'] - today).days <= 7
    ]

    # ── Pending membership/payment requests (Request tab) ──
    pending_requests_rows = (
        Payment.query
        .filter_by(status='pending')
        .order_by(Payment.paid_at.desc())
        .all()
    )
    pending_requests = [{
        'id': p.id,
        'txn': f'TXN-{9000 + p.id}',
        'member_name': p.member.full_name,
        'plan': p.plan.name if p.plan else '—',
        'method': p.method,
        'reference': p.reference_number or '—',
        'amount': f'{float(p.amount):,.2f}',
        'proof_image_path': p.proof_image_path,
        'is_student': p.is_student,
        'student_id_image_path': p.student_id_image_path,
        'wants_coach': p.wants_coach,
        'coach_name': p.coach_name,
    } for p in pending_requests_rows]

    # ── Recently processed requests (approved/rejected), for reference ──
    processed_requests_rows = (
        Payment.query
        .filter(Payment.status.in_(['verified', 'rejected']))
        .order_by(Payment.paid_at.desc())
        .limit(20)
        .all()
    )
    processed_requests = [{
        'txn': f'TXN-{9000 + p.id}',
        'member_name': p.member.full_name,
        'plan': p.plan.name if p.plan else '—',
        'method': p.method,
        'amount': f'{float(p.amount):,.2f}',
        'date': p.paid_at.strftime('%b %d, %Y'),
        'status': p.status,
    } for p in processed_requests_rows]

    # ── Coach assignments (Coach tab) — every payment where a coach was
    #    requested, newest first ──
    coach_rows = (
        Payment.query
        .filter(Payment.wants_coach.is_(True))
        .order_by(Payment.paid_at.desc())
        .all()
    )
    coach_assignments = [{
        'member_name': p.member.full_name,
        'coach_name': p.coach_name or '—',
        'plan': p.plan.name if p.plan else '—',
        'status': p.status,
        'date': p.paid_at.strftime('%b %d, %Y'),
    } for p in coach_rows]

    stats = {
        'checkins_today':   len(attendance_today),
        'active_members':   len(active_members),
        'pending_payments': len(pending_requests),
        'expiring_soon':    len(expiring_soon),
    }

    return render_template(
        'staff-dashboard.html',
        attendance_today=attendance_today,
        members=members,
        members_checkin=_get_members_checkin_status(),
        expiring_soon=expiring_soon,
        stats=stats,
        pending_requests=pending_requests,
        processed_requests=processed_requests,
        coach_assignments=coach_assignments,
        current_user=User.query.get(session['user_id']),
    )


def _get_members_with_plans():
    """Return every member with their current plan/expiry/status, newest first."""
    rows = (
        db.session.query(User, Membership, MembershipPlan)
        .outerjoin(Membership, Membership.member_id == User.id)
        .outerjoin(MembershipPlan, MembershipPlan.id == Membership.plan_id)
        .filter(User.role == 'member')
        .order_by(User.id.desc())
        .all()
    )

    today = _today_manila()
    members = []
    for user, membership, plan in rows:
        expiry_date = None
        if membership is None or plan is None:
            plan_name, expiry_text, status_label = '—', '—', 'No Plan'
        else:
            plan_name   = plan.name
            expiry_date = membership.expiry_date
            expiry_text = expiry_date.strftime('%b %d, %Y') if expiry_date else '—'
            if expiry_date and expiry_date < today:
                status_label = 'Expired'
            elif membership.status == 'pending':
                status_label = 'Pending'
            else:
                status_label = 'Active'

        members.append({
            'id': user.id,
            'name': user.full_name,
            'first_name': user.first_name,
            'middle_initial': user.middle_initial or '',
            'last_name': user.last_name,
            'extension_name': user.extension_name or '',
            'email': user.email,
            'phone': user.phone or '',
            'plan': plan_name,
            'expiry': expiry_text,
            'expiry_date': expiry_date,
            'status': status_label,
        })
    return members


@app.route('/admin')
def admin():
    if 'role' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'admin':
        return redirect(url_for(session.get('role', 'login')))

    members = _get_members_with_plans()
    attendance_today = _get_attendance_today()
    attendance_calendar = _get_attendance_calendar()

    pending_payments_rows = (
        Payment.query
        .filter_by(status='pending')
        .order_by(Payment.paid_at.desc())
        .all()
    )
    pending_payments = [{
        'id': p.id,
        'txn': f'TXN-{9000 + p.id}',
        'member_name': p.member.full_name,
        'plan': p.plan.name if p.plan else '—',
        'method': p.method,
        'reference': p.reference_number or '—',
        'amount': f'{float(p.amount):,.2f}',
        'proof_image_path': p.proof_image_path,
        'is_student': p.is_student,
        'student_id_image_path': p.student_id_image_path,
        'wants_coach': p.wants_coach,
        'coach_name': p.coach_name,
    } for p in pending_payments_rows]

    payment_history_rows = (
        Payment.query
        .filter(Payment.status.in_(['verified', 'rejected']))
        .order_by(Payment.paid_at.desc())
        .limit(50)
        .all()
    )
    payment_history = [{
        'txn': f'TXN-{9000 + p.id}',
        'member_name': p.member.full_name,
        'plan': p.plan.name if p.plan else '—',
        'method': p.method,
        'amount': f'{float(p.amount):,.2f}',
        'date': p.paid_at.strftime('%b %d, %Y'),
        'status': p.status,
        'is_student': p.is_student,
        'wants_coach': p.wants_coach,
        'coach_name': p.coach_name,
    } for p in payment_history_rows]

    return render_template(
        'admin-dashboard.html',
        members=members,
        pending_payments=pending_payments,
        payment_history=payment_history,
        attendance_today=attendance_today,
        attendance_calendar=attendance_calendar,
        current_user=User.query.get(session['user_id']),
    )


# ── Seed ──────────────────────────────────────────────────────

def seed_default_users():
    defaults = [
        {'first_name': 'Administrator', 'last_name': 'User',   'email': 'admin@powergym.com', 'password': 'admin123',  'role': 'admin'},
        {'first_name': 'Staff',         'last_name': 'Member', 'email': 'staff@powergym.com', 'password': 'staff123',  'role': 'staff'},
        {'first_name': 'Maria',         'last_name': 'Santos', 'email': 'maria@email.com',    'password': 'member123', 'role': 'member'},
    ]
    for u in defaults:
        if User.query.filter_by(email=u['email']).first() is None:
            db.session.add(User(
                first_name=u['first_name'],
                last_name=u['last_name'],
                email=u['email'],
                password=generate_password_hash(u['password']),
                role=u['role'],
                status='active',
            ))
    db.session.commit()


def seed_default_plans():
    defaults = [
        {'name': 'Daily',   'duration_days': 1,   'price': 100.0},
        {'name': 'Weekly',  'duration_days': 14,  'price': 450.0},
        {'name': 'Monthly', 'duration_days': 30,  'price': 900.0},
        {'name': 'Yearly',  'duration_days': 365, 'price': 7000.0},
    ]
    for p in defaults:
        existing = MembershipPlan.query.filter_by(name=p['name']).first()
        if existing is None:
            db.session.add(MembershipPlan(
                name=p['name'],
                duration_days=p['duration_days'],
                price=p['price'],
            ))
        else:
            # Keep an already-seeded row in sync if the defaults above change
            # (e.g. Weekly's duration moving from 7 to 14 days).
            existing.duration_days = p['duration_days']
            existing.price         = p['price']
    db.session.commit()


def _run_startup_migrations():
    """db.create_all() only creates brand-new tables — it won't add columns
    to a table that already exists from a previous run. This adds any
    columns introduced after the database was first created, so existing
    installs don't need a manual ALTER TABLE."""
    migrations = [
        ('payments', 'requested_start_date', "ALTER TABLE payments ADD COLUMN requested_start_date DATE NULL"),
        ('payments', 'notified', "ALTER TABLE payments ADD COLUMN notified TINYINT(1) NOT NULL DEFAULT 0"),
    ]
    with db.engine.connect() as conn:
        for table, column, ddl in migrations:
            result = conn.execute(text(f"SHOW COLUMNS FROM {table} LIKE '{column}'"))
            if result.fetchone() is None:
                conn.execute(text(ddl))
                conn.commit()
                print(f"Migration: added {table}.{column}")


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _run_startup_migrations()
        seed_default_plans()
        seed_default_users()
        print("Tables created, plans and demo users seeded!")
    app.run(debug=True)