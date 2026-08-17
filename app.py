import os
import secrets
import string
import calendar
import csv
import io
from collections import OrderedDict
from dotenv import load_dotenv
load_dotenv()  # Reads variables from a .env file in the project root, if present

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.orm import joinedload
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timezone, date, timedelta


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_secret_key')

# ── Static asset caching ─────────────────────────────────────
# By default Flask re-validates every CSS/JS/image request with the browser
# on every page load. Since these files (tr-styles.css, tr-*.js, logo, etc.)
# rarely change, telling the browser to cache them for a week means repeat
# visits to any dashboard skip re-downloading them entirely — a big part of
# what makes navigation feel slow on a fresh load. Set SEND_FILE_MAX_AGE=0
# via env var during active front-end development if you need changes to
# show up immediately without a hard refresh.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = int(os.environ.get('SEND_FILE_MAX_AGE', 604800))  # 7 days

# Auto cache-busting: append the static file's own last-modified time as a
# ?v= query string to every url_for('static', ...) call, across every
# template, automatically. Combined with the week-long cache above, this
# means updated CSS/JS/images show up immediately for everyone on their very
# next page load — no hard refresh needed — while files that haven't
# changed still get served straight from the browser's cache. Without this,
# a 7-day cache means anyone who visited before an update could keep seeing
# the old file until that cache naturally expires.
@app.url_defaults
def _add_static_file_version(endpoint, values):
    if endpoint == 'static' and 'filename' in values:
        filepath = os.path.join(app.static_folder or '', values['filename'])
        try:
            values['v'] = int(os.stat(filepath).st_mtime)
        except OSError:
            pass

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


# Discounted prices for students with a verified school ID. Daily is not
# discounted (it's not listed in the promo), so it's left out on purpose —
# any plan not in this table just falls back to its normal price.
STUDENT_PLAN_PRICES = {
    'Weekly':  400.0,
    'Monthly': 800.0,
    'Yearly':  6000.0,
}


def _plan_amount(plan, is_student):
    """The amount to actually charge for a plan, applying the student
    discount when applicable. Falls back to the plan's normal price for
    plans with no listed student rate (e.g. Daily) or for non-students."""
    if plan and is_student and plan.name in STUDENT_PLAN_PRICES:
        return STUDENT_PLAN_PRICES[plan.name]
    return plan.price if plan else 0.0


def _coach_fee(coach_name):
    """The coach fee to add on top of the plan price, looked up by name.
    Staff/admin set this per-coach from their dashboards. Returns 0 if no
    coach was requested or the named coach no longer exists."""
    if not coach_name:
        return 0.0
    coach = Coach.query.filter_by(name=coach_name).first()
    return float(coach.fee) if coach else 0.0


def _payment_total(plan, is_student, coach_name=None):
    """Full amount a member owes: the (student-adjusted) plan price plus
    the selected coach's fee, if any. This is the single source of truth
    for what gets charged/displayed everywhere a plan + coach combination
    is priced."""
    return _plan_amount(plan, is_student) + _coach_fee(coach_name)


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


def _get_member_attendance_month(user_id, year, month):
    """Attendance calendar grid + session history for one member, for an
    arbitrary (year, month) — powers both the initial 'My Attendance' page
    load and the back/forward month navigation (see /member/attendance-month).
    If the requested month is the current one, today_day is set so days that
    haven't happened yet render as 'upcoming' rather than 'absent'."""
    today = _today_manila()
    days_in_month = calendar.monthrange(year, month)[1]

    month_start_dt, _ = _manila_day_bounds_utc(date(year, month, 1))
    next_month  = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    month_end_dt, _ = _manila_day_bounds_utc(next_month)

    attendance_rows = (
        Attendance.query
        .filter(Attendance.member_id == user_id,
                Attendance.check_in >= month_start_dt,
                Attendance.check_in < month_end_dt)
        .order_by(Attendance.check_in.desc())
        .all()
    )

    present_days = sorted({_to_manila(a.check_in).day for a in attendance_rows})

    # ── Days the member had no active membership plan at all — these should
    #    stay neutral on the calendar (not red) since there was nothing to
    #    check in for. A day counts as "plan-covered" only if it falls within
    #    the member's current membership start_date..expiry_date range. ──
    membership = Membership.query.filter_by(member_id=user_id).first()
    no_plan_days = []
    for d in range(1, days_in_month + 1):
        day_date = date(year, month, d)
        has_plan = (
            membership is not None
            and membership.start_date is not None
            and membership.expiry_date is not None
            and membership.start_date <= day_date <= membership.expiry_date
        )
        if not has_plan:
            no_plan_days.append(d)

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

    is_current_month = (year == today.year and month == today.month)

    return {
        'year': year,
        'month': month,
        'month_label': date(year, month, 1).strftime('%B %Y'),
        'days_in_month': days_in_month,
        'today_day': today.day if is_current_month else None,
        'is_current_month': is_current_month,
        'present_days': present_days,
        'no_plan_days': no_plan_days,
        'session_history': session_history,
    }


@app.route('/member/attendance-month')
def member_attendance_month():
    """AJAX endpoint behind the back/forward arrows on 'My Attendance' —
    returns the calendar + session history for whichever month was requested,
    without a full page reload."""
    if session.get('role') != 'member':
        return jsonify(success=False, error='Unauthorized.'), 403
    user_id = session.get('user_id')

    try:
        year  = int(request.args.get('year'))
        month = int(request.args.get('month'))
    except (TypeError, ValueError):
        return jsonify(success=False, error='Invalid month.'), 400
    if month < 1 or month > 12:
        return jsonify(success=False, error='Invalid month.'), 400

    today = _today_manila()
    if (year, month) > (today.year, today.month):
        return jsonify(success=False, error='Cannot view a future month.'), 400
    if year < 2020:
        return jsonify(success=False, error='Invalid month.'), 400

    return jsonify(success=True, **_get_member_attendance_month(user_id, year, month))


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

# ── Gym content (plans / services / equipment) picture uploads ─
CONTENT_UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads', 'content')
CONTENT_ALLOWED_EXT   = {'png', 'jpg', 'jpeg', 'webp'}
CONTENT_MAX_BYTES     = 8 * 1024 * 1024  # 8MB
os.makedirs(CONTENT_UPLOAD_FOLDER, exist_ok=True)

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
    # Tracks the last time this user's dashboard checked in on announcements,
    # so we know which ones are "new" for them since their last visit.
    last_seen_announcements_at = db.Column(db.DateTime, nullable=True)

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
    # ── Public-facing content (editable by staff/admin from the dashboard,
    #    displayed on the home page pricing cards) ──
    description   = db.Column(db.Text, nullable=True)
    image_path    = db.Column(db.String(255), nullable=True)
    inclusions    = db.Column(db.Text, nullable=True)   # one inclusion per line
    sort_order    = db.Column(db.Integer, nullable=False, default=0)

    memberships   = db.relationship('Membership', back_populates='plan')
    payments      = db.relationship('Payment', back_populates='plan')

    @property
    def inclusions_list(self):
        if not self.inclusions:
            return []
        return [line.strip() for line in self.inclusions.splitlines() if line.strip()]

    def __repr__(self):
        return f"<MembershipPlan {self.name} ₱{self.price}>"


class Membership(db.Model):
    __tablename__ = 'memberships'
    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    member_id   = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                            nullable=False, unique=True, index=True)
    plan_id     = db.Column(db.Integer, db.ForeignKey('membership_plans.id', ondelete='SET NULL'), nullable=True, index=True)
    start_date  = db.Column(db.Date, nullable=False)
    expiry_date = db.Column(db.Date, nullable=False)
    status      = db.Column(db.String(10), nullable=False, default='pending')
    created_at  = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at  = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    member = db.relationship('User', back_populates='membership')
    plan   = db.relationship('MembershipPlan', back_populates='memberships')


class Coach(db.Model):
    """A personal coach members can request. Availability (days) and
    capacity (max members) are editable by staff from the Coach tab."""
    __tablename__  = 'coaches'
    id             = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name           = db.Column(db.String(60), nullable=False, unique=True)
    available_days = db.Column(db.String(40), nullable=False, default='')  # e.g. "Mon,Wed,Fri"
    max_members    = db.Column(db.Integer, nullable=False, default=10)
    fee            = db.Column(db.Numeric(10, 2), nullable=False, default=0)  # added on top of the plan price when a member picks this coach
    is_active      = db.Column(db.Boolean, nullable=False, default=True)
    updated_at     = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                               onupdate=lambda: datetime.now(timezone.utc))

    @property
    def available_days_list(self):
        if not self.available_days:
            return []
        return [d.strip() for d in self.available_days.split(',') if d.strip()]

    def __repr__(self):
        return f"<Coach {self.name}>"


class Payment(db.Model):
    __tablename__    = 'payments'
    id               = db.Column(db.Integer, primary_key=True, autoincrement=True)
    member_id        = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    plan_id          = db.Column(db.Integer, db.ForeignKey('membership_plans.id'), nullable=True, index=True)
    amount           = db.Column(db.Numeric(10, 2), nullable=False)
    method           = db.Column(db.String(32), nullable=False)
    reference_number = db.Column(db.String(60), nullable=True)
    proof_image_path = db.Column(db.String(255), nullable=True)
    is_student            = db.Column(db.Boolean, nullable=False, default=False)
    student_id_image_path = db.Column(db.String(255), nullable=True)
    wants_coach           = db.Column(db.Boolean, nullable=False, default=False)
    coach_name             = db.Column(db.String(60), nullable=True)
    requested_start_date  = db.Column(db.Date, nullable=True)
    status           = db.Column(db.String(10), nullable=False, default='pending', index=True)
    recorded_by_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    notes            = db.Column(db.Text, nullable=True)
    paid_at          = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    verified_at      = db.Column(db.DateTime, nullable=True)
    notified         = db.Column(db.Boolean, nullable=False, default=False)
    staff_viewed     = db.Column(db.Boolean, nullable=False, default=False)

    member      = db.relationship('User', foreign_keys=[member_id], back_populates='payments')
    plan        = db.relationship('MembershipPlan', back_populates='payments')
    recorded_by = db.relationship('User', foreign_keys=[recorded_by_id], back_populates='recorded_payments')


class Attendance(db.Model):
    __tablename__ = 'attendance'
    id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    member_id    = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    check_in     = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
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


# Many-to-many join table linking a Service to the Equipment/Machines used
# for it, so the member dashboard can show "Equipment Used" per service.
# A brand-new table like this is created automatically by db.create_all()
# on next startup — no ALTER TABLE / manual migration needed.
service_equipment = db.Table(
    'service_equipment',
    db.Column('service_id',   db.Integer, db.ForeignKey('gym_services.id',  ondelete='CASCADE'), primary_key=True),
    db.Column('equipment_id', db.Integer, db.ForeignKey('gym_equipment.id', ondelete='CASCADE'), primary_key=True),
)


class GymService(db.Model):
    """A service offered at the gym (e.g. 'Personal Coaching', 'Locker
    Rental') — editable by staff/admin and shown on the public home page."""
    __tablename__ = 'gym_services'
    id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name         = db.Column(db.String(80), nullable=False)
    description  = db.Column(db.Text, nullable=True)
    image_path   = db.Column(db.String(255), nullable=True)
    category     = db.Column(db.String(60), nullable=True)   # e.g. "Boxing", "Coaching"
    icon         = db.Column(db.String(8), nullable=True)    # single emoji shown on chips/cards
    is_active    = db.Column(db.Boolean, nullable=False, default=True)
    sort_order   = db.Column(db.Integer, nullable=False, default=0)
    created_at   = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at   = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                             onupdate=lambda: datetime.now(timezone.utc))
    equipment    = db.relationship('GymEquipment', secondary=service_equipment,
                                    order_by='GymEquipment.sort_order, GymEquipment.id')

    def __repr__(self):
        return f"<GymService {self.name}>"


class GymEquipment(db.Model):
    """A piece of equipment / machine / training area — editable by
    staff/admin and shown on the public home page."""
    __tablename__ = 'gym_equipment'
    id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name         = db.Column(db.String(80), nullable=False)
    description  = db.Column(db.Text, nullable=True)
    image_path   = db.Column(db.String(255), nullable=True)
    category     = db.Column(db.String(60), nullable=True)   # e.g. "Boxing", "Strengthening"
    icon         = db.Column(db.String(8), nullable=True)    # single emoji shown on chips/cards
    is_active    = db.Column(db.Boolean, nullable=False, default=True)
    sort_order   = db.Column(db.Integer, nullable=False, default=0)
    # True for the broad facility-zone photos (Weight Area, Cardio Area,
    # Reception, etc.) used on the home page's "Our Facilities" section —
    # they're not real individual machines, so they're hidden from the
    # member dashboard's "Gym Machines and Equipment" list and from the
    # equipment picker on the Services form.
    is_facility  = db.Column(db.Boolean, nullable=False, default=False)
    created_at   = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at   = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                             onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<GymEquipment {self.name}>"


class GymSettings(db.Model):
    """Single-row table holding gym-wide settings editable by Admin —
    currently just the GCash account members send payments to. Always
    accessed through _get_gym_settings(), which gets/creates row id=1."""
    __tablename__ = 'gym_settings'
    id                 = db.Column(db.Integer, primary_key=True, autoincrement=True)
    gcash_number       = db.Column(db.String(20),  nullable=True)
    gcash_account_name = db.Column(db.String(120), nullable=True)
    updated_at         = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                                    onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<GymSettings gcash_number={self.gcash_number}>"


def _get_gym_settings():
    """Fetch the singleton settings row, creating it with sensible
    defaults on first use so callers never have to null-check."""
    settings = GymSettings.query.get(1)
    if settings is None:
        settings = GymSettings(id=1, gcash_number='0945 397 0594', gcash_account_name='LYDIA M. EMATA')
        db.session.add(settings)
        db.session.commit()
    return settings


# ── Content-management (plans / services / equipment) helpers ──────────
def _content_role_ok():
    return session.get('role') in ('staff', 'admin')


def _save_content_image(file_storage, existing_path=None):
    """Save an uploaded content image to CONTENT_UPLOAD_FOLDER and return the
    web-relative path to store on the model. Returns existing_path unchanged
    if no new file was uploaded. Raises ValueError on invalid file."""
    if not file_storage or not file_storage.filename:
        return existing_path
    ext = file_storage.filename.rsplit('.', 1)[-1].lower() if '.' in file_storage.filename else ''
    if ext not in CONTENT_ALLOWED_EXT:
        raise ValueError('Image must be a PNG, JPG, JPEG, or WEBP file.')
    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)
    if size > CONTENT_MAX_BYTES:
        raise ValueError('Image must be smaller than 8MB.')
    safe_name = secure_filename(f"{secrets.token_hex(8)}.{ext}")
    file_storage.save(os.path.join(CONTENT_UPLOAD_FOLDER, safe_name))
    return f'uploads/content/{safe_name}'


def _delete_content_image(image_path):
    """Best-effort removal of a previously-uploaded content image from disk."""
    if not image_path:
        return
    full_path = os.path.join(app.root_path, 'static', image_path)
    try:
        if os.path.isfile(full_path):
            os.remove(full_path)
    except OSError:
        pass


def _plan_to_dict(p):
    return {
        'id': p.id, 'name': p.name, 'duration_days': p.duration_days,
        'price': p.price, 'is_active': p.is_active,
        'description': p.description or '', 'image_path': p.image_path or '',
        'inclusions': p.inclusions or '', 'sort_order': p.sort_order,
    }


DEFAULT_SERVICE_ICON   = '🛎️'
DEFAULT_EQUIPMENT_ICON = '🏋️'
DEFAULT_CATEGORY       = 'General'


def _service_to_dict(s):
    return {
        'id': s.id, 'name': s.name, 'description': s.description or '',
        'image_path': s.image_path or '', 'is_active': s.is_active,
        'category': s.category or DEFAULT_CATEGORY,
        'icon': s.icon or DEFAULT_SERVICE_ICON,
        'sort_order': s.sort_order,
        # Ids only here (admin form pre-checks these boxes); the member
        # dashboard gets fuller name/icon objects via services_data below.
        'equipment_ids': [e.id for e in s.equipment],
    }


def _equipment_to_dict(e):
    return {
        'id': e.id, 'name': e.name, 'description': e.description or '',
        'image_path': e.image_path or '', 'is_active': e.is_active,
        'category': e.category or DEFAULT_CATEGORY,
        'icon': e.icon or DEFAULT_EQUIPMENT_ICON,
        'sort_order': e.sort_order,
        'is_facility': e.is_facility,
    }


CATEGORY_ICONS = {
    'boxing': '🥊', 'strengthening': '💪', 'cardio zone': '🏃', 'weight loss': '🔥',
    'functional training': '🤸', 'coaching': '🧑‍🏫', 'membership perks': '🎁',
    'facilities': '🏢', 'classes': '📅', 'general': '🛎️',
}


def _group_content_by_category(items, default_icon='🛎️'):
    """Group a list of GymService/GymEquipment rows into an ordered list of
    (category_name, category_icon, [items]) tuples, preserving each item's
    sort_order and putting categories in first-seen order. Items with no
    category fall into a trailing "General" group. The category header
    icon comes from a small known-category lookup (falling back to the
    first item's own icon, then a generic default) so it reads distinctly
    from each item's individual icon."""
    groups = OrderedDict()
    for item in items:
        cat = (item.category or DEFAULT_CATEGORY).strip() or DEFAULT_CATEGORY
        groups.setdefault(cat, []).append(item)
    result = []
    for cat, cat_items in groups.items():
        cat_icon = CATEGORY_ICONS.get(cat.lower()) or (cat_items[0].icon if cat_items[0].icon else default_icon)
        result.append((cat, cat_icon, cat_items))
    return result


# ── Content-management API: Category picker ─────────────────────────────
@app.route('/api/content/categories', methods=['GET'])
def api_list_categories():
    """Categories currently in use, so the staff/admin form can offer real,
    already-typed values instead of a fixed hardcoded list — keeping a
    service's category (e.g. "Boxing") spelled exactly the same as the
    equipment tagged under it, which is what makes them group together
    correctly on the member dashboard.

    Accepts an optional ?type= filter: 'services', 'machines', or
    'facilities'. Facilities and Machines both live in the GymEquipment
    table (split by is_facility), so without this split a facility-only
    category (e.g. "Cardio Zone" on the Cardio Area facility) would leak
    into the Machines form, and vice versa. With no type given, everything
    is merged (legacy/back-compat behavior)."""
    if not _content_role_ok():
        return jsonify(success=False, error='Unauthorized.'), 403
    content_type = (request.args.get('type') or '').strip().lower()
    used = set()
    if content_type in ('', 'services'):
        for row in GymService.query.with_entities(GymService.category).distinct():
            if row[0] and row[0].strip():
                used.add(row[0].strip())
    if content_type in ('', 'machines'):
        for row in (GymEquipment.query.filter_by(is_facility=False)
                    .with_entities(GymEquipment.category).distinct()):
            if row[0] and row[0].strip():
                used.add(row[0].strip())
    if content_type in ('', 'facilities'):
        for row in (GymEquipment.query.filter_by(is_facility=True)
                    .with_entities(GymEquipment.category).distinct()):
            if row[0] and row[0].strip():
                used.add(row[0].strip())
    return jsonify(success=True, categories=sorted(used, key=str.lower))


# ── Content-management API: Membership Plans ────────────────────────────
@app.route('/api/content/plans', methods=['GET'])
def api_list_plans():
    if not _content_role_ok():
        return jsonify(success=False, error='Unauthorized.'), 403
    plans = MembershipPlan.query.order_by(MembershipPlan.sort_order, MembershipPlan.id).all()
    return jsonify(success=True, items=[_plan_to_dict(p) for p in plans])


@app.route('/api/content/plans/save', methods=['POST'])
def api_save_plan():
    if not _content_role_ok():
        return jsonify(success=False, error='Unauthorized.'), 403

    plan_id = request.form.get('id', '').strip()
    name          = (request.form.get('name') or '').strip()
    duration_days = request.form.get('duration_days', '').strip()
    price         = request.form.get('price', '').strip()
    description   = (request.form.get('description') or '').strip()
    inclusions    = (request.form.get('inclusions') or '').strip()
    sort_order    = request.form.get('sort_order', '0').strip()
    is_active     = request.form.get('is_active', 'true').strip().lower() != 'false'
    remove_image  = request.form.get('remove_image', 'false').strip().lower() == 'true'

    if not name:
        return jsonify(success=False, error='Plan name is required.'), 400
    try:
        duration_days = int(duration_days)
        price = float(price)
        sort_order = int(sort_order or 0)
        if duration_days <= 0 or price < 0:
            raise ValueError()
    except ValueError:
        return jsonify(success=False, error='Duration and price must be valid positive numbers.'), 400

    if plan_id:
        plan = MembershipPlan.query.get(plan_id)
        if not plan:
            return jsonify(success=False, error='Plan not found.'), 404
        dupe = MembershipPlan.query.filter(MembershipPlan.name == name, MembershipPlan.id != plan.id).first()
    else:
        plan = MembershipPlan()
        dupe = MembershipPlan.query.filter_by(name=name).first()

    if dupe:
        return jsonify(success=False, error='A plan with that name already exists.'), 400

    try:
        image_path = plan.image_path if plan_id else None
        if remove_image:
            _delete_content_image(image_path)
            image_path = None
        else:
            new_path = _save_content_image(request.files.get('image'), image_path)
            if new_path != image_path:
                _delete_content_image(image_path)
            image_path = new_path
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400

    plan.name          = name
    plan.duration_days = duration_days
    plan.price         = price
    plan.description   = description or None
    plan.inclusions    = inclusions or None
    plan.image_path    = image_path
    plan.sort_order    = sort_order
    plan.is_active      = is_active

    if not plan_id:
        db.session.add(plan)
    db.session.commit()
    return jsonify(success=True, message='Plan saved.', item=_plan_to_dict(plan))


@app.route('/api/content/plans/<int:plan_id>/delete', methods=['POST'])
def api_delete_plan(plan_id):
    if not _content_role_ok():
        return jsonify(success=False, error='Unauthorized.'), 403
    plan = MembershipPlan.query.get(plan_id)
    if not plan:
        return jsonify(success=False, error='Plan not found.'), 404
    if Membership.query.filter_by(plan_id=plan.id).first():
        # Don't hard-delete a plan members are actively on — deactivate instead.
        plan.is_active = False
        db.session.commit()
        return jsonify(success=True, message='Plan is in use by members, so it was deactivated instead of deleted.', deactivated=True)
    _delete_content_image(plan.image_path)
    db.session.delete(plan)
    db.session.commit()
    return jsonify(success=True, message='Plan deleted.')


# ── Content-management API: Services ─────────────────────────────────────
@app.route('/api/content/services', methods=['GET'])
def api_list_services():
    if not _content_role_ok():
        return jsonify(success=False, error='Unauthorized.'), 403
    items = GymService.query.order_by(GymService.sort_order, GymService.id).all()
    return jsonify(success=True, items=[_service_to_dict(s) for s in items])


@app.route('/api/content/services/save', methods=['POST'])
def api_save_service():
    if not _content_role_ok():
        return jsonify(success=False, error='Unauthorized.'), 403

    item_id      = request.form.get('id', '').strip()
    name         = (request.form.get('name') or '').strip()
    description  = (request.form.get('description') or '').strip()
    category     = (request.form.get('category') or '').strip()[:60]
    icon         = (request.form.get('icon') or '').strip()[:8]
    sort_order   = request.form.get('sort_order', '0').strip()
    is_active    = request.form.get('is_active', 'true').strip().lower() != 'false'
    remove_image = request.form.get('remove_image', 'false').strip().lower() == 'true'

    if not name:
        return jsonify(success=False, error='Service name is required.'), 400
    try:
        sort_order = int(sort_order or 0)
    except ValueError:
        sort_order = 0

    if item_id:
        item = GymService.query.get(item_id)
        if not item:
            return jsonify(success=False, error='Service not found.'), 404
    else:
        item = GymService()

    try:
        image_path = item.image_path if item_id else None
        if remove_image:
            _delete_content_image(image_path)
            image_path = None
        else:
            new_path = _save_content_image(request.files.get('image'), image_path)
            if new_path != image_path:
                _delete_content_image(image_path)
            image_path = new_path
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400

    item.name        = name
    item.description = description or None
    item.image_path  = image_path
    item.category    = category or None
    item.icon        = icon or None
    item.sort_order  = sort_order
    item.is_active   = is_active

    # Equipment/machines checked in the form (sent as repeated
    # "equipment_ids" fields). Replacing the whole list on every save keeps
    # this in sync even when boxes are unchecked.
    equipment_ids = [i for i in request.form.getlist('equipment_ids') if i.strip()]
    if equipment_ids:
        item.equipment = GymEquipment.query.filter(GymEquipment.id.in_(equipment_ids)).all()
    else:
        item.equipment = []

    if not item_id:
        db.session.add(item)
    db.session.commit()
    return jsonify(success=True, message='Service saved.', item=_service_to_dict(item))


@app.route('/api/content/services/<int:item_id>/delete', methods=['POST'])
def api_delete_service(item_id):
    if not _content_role_ok():
        return jsonify(success=False, error='Unauthorized.'), 403
    item = GymService.query.get(item_id)
    if not item:
        return jsonify(success=False, error='Service not found.'), 404
    _delete_content_image(item.image_path)
    db.session.delete(item)
    db.session.commit()
    return jsonify(success=True, message='Service deleted.')


# ── Content-management API: Equipment / Machines ─────────────────────────
@app.route('/api/content/equipment', methods=['GET'])
def api_list_equipment():
    if not _content_role_ok():
        return jsonify(success=False, error='Unauthorized.'), 403
    items = GymEquipment.query.order_by(GymEquipment.sort_order, GymEquipment.id).all()
    return jsonify(success=True, items=[_equipment_to_dict(e) for e in items])


@app.route('/api/content/equipment/save', methods=['POST'])
def api_save_equipment():
    if not _content_role_ok():
        return jsonify(success=False, error='Unauthorized.'), 403

    item_id      = request.form.get('id', '').strip()
    name         = (request.form.get('name') or '').strip()
    description  = (request.form.get('description') or '').strip()
    category     = (request.form.get('category') or '').strip()[:60]
    icon         = (request.form.get('icon') or '').strip()[:8]
    sort_order   = request.form.get('sort_order', '0').strip()
    is_active    = request.form.get('is_active', 'true').strip().lower() != 'false'
    is_facility  = request.form.get('is_facility', 'false').strip().lower() == 'true'
    remove_image = request.form.get('remove_image', 'false').strip().lower() == 'true'

    if not name:
        return jsonify(success=False, error='Equipment name is required.'), 400
    try:
        sort_order = int(sort_order or 0)
    except ValueError:
        sort_order = 0

    if item_id:
        item = GymEquipment.query.get(item_id)
        if not item:
            return jsonify(success=False, error='Equipment not found.'), 404
    else:
        item = GymEquipment()

    try:
        image_path = item.image_path if item_id else None
        if remove_image:
            _delete_content_image(image_path)
            image_path = None
        else:
            new_path = _save_content_image(request.files.get('image'), image_path)
            if new_path != image_path:
                _delete_content_image(image_path)
            image_path = new_path
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400

    item.name        = name
    item.description = description or None
    item.image_path  = image_path
    item.category    = category or None
    item.icon        = icon or None
    item.sort_order  = sort_order
    item.is_active   = is_active
    item.is_facility = is_facility

    if not item_id:
        db.session.add(item)
    db.session.commit()
    return jsonify(success=True, message='Equipment saved.', item=_equipment_to_dict(item))


@app.route('/api/content/equipment/<int:item_id>/delete', methods=['POST'])
def api_delete_equipment(item_id):
    if not _content_role_ok():
        return jsonify(success=False, error='Unauthorized.'), 403
    item = GymEquipment.query.get(item_id)
    if not item:
        return jsonify(success=False, error='Equipment not found.'), 404
    _delete_content_image(item.image_path)
    db.session.delete(item)
    db.session.commit()
    return jsonify(success=True, message='Equipment deleted.')


# ── Content-management API: Announcements ────────────────────────────────
def _announcement_to_dict(a):
    return {
        'id':         a.id,
        'title':      a.title,
        'body':       a.body,
        'target':     a.target,
        'is_active':  a.is_active,
        'posted_by':  a.posted_by.full_name if a.posted_by else 'Admin',
        'created_at': _to_manila(a.created_at).strftime('%b %d, %Y') if a.created_at else '',
    }


@app.route('/api/announcements', methods=['GET'])
def api_list_announcements():
    if not _content_role_ok():
        return jsonify(success=False, error='Unauthorized.'), 403
    items = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return jsonify(success=True, items=[_announcement_to_dict(a) for a in items])


@app.route('/api/announcements/save', methods=['POST'])
def api_save_announcement():
    if session.get('role') != 'admin':
        return jsonify(success=False, error='Unauthorized.'), 403

    title  = (request.form.get('title') or '').strip()
    body   = (request.form.get('body') or '').strip()
    target = (request.form.get('target') or 'all').strip()
    if target not in ('all', 'active', 'expiring', 'staff'):
        target = 'all'

    if not title:
        return jsonify(success=False, error='Title is required.'), 400
    if not body:
        return jsonify(success=False, error='Message is required.'), 400

    item = Announcement(
        title=title,
        body=body,
        target=target,
        posted_by_id=session.get('user_id'),
        is_active=True,
    )
    db.session.add(item)
    db.session.commit()
    return jsonify(success=True, message='Announcement published.', item=_announcement_to_dict(item))


@app.route('/api/announcements/<int:item_id>/edit', methods=['POST'])
def api_edit_announcement(item_id):
    if session.get('role') != 'admin':
        return jsonify(success=False, error='Unauthorized.'), 403
    item = Announcement.query.get(item_id)
    if not item:
        return jsonify(success=False, error='Announcement not found.'), 404

    title  = (request.form.get('title') or '').strip()
    body   = (request.form.get('body') or '').strip()
    target = (request.form.get('target') or 'all').strip()
    if target not in ('all', 'active', 'expiring', 'staff'):
        target = 'all'

    if not title:
        return jsonify(success=False, error='Title is required.'), 400
    if not body:
        return jsonify(success=False, error='Message is required.'), 400

    item.title  = title
    item.body   = body
    item.target = target
    db.session.commit()
    return jsonify(success=True, message='Announcement updated.', item=_announcement_to_dict(item))


@app.route('/api/announcements/<int:item_id>/toggle', methods=['POST'])
def api_toggle_announcement(item_id):
    if session.get('role') != 'admin':
        return jsonify(success=False, error='Unauthorized.'), 403
    item = Announcement.query.get(item_id)
    if not item:
        return jsonify(success=False, error='Announcement not found.'), 404
    item.is_active = not item.is_active
    db.session.commit()
    return jsonify(success=True, message='Announcement updated.', item=_announcement_to_dict(item))


@app.route('/api/announcements/<int:item_id>/delete', methods=['POST'])
def api_delete_announcement(item_id):
    if session.get('role') != 'admin':
        return jsonify(success=False, error='Unauthorized.'), 403
    item = Announcement.query.get(item_id)
    if not item:
        return jsonify(success=False, error='Announcement not found.'), 404
    db.session.delete(item)
    db.session.commit()
    return jsonify(success=True, message='Announcement deleted.')


# ── Routes ────────────────────────────────────────────────────
@app.route('/')
@app.route('/home')
def home():
    plans     = MembershipPlan.query.filter_by(is_active=True).order_by(MembershipPlan.sort_order, MembershipPlan.id).all()
    services  = GymService.query.filter_by(is_active=True).order_by(GymService.sort_order, GymService.id).all()
    # Public landing page only shows facility-zone photos (Weight Area,
    # Cardio Area, etc.) — real machines/equipment are member-only and
    # live on the member dashboard's "Gym Machines and Equipment" list.
    equipment = (GymEquipment.query
                 .filter_by(is_active=True, is_facility=True)
                 .order_by(GymEquipment.sort_order, GymEquipment.id).all())
    return render_template('home.html', plans=plans, services=services, equipment=equipment)

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


def _otp_email_html(first_name, otp):
    """Styled HTML version of the password-reset OTP email (POWER GYM branded)."""
    digits_html = ''.join(
        f'<td style="padding:0 6px;"><div style="width:44px;height:52px;background:#f2f3f6;'
        f'border:1px solid #dcdfe6;border-radius:8px;color:#141820;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:26px;font-weight:700;line-height:52px;text-align:center;">{d}</div></td>'
        for d in otp
    )
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#c6c9d1;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#c6c9d1;padding:40px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="420" cellpadding="0" cellspacing="0"
               style="max-width:420px;width:100%;background:#ffffff;border:1px solid #e2e4ea;
                      border-radius:14px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;
                      box-shadow:0 4px 18px rgba(0,0,0,0.06);">
          <tr>
            <td style="background:linear-gradient(135deg,#e61e25,#b8141c);padding:22px 24px;text-align:center;">
              <div style="color:#ffffff;font-size:20px;font-weight:800;letter-spacing:1px;">POWER GYM</div>
              <div style="color:rgba(255,255,255,0.85);font-size:11px;letter-spacing:2px;margin-top:2px;">ACCOUNT RECOVERY</div>
            </td>
          </tr>
          <tr>
            <td style="padding:32px 28px 8px 28px;text-align:center;">
              <div style="width:64px;height:64px;margin:0 auto 18px auto;background:rgba(255,171,64,0.14);
                          border-radius:50%;line-height:64px;font-size:28px;">✉️</div>
              <div style="color:#141820;font-size:18px;font-weight:700;margin-bottom:6px;">Your Verification Code</div>
              <div style="color:#6b7280;font-size:13px;line-height:1.6;margin-bottom:24px;">
                Hi {first_name}, use the code below to reset your<br>POWER GYM account password.
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:0 28px;text-align:center;">
              <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto;">
                <tr>{digits_html}</tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:22px 28px 6px 28px;text-align:center;">
              <div style="color:#6b7280;font-size:12px;">
                This code expires in <strong style="color:#141820;">{OTP_VALID_MINUTES} minutes</strong>
                and can be entered up to <strong style="color:#141820;">{OTP_MAX_ATTEMPTS} times</strong>.
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 28px 28px 28px;text-align:center;">
              <div style="height:1px;background:#e2e4ea;margin-bottom:16px;"></div>
              <div style="color:#9aa0b0;font-size:11px;line-height:1.6;">
                If you didn't request this code, you can safely ignore this email —
                your password will not be changed.
              </div>
            </td>
          </tr>
        </table>
        <div style="color:#9aa0b0;font-size:11px;margin-top:18px;font-family:Arial,Helvetica,sans-serif;">
          © Power Gym. This is an automated message, please do not reply.
        </div>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _membership_activated_email_html(first_name, plan_name, start_date):
    """Styled HTML congratulations email sent once a payment is approved and
    the membership is actually activated (POWER GYM branded, mirrors the
    OTP email's look)."""
    start_label = start_date.strftime('%B %d, %Y')
    plan_line = f' on the <strong style="color:#141820;">{plan_name}</strong> plan' if plan_name else ''
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#c6c9d1;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#c6c9d1;padding:40px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="420" cellpadding="0" cellspacing="0"
               style="max-width:420px;width:100%;background:#ffffff;border:1px solid #e2e4ea;
                      border-radius:14px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;
                      box-shadow:0 4px 18px rgba(0,0,0,0.06);">
          <tr>
            <td style="background:linear-gradient(135deg,#e61e25,#b8141c);padding:22px 24px;text-align:center;">
              <div style="color:#ffffff;font-size:20px;font-weight:800;letter-spacing:1px;">POWER GYM</div>
              <div style="color:rgba(255,255,255,0.85);font-size:11px;letter-spacing:2px;margin-top:2px;">MEMBERSHIP ACTIVATED</div>
            </td>
          </tr>
          <tr>
            <td style="padding:32px 28px 8px 28px;text-align:center;">
              <div style="width:64px;height:64px;margin:0 auto 18px auto;background:rgba(27,175,122,0.14);
                          border-radius:50%;line-height:64px;font-size:28px;">🎉</div>
              <div style="color:#141820;font-size:19px;font-weight:800;margin-bottom:10px;">Congratulations, {first_name}!</div>
              <div style="color:#3a3f4b;font-size:14px;line-height:1.7;margin-bottom:6px;">
                Your payment has been verified and you are now officially
                one of the members of <strong style="color:#141820;">Power Gym</strong>{plan_line}.
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:10px 28px 4px 28px;text-align:center;">
              <div style="background:#f2f3f6;border:1px solid #dcdfe6;border-radius:10px;padding:16px 18px;">
                <div style="color:#6b7280;font-size:11px;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px;">You can start training on</div>
                <div style="color:#141820;font-size:20px;font-weight:800;">{start_label}</div>
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:22px 28px 6px 28px;text-align:center;">
              <div style="color:#6b7280;font-size:12px;line-height:1.6;">
                Sign in to your member dashboard anytime to check your plan status,
                attendance, and renewal date.
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 28px 28px 28px;text-align:center;">
              <div style="height:1px;background:#e2e4ea;margin-bottom:16px;"></div>
              <div style="color:#9aa0b0;font-size:11px;line-height:1.6;">
                Questions about your membership? Just ask our front desk staff.
              </div>
            </td>
          </tr>
        </table>
        <div style="color:#9aa0b0;font-size:11px;margin-top:18px;font-family:Arial,Helvetica,sans-serif;">
          © Power Gym. This is an automated message, please do not reply.
        </div>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _send_membership_activated_email(member, plan, start_date):
    """Best-effort congratulations email once a membership is activated by
    an approved payment. Mirrors the OTP email's dev-mode fallback: if SMTP
    isn't configured, or sending fails, we log it and move on rather than
    blocking the approval itself — the membership is already active either way."""
    if not member or not member.email:
        return
    plan_name = plan.name if plan else ''
    if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD'):
        try:
            msg = Message(
                subject='POWER GYM — Welcome! Your Membership Is Active',
                recipients=[member.email],
                body=(
                    f"Hi {member.first_name},\n\n"
                    f"Congratulations — you are now officially one of the members of Power Gym"
                    f"{f' on the {plan_name} plan' if plan_name else ''}!\n\n"
                    f"You can start your membership on {start_date.strftime('%B %d, %Y')}.\n\n"
                    f"Sign in to your member dashboard anytime to check your plan status, "
                    f"attendance, and renewal date."
                ),
                html=_membership_activated_email_html(member.first_name, plan_name, start_date),
            )
            mail.send(msg)
        except Exception as e:
            print(f"[MAIL ERROR] Could not send membership-activated email to {member.email}: {e}")
    else:
        print(f"[DEV] Email not configured. Membership-activated email would be sent to "
              f"{member.email} — plan={plan_name}, start={start_date}")


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
                        html=_otp_email_html(user.first_name, otp),
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
    if payment.status not in ('pending', 'approved'):
        # Someone else already finished processing this exact request (e.g. two
        # staff/admin tabs had the same card open). Tell the client to just drop
        # the stale card instead of showing a scary error toast. ──
        return jsonify(success=False, error='This request was already processed by someone else.',
                        stale=True), 409

    # ── Stage 1: the plan request itself is awaiting approval — no payment
    #    method has been chosen yet. This is staff's call: approving here
    #    just greenlights the plan so the member can proceed to pay; it does
    #    NOT activate the membership yet. Admin doesn't act at this stage. ──
    if payment.status == 'pending':
        if session.get('role') != 'staff':
            return jsonify(success=False, error='Plan requests are approved by staff, not admin.'), 403

        if action == 'reject':
            payment.status = 'rejected'
            payment.verified_at = datetime.now(timezone.utc)
            payment.notified = False  # let the member see a "request declined" notice
            # Only flip the membership itself to 'declined' if it was sitting
            # there *because of this pending request* (status == 'pending').
            # If the member already has an active plan and this was a renewal
            # request on top of it, leave the active membership untouched —
            # only the renewal attempt was declined, not their current plan.
            membership = Membership.query.filter_by(member_id=payment.member_id).first()
            if membership and membership.status == 'pending':
                membership.status = 'declined'
            db.session.commit()
            return jsonify(success=True, message='Plan request rejected.', status='rejected')

        # Staff reviews the uploaded school ID (if any) at this stage and has
        # the final say on student status — the member's self-reported
        # checkbox at request time is just a starting point. Whatever staff
        # confirms here becomes the amount actually charged.
        if 'is_student' in data:
            confirmed_student = (data.get('is_student') or '').strip().lower() in ('1', 'true', 'yes')
            payment.is_student = confirmed_student
            payment.amount     = _payment_total(payment.plan, confirmed_student, payment.coach_name)

        payment.status = 'approved'
        payment.method = 'Pending — choose payment method'
        payment.notified = False
        db.session.commit()
        return jsonify(success=True, message='Plan approved — the member can now submit payment.', status='approved')

    # ── Stage 2: the plan was already approved by staff; this verifies the
    #    payment the member has since submitted. Who handles it depends on
    #    the payment method the member chose — Cash payments are confirmed
    #    by front-desk staff, GCash payments are verified by admin. ──
    if payment.method.startswith('Pending'):
        return jsonify(success=False, error='This member has not chosen a payment method yet. Ask them to complete payment on their Payment tab before verifying.'), 400

    required_role = 'staff' if payment.method == 'Cash' else 'admin'
    if session.get('role') != required_role:
        # Not this role's card to act on — not an error, just stale for them.
        if required_role == 'staff':
            error = 'This is a Cash payment — it\'s confirmed by front-desk staff, no action needed here.'
        else:
            error = 'This is a GCash payment — it\'s verified by admin, no action needed here.'
        return jsonify(success=False, error=error, stale=True), 409
    if action == 'approve' and payment.method == 'GCash' and not payment.proof_image_path:
        return jsonify(success=False, error='No GCash proof of payment was uploaded for this request.'), 400

    if action == 'reject':
        payment.status = 'rejected'
        payment.verified_at = datetime.now(timezone.utc)
        payment.notified = False  # let the member see a "request declined" notice
        membership = Membership.query.filter_by(member_id=payment.member_id).first()
        if membership and membership.status == 'pending':
            membership.status = 'declined'
        db.session.commit()
        return jsonify(success=True, message='Payment rejected.', status='rejected')

    # ── Approve: activate/extend membership (same logic as staff_record_payment) ──
    payment.status = 'verified'
    payment.verified_at = datetime.now(timezone.utc)
    payment.notified = False  # let the member see a fresh "payment approved!" popup

    member = payment.member
    plan   = payment.plan
    today  = _today_manila()

    membership = Membership.query.filter_by(member_id=member.id).first()

    # Only an already-ACTIVE membership represents real remaining time worth
    # stacking a renewal on top of. A 'pending' membership's expiry_date is
    # just a preview computed when the request was first submitted (before
    # staff/admin ever approved it) — treating that as "existing unexpired
    # time" here would double-count the plan's duration on top of itself.
    was_active_with_time = (
        membership is not None
        and membership.status == 'active'
        and membership.expiry_date is not None
        and membership.expiry_date > today
    )

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
    # unexpired time on an active plan, honor that date instead of "today".
    requested_start = payment.requested_start_date
    if was_active_with_time:
        base_date = membership.expiry_date
    else:
        base_date = requested_start if (requested_start and requested_start > today) else today
        membership.start_date = base_date

    membership.expiry_date = _plan_expiry(plan, base_date)
    if plan is not None:
        membership.plan_id = plan.id
    membership.status = 'active'

    if member.status != 'active':
        member.status = 'active'

    db.session.commit()

    _send_membership_activated_email(member, plan, membership.start_date)

    return jsonify(
        success=True,
        message='Payment approved — membership activated!',
        status='verified',
        expiry=membership.expiry_date.strftime('%b %d, %Y'),
    )


def _payment_stage(p):
    """Classify an in-progress Payment into its approval stage:
       - 'approval'        : the plan request itself, awaiting staff sign-off
                              (no payment method chosen yet)
       - 'awaiting_payment': plan approved, member hasn't chosen Cash/GCash yet
       - 'verify_cash'     : Cash payment, confirmed by front-desk staff
       - 'verify_gcash'    : GCash payment, verified by admin
    """
    if p.status == 'pending':
        return 'approval'
    if p.method.startswith('Pending'):
        return 'awaiting_payment'
    if p.method == 'Cash':
        return 'verify_cash'
    return 'verify_gcash'


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

    existing_request = (
        Payment.query
        .filter(Payment.member_id == user.id, Payment.status.in_(['pending', 'approved']))
        .first()
    )
    if existing_request is not None:
        if existing_request.status == 'pending':
            return jsonify(success=False, error='You already have a plan request awaiting staff approval.'), 409
        return jsonify(success=False, error='Your plan has already been approved — head to the Payment tab to complete payment.'), 409

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

    if wants_coach:
        coach = Coach.query.filter_by(name=coach_name, is_active=True).first()
        if coach is None:
            return jsonify(success=False, error='Please select a coach.'), 400
        occupancy = _get_coach_occupancy().get(coach.name, 0)
        if occupancy >= coach.max_members:
            return jsonify(success=False, error=f'{coach.name} is currently at full capacity. Please choose another coach.'), 409
    else:
        coach_name = None

    payment_amount = _payment_total(plan, is_student, coach_name)

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
        amount=payment_amount,
        method='Pending — awaiting staff approval',
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


@app.route('/member/cancel-plan-request', methods=['POST'])
def member_cancel_plan_request():
    """Member withdraws their own plan request — but only while it's still
    strictly Pending, i.e. staff hasn't opened/reviewed it yet. Once staff
    has seen it (Processing) or made a decision (Approved/Declined), it's no
    longer cancelable here — it's already in motion.

    Cancelling removes the request from staff's queue entirely (the
    'pending'/'approved' filter used everywhere else naturally excludes
    'cancelled'), and — if this request hadn't been activated yet — clears
    the placeholder membership row so the member can submit a fresh request
    right away."""
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
        return jsonify(success=False, error='No pending plan request to cancel.'), 404

    if payment.staff_viewed:
        return jsonify(success=False, error='Staff has already started reviewing this request — it can no longer be cancelled.'), 409

    payment.status = 'cancelled'
    payment.verified_at = datetime.now(timezone.utc)
    payment.notified = True  # member cancelled it themselves — no popup needed

    # Only clear the membership if it was 'pending' *because of this request*.
    # An already-active membership (this was a renewal attempt on top of it)
    # stays untouched.
    membership = Membership.query.filter_by(member_id=user.id).first()
    if membership and membership.status == 'pending':
        db.session.delete(membership)

    db.session.commit()

    return jsonify(success=True, message='Plan request cancelled. You can submit a new request anytime.')


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
        .filter_by(member_id=user.id, status='approved')
        .order_by(Payment.paid_at.desc())
        .first()
    )
    if payment is None:
        return jsonify(success=False, error='No approved plan found. Please wait for staff to approve your plan request before paying.'), 400
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
        payment.method            = 'Cash'
        payment.reference_number  = None
        payment.proof_image_path  = None

    db.session.commit()

    if payment_method == 'gcash':
        message = 'GCash payment submitted! Awaiting verification by admin.'
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

    member, error = _find_member(member_identifier)
    if error:
        return jsonify(success=False, error=error), 404

    plan = MembershipPlan.query.filter_by(name=plan_name).first()
    if plan is None:
        return jsonify(success=False, error='Please select a valid membership plan.'), 400

    if not method:
        return jsonify(success=False, error='Please select a payment method.'), 400
    if method != 'Cash':
        # Front-desk entries are Cash only — GCash goes through the member's
        # own submission + Admin verification flow, not this manual form.
        return jsonify(success=False, error='This form only records Cash payments. GCash payments are verified by Admin from the member\'s own submission.'), 400

    # If this member already has an in-progress request (plan request awaiting
    # approval, or a payment awaiting verification), settle that same record
    # instead of leaving it stale — so it disappears from Pending Requests
    # once staff records the payment here.
    existing_request = (
        Payment.query
        .filter(Payment.member_id == member.id, Payment.status.in_(['pending', 'approved']))
        .order_by(Payment.paid_at.desc())
        .first()
    )
    if existing_request is not None:
        existing_request.plan_id = plan.id
        existing_request.amount = _payment_total(plan, existing_request.is_student, existing_request.coach_name)
        existing_request.method = method
        existing_request.status = 'verified'
        existing_request.recorded_by_id = session.get('user_id')
        existing_request.verified_at = datetime.now(timezone.utc)
        existing_request.notified = False  # let the member see a fresh "payment approved!" popup
        new_payment = existing_request
    else:
        new_payment = Payment(
            member_id=member.id,
            plan_id=plan.id,
            amount=plan.price,
            method=method,
            status='verified',
            recorded_by_id=session.get('user_id'),
            verified_at=datetime.now(timezone.utc),
        )
        db.session.add(new_payment)

    # Extend (or create) the member's membership, starting from whichever is later:
    # today, or their current expiry date (so early renewals stack on top of remaining time).
    today = _today_manila()
    membership = Membership.query.filter_by(member_id=member.id).first()

    # Only stack on top of an already-ACTIVE membership's remaining time. A
    # 'pending' membership's expiry_date is just a preview computed when the
    # plan request was first submitted — stacking on that would double-count
    # the plan's duration on top of itself.
    was_active_with_time = (
        membership is not None
        and membership.status == 'active'
        and membership.expiry_date is not None
        and membership.expiry_date > today
    )

    if membership is None:
        membership = Membership(member_id=member.id, plan_id=plan.id, start_date=today,
                                 expiry_date=today, status='active')
        db.session.add(membership)

    base_date = membership.expiry_date if was_active_with_time else today
    membership.plan_id     = plan.id
    membership.expiry_date = _plan_expiry(plan, base_date)
    membership.status      = 'active'
    if member.status != 'active':
        member.status = 'active'

    db.session.commit()

    _send_membership_activated_email(member, plan, membership.start_date)

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


VALID_COACH_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


@app.route('/staff/coach/update', methods=['POST'])
def staff_update_coach():
    """Staff edits a coach's available days and member capacity so members
    see accurate availability when requesting that coach."""
    if session.get('role') not in ('staff', 'admin'):
        return jsonify(success=False, error='Unauthorized.'), 403

    coach_id = request.form.get('coach_id', type=int)
    coach = Coach.query.get(coach_id) if coach_id else None
    if coach is None:
        return jsonify(success=False, error='Coach not found.'), 404

    days = request.form.getlist('available_days')
    days = [d for d in days if d in VALID_COACH_DAYS]
    # Keep Mon..Sun order regardless of checkbox submission order
    days = [d for d in VALID_COACH_DAYS if d in days]

    max_members = request.form.get('max_members', type=int)
    if max_members is None or max_members < 1:
        return jsonify(success=False, error='Capacity must be at least 1 member.'), 400

    fee = request.form.get('fee', type=float)
    if fee is None or fee < 0:
        return jsonify(success=False, error='Coach fee must be 0 or a positive amount.'), 400

    current_occupancy = _get_coach_occupancy().get(coach.name, 0)
    if max_members < current_occupancy:
        return jsonify(
            success=False,
            error=f'{coach.name} currently has {current_occupancy} active member(s) — '
                  f'capacity cannot be set below that.'
        ), 400

    coach.available_days = ','.join(days)
    coach.max_members = max_members
    coach.fee = fee
    db.session.commit()

    return jsonify(success=True, message=f"{coach.name}'s availability and fee updated.")


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


@app.route('/admin/update-gcash-settings', methods=['POST'])
def admin_update_gcash_settings():
    """Admin-only: update the GCash account number/name members are shown
    when submitting a payment. Takes effect immediately for every member,
    since the member dashboard reads this same row on each page load."""
    if session.get('role') != 'admin':
        return jsonify(success=False, error='Unauthorized.'), 403

    data = request.get_json(silent=True) or {}
    gcash_number       = (data.get('gcash_number') or '').strip()
    gcash_account_name = (data.get('gcash_account_name') or '').strip()

    if not gcash_number or not gcash_account_name:
        return jsonify(success=False, error='GCash number and account name are both required.'), 400
    digits_only = gcash_number.replace(' ', '').replace('-', '')
    if not _valid_phone(digits_only):
        return jsonify(success=False, error='Enter a valid GCash number, e.g. 0917 123 4567.'), 400

    settings = _get_gym_settings()
    # Store in the same spaced format shown to members: 0917 123 4567
    settings.gcash_number       = f"{digits_only[0:4]} {digits_only[4:7]} {digits_only[7:11]}"
    settings.gcash_account_name = gcash_account_name.upper()
    db.session.commit()

    return jsonify(success=True, message='GCash payment details updated.', settings={
        'gcash_number':       settings.gcash_number,
        'gcash_account_name': settings.gcash_account_name,
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
    declined_plan_info = None
    if membership and plan_obj:
        if membership.status == 'declined':
            # A declined request never actually became a real membership —
            # there's no real start/expiry/days-left to report, so it should
            # NOT populate the "Current Plan" panel (that panel is only for
            # plans that are/were actually in effect). Keep just the plan
            # name so the Upgrade/Renew panel can still show its "request
            # was declined" banner; the Current Plan panel itself falls back
            # to its normal "NO ACTIVE PLAN" state.
            declined_plan_info = {'name': plan_obj.name}
        else:
            # Count down from whichever is later: today, or the membership's
            # own start date. Without this, a membership that hasn't started
            # yet (start_date in the future) shows a "Days Left" figure
            # counted from today — which can end up LONGER than the plan's
            # own duration (e.g. a Monthly plan showing 63 days left).
            # Clamping to the start date keeps Days Left always inside the
            # plan's real length.
            effective_start = max(membership.start_date, today)
            days_total = max((membership.expiry_date - membership.start_date).days, 1)
            days_left  = max((membership.expiry_date - effective_start).days, 0)
            days_used  = max(min(days_total - days_left, days_total), 0)
            percent_used = int((days_used / days_total) * 100) if days_total else 0

            if membership.expiry_date < today:
                plan_status = 'Expired'
            elif membership.status == 'pending':
                plan_status = 'Pending'
            else:
                plan_status = 'Active'

            # ── Payment status — deliberately kept separate from the plan
            #    status above. "Plan Status" describes whether the membership
            #    itself is currently in effect (Active/Pending/Expired).
            #    "Payment Status" describes where the underlying payment sits
            #    in staff/admin's approval pipeline for this member's most
            #    recent request, so a member isn't left guessing why their
            #    plan says "Pending" (e.g. staff already approved the request
            #    — the plan is just waiting on the member to pay). ──
            latest_payment_row = (
                Payment.query
                .filter_by(member_id=user.id)
                .order_by(Payment.paid_at.desc())
                .first()
            )
            payment_status = 'Verified'
            if latest_payment_row is not None:
                if latest_payment_row.status == 'pending':
                    payment_status = 'Pending Staff Approval'
                elif latest_payment_row.status == 'approved' and latest_payment_row.method.startswith('Pending'):
                    payment_status = 'Approved — Awaiting Payment'
                elif latest_payment_row.status == 'approved' and latest_payment_row.method == 'Cash':
                    payment_status = 'Awaiting Staff Verification (Cash)'
                elif latest_payment_row.status == 'approved':
                    payment_status = 'Awaiting Admin Verification (GCash)'
                elif latest_payment_row.status == 'verified':
                    payment_status = 'Verified'
                elif latest_payment_row.status == 'rejected':
                    payment_status = 'Rejected'
                elif latest_payment_row.status == 'cancelled':
                    payment_status = 'Cancelled'

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
                'payment_status': payment_status,
            }

    # ── Attendance (current month) ──
    current_month_data = _get_member_attendance_month(user.id, today.year, today.month)
    days_in_month   = current_month_data['days_in_month']
    present_days    = current_month_data['present_days']
    no_plan_days    = current_month_data['no_plan_days']
    session_history = current_month_data['session_history']
    today_day       = current_month_data['today_day']

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
    # A row that was declined at the *plan request* stage (status='rejected'
    # while the method is still the placeholder "Pending — ...") never had an
    # actual payment attached — the member was never asked to pay, so it
    # doesn't belong in "Past Payments". Only show declined rows here if a
    # real payment method (Cash/GCash) had actually been submitted and later
    # rejected.
    #
    # A row the member cancelled themselves (status='cancelled', via
    # /member/cancel-plan-request) is excluded entirely — the member
    # withdrew the request before it went anywhere, so it should not be
    # recorded in Past Payments at all.
    payment_rows = (
        Payment.query
        .options(joinedload(Payment.plan))
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
        'is_student': p.is_student,
    } for p in payment_rows
      if not (p.status == 'rejected' and (p.method or '').startswith('Pending'))
      and p.status != 'cancelled']

    # ── Awaiting approval (plan request submitted, staff/admin hasn't
    #    reviewed it yet — no payment can be made until it's approved) ──
    awaiting_approval_row = (
        Payment.query
        .filter_by(member_id=user.id, status='pending')
        .order_by(Payment.paid_at.desc())
        .first()
    )
    awaiting_approval = None
    if awaiting_approval_row is not None:
        awaiting_approval = {
            'id':          awaiting_approval_row.id,
            'plan_name':  awaiting_approval_row.plan.name if awaiting_approval_row.plan else '—',
            'amount':     f'{float(awaiting_approval_row.amount):,.2f}',
            'start_date': awaiting_approval_row.requested_start_date.strftime('%b %d, %Y') if awaiting_approval_row.requested_start_date else '—',
            'is_student': awaiting_approval_row.is_student,
            # 'Pending'    — submitted, staff hasn't opened the Request tab yet
            # 'Processing' — staff has opened it, decision not made yet
            'request_status': 'Processing' if awaiting_approval_row.staff_viewed else 'Pending',
        }

    # ── Pending payment (plan already approved by staff/admin — drives the
    #    "Submit Payment" panel on the Payment tab) ──
    pending_payment_row = (
        Payment.query
        .filter_by(member_id=user.id, status='approved')
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
            'is_student':  pending_payment_row.is_student,
            'request_status': 'Approved',
        }

    # ── Just-approved notice (shown once as a "Congratulations! Proceed to
    #    payment" popup) — fires when staff/admin approves the PLAN, which
    #    is the point at which the member is actually allowed to pay. ──
    just_approved_row = (
        Payment.query
        .filter_by(member_id=user.id, status='approved', notified=False)
        .order_by(Payment.paid_at.desc())
        .first()
    )
    plan_approved_notice = None
    if just_approved_row is not None:
        plan_approved_notice = {
            'plan_name': just_approved_row.plan.name if just_approved_row.plan else 'membership',
        }
        just_approved_row.notified = True
        db.session.commit()

    # ── Just-verified notice (shown once as a "Congratulations! Payment
    #    approved" popup with the membership start date) — fires when admin
    #    approves the actual PAYMENT (Cash/GCash, or a front-desk payment
    #    recorded directly by staff), which is the point the membership
    #    actually activates. ──
    just_verified_row = (
        Payment.query
        .filter_by(member_id=user.id, status='verified', notified=False)
        .order_by(Payment.paid_at.desc())
        .first()
    )
    payment_verified_notice = None
    if just_verified_row is not None:
        start_date_text = (
            membership.start_date.strftime('%B %d, %Y')
            if membership and membership.start_date
            else (just_verified_row.requested_start_date.strftime('%B %d, %Y')
                  if just_verified_row.requested_start_date else today.strftime('%B %d, %Y'))
        )
        payment_verified_notice = {
            'plan_name':  just_verified_row.plan.name if just_verified_row.plan else 'membership',
            'start_date': start_date_text,
        }
        just_verified_row.notified = True
        db.session.commit()

    # ── Just-declined notice (shown once as a "Your request was declined"
    #    popup) — fires when staff/admin rejects either the plan request or
    #    the payment itself, at whichever stage it happened. ──
    just_declined_row = (
        Payment.query
        .filter_by(member_id=user.id, status='rejected', notified=False)
        .order_by(Payment.paid_at.desc())
        .first()
    )
    plan_declined_notice = None
    if just_declined_row is not None:
        plan_declined_notice = {
            'plan_name': just_declined_row.plan.name if just_declined_row.plan else 'membership',
        }
        just_declined_row.notified = True
        db.session.commit()

    # Members without a paid, active membership only get Overview + My
    # Membership — everything else (attendance history, goals, services) is
    # locked behind an active plan.
    plan_active = bool(current_plan and current_plan['status'] == 'Active')

    # ── Announcements — filtered per member by the admin's chosen target
    #    audience: everyone, active members only, or members expiring
    #    within 30 days of their current plan. ──
    member_days_left = current_plan['days_left'] if current_plan else None
    announcements = [
        a for a in Announcement.query.filter_by(is_active=True).order_by(Announcement.created_at.desc()).all()
        if a.target == 'all'
        or (a.target == 'active' and plan_active)
        or (a.target == 'expiring' and plan_active and member_days_left is not None and member_days_left <= 30)
    ]

    # Announcements posted since this member's last visit pop up as a
    # "Notice from the Admin" message box on this page load, so a new
    # notice doesn't go unnoticed — mirrors the pattern used for
    # plan_approved_notice etc.
    last_seen = user.last_seen_announcements_at
    new_announcements = [
        {'title': a.title, 'body': a.body} for a in announcements
        if last_seen is None or (a.created_at and a.created_at > last_seen)
    ]
    user.last_seen_announcements_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()

    # ── Public-facing content (membership plans / services / equipment) ──
    # Sourced from the same admin/staff-editable tables that drive the home
    # page, so anything they change in Settings → Manage Content shows up
    # here too instead of being hardcoded per-page.
    content_plans = MembershipPlan.query.filter_by(is_active=True).order_by(MembershipPlan.sort_order, MembershipPlan.id).all()
    content_services  = GymService.query.filter_by(is_active=True).order_by(GymService.sort_order, GymService.id).all()
    content_equipment = GymEquipment.query.filter_by(is_active=True).order_by(GymEquipment.sort_order, GymEquipment.id).all()

    # Equipment grouped by category for the "Gym Machines and Equipment"
    # display — each group is (category_name, category_icon, [items]),
    # preserving sort_order within the category and category first-seen
    # order overall. Services are shown as a flat grid (each service acts
    # as its own "category" card, e.g. "Boxing" / "Strengthening"), so no
    # grouping is needed for them. Facility-zone photos (is_facility=True,
    # e.g. "Weight Area", "Reception") are shown on the home page's "Our
    # Facilities" section but are not real individual machines, so they're
    # left out of this member-facing equipment list.
    real_equipment = [e for e in content_equipment if not e.is_facility]
    equipment_by_category = _group_content_by_category(real_equipment, default_icon=DEFAULT_EQUIPMENT_ICON)
    services_by_category  = _group_content_by_category(content_services, default_icon=DEFAULT_SERVICE_ICON)

    # ── Coaches (for the "Choose a Coach" field on the plan request form) —
    #    shows each coach's available days and remaining slots so members
    #    can pick one that's actually open. ──
    coaches_data = [c for c in _get_coaches_data() if c['is_active']]

    plans_data = [{
        'key':            p.name.lower(),
        'name':           p.name,
        'price':          p.price,
        'duration_days':  p.duration_days,
        'description':    p.description or '',
        'inclusions':     p.inclusions_list,
        'image_path':     url_for('static', filename=p.image_path) if p.image_path else '',
    } for p in content_plans]

    services_data = [{
        'id':          s.id,
        'name':        s.name,
        'description': s.description or '',
        'image_path':  url_for('static', filename=s.image_path) if s.image_path else '',
        'category':    s.category or DEFAULT_CATEGORY,
        'icon':        s.icon or DEFAULT_SERVICE_ICON,
        'equipment':   [{'name': e.name, 'icon': e.icon or DEFAULT_EQUIPMENT_ICON} for e in s.equipment],
    } for s in content_services]

    return render_template(
        'member-dashboard.html',
        member=user,
        plan=current_plan,
        declined_plan=declined_plan_info,
        plan_active=plan_active,
        content_plans=content_plans,
        content_services=content_services,
        content_equipment=content_equipment,
        equipment_by_category=equipment_by_category,
        services_by_category=services_by_category,
        plans_data=plans_data,
        services_data=services_data,
        coaches=coaches_data,
        present_days=present_days,
        no_plan_days=no_plan_days,
        days_in_month=days_in_month,
        today_day=today_day,
        month_label=today.strftime('%B %Y'),
        attendance_year=today.year,
        attendance_month=today.month,
        session_history=session_history,
        attendance_rate=attendance_rate,
        goal=goal,
        payment_history=payment_history,
        awaiting_approval=awaiting_approval,
        pending_payment=pending_payment,
        plan_approved_notice=plan_approved_notice,
        payment_verified_notice=payment_verified_notice,
        plan_declined_notice=plan_declined_notice,
        announcements=announcements,
        new_announcements=new_announcements,
        gcash_settings=_get_gym_settings(),
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
        'today_day': today.day,
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
        .options(joinedload(Attendance.member))
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
    member_ids = [m['id'] for m in members]

    # Single batched query for everyone's attendance today, instead of one
    # query per member (which used to mean N extra round-trips for N members
    # on every dashboard load — the main reason the staff dashboard felt slow).
    todays_entries_by_member = {}
    if member_ids:
        todays_rows = (
            Attendance.query
            .filter(
                Attendance.member_id.in_(member_ids),
                Attendance.check_in >= today_start,
                Attendance.check_in <= today_end,
            )
            .order_by(Attendance.check_in.desc())
            .all()
        )
        for a in todays_rows:
            todays_entries_by_member.setdefault(a.member_id, []).append(a)

    result = []
    for m in members:
        entries     = todays_entries_by_member.get(m['id'], [])
        latest      = entries[0] if entries else None
        open_entry  = next((e for e in entries if e.check_out is None), None)

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

    # "No Plan" and "Declined" members aren't relevant to staff's day-to-day
    # (check-in, payment verification, coaching) — the Member Directory only
    # needs to show members who actually have a plan in effect.
    members = [m for m in _get_members_with_plans() if m['status'] not in ('No Plan', 'Declined')]
    active_members       = [m for m in members if m['status'] == 'Active']
    pending_status_members = [m for m in members if m['status'] == 'Pending']
    expiring_soon    = [
        m for m in members
        if m['status'] == 'Active' and m['expiry_date'] and 0 <= (m['expiry_date'] - today).days <= 7
    ]

    # ── Pending plan requests & payments (Request tab). Staff approves the
    #    plan request itself and confirms Cash payments; GCash payments are
    #    verified by admin and shown here for visibility only. ──
    pending_requests_rows = (
        Payment.query
        .options(joinedload(Payment.member), joinedload(Payment.plan))
        .filter(Payment.status.in_(['pending', 'approved']))
        .order_by(Payment.paid_at.desc())
        .all()
    )

    # ── The member-facing "Processing" status fires the moment staff actually
    #    sees a plan request — which is right here, as it's loaded into this
    #    dashboard. Flip the flag for any new-request card that hasn't been
    #    seen yet. ──
    _newly_viewed = False
    for p in pending_requests_rows:
        if p.status == 'pending' and not p.staff_viewed:
            p.staff_viewed = True
            _newly_viewed = True
    if _newly_viewed:
        db.session.commit()

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
        'stage': _payment_stage(p),
        'staff_viewed': p.staff_viewed,
    } for p in pending_requests_rows]


    # ── Recently processed requests — approved only. A declined request just
    #    disappears from view here; it only reappears once the member submits
    #    a new request and that one gets approved. (Admin's Payment History
    #    tab still keeps the full approved+declined audit trail.) ──
    processed_requests_rows = (
        Payment.query
        .options(joinedload(Payment.member), joinedload(Payment.plan))
        .filter(Payment.status == 'verified')
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
        .options(joinedload(Payment.member), joinedload(Payment.plan))
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

    coaches_data = _get_coaches_data()

    stats = {
        'checkins_today':   len(attendance_today),
        'active_members':   len(active_members),
        'pending_payments': len(pending_requests),
        'expiring_soon':    len(expiring_soon),
    }

    # ── Analytics tab: default to "This Month" on first load; the report
    #    generation buttons let staff pick a different range and download
    #    a CSV without needing a page reload. ──
    analytics_start, analytics_end, analytics_range_label = _report_range('this_month')
    analytics = {
        'range_label': analytics_range_label,
        'revenue':     _revenue_report(analytics_start, analytics_end),
        'membership':  _membership_report(),
        'attendance':  _attendance_report(analytics_start, analytics_end),
    }

    # ── Lightweight member list for the Payment Record autocomplete/dropdown.
    #    Keyed by email (unique + always accepted by _find_member), with the
    #    member's current plan so the UI can auto-select the matching Plan
    #    option once a member is chosen. JSON-safe (no date objects). ──
    payment_members = [{
        'id': m['id'],
        'name': m['name'],
        'email': m['email'],
        'plan': m['plan'],
        'status': m['status'],
    } for m in members]

    # Staff only need to see notices actually meant for them — the member-
    # facing "All Members" / "Active Members Only" / "Expiring This Month"
    # announcements belong on the member dashboard, not here.
    announcements = Announcement.query.filter_by(is_active=True, target='staff').order_by(Announcement.created_at.desc()).all()

    # New-since-last-visit announcements pop up as a "Notice from the
    # Admin" message box, same as members.
    staff_user = User.query.get(session['user_id'])
    last_seen = staff_user.last_seen_announcements_at
    new_announcements = [
        {'title': a.title, 'body': a.body} for a in announcements
        if last_seen is None or (a.created_at and a.created_at > last_seen)
    ]
    staff_user.last_seen_announcements_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()

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
        coaches=coaches_data,
        coach_days=VALID_COACH_DAYS,
        payment_members=payment_members,
        analytics=analytics,
        report_ranges=REPORT_RANGES,
        announcements=announcements,
        new_announcements=new_announcements,
        current_user=staff_user,
    )


def _format_currency_short(amount):
    """Compact currency for tight stat-card display, e.g. 86000 -> '86K',
    1250000 -> '1.3M', 950 -> '950'. Full precision is still available
    elsewhere (Analytics tab, CSV exports)."""
    amount = float(amount)
    if abs(amount) >= 1_000_000:
        return f'{amount / 1_000_000:.1f}'.rstrip('0').rstrip('.') + 'M'
    if abs(amount) >= 1_000:
        return f'{amount / 1_000:.1f}'.rstrip('0').rstrip('.') + 'K'
    return f'{amount:,.0f}'


REPORT_RANGES = {
    'today':      'Today',
    'this_month': 'This Month',
    'last_30':    'Last 30 Days',
    'this_year':  'This Year',
    'all_time':   'All Time',
}


def _report_range(range_key):
    """Resolve a report range key into a (start_date, end_date, label) tuple.
    end_date is always today; start_date is None for 'all_time' (no lower bound)."""
    today = _today_manila()
    if range_key == 'today':
        return today, today, REPORT_RANGES['today']
    if range_key == 'last_30':
        return today - timedelta(days=29), today, REPORT_RANGES['last_30']
    if range_key == 'this_year':
        return date(today.year, 1, 1), today, REPORT_RANGES['this_year']
    if range_key == 'all_time':
        return None, today, REPORT_RANGES['all_time']
    return date(today.year, today.month, 1), today, REPORT_RANGES['this_month']


def _resolve_report_window(range_key, from_str=None, to_str=None):
    """Like _report_range, but a valid custom 'from'/'to' pair (YYYY-MM-DD,
    e.g. from a <input type=date> calendar picker) always takes priority over
    the preset range dropdown."""
    if from_str and to_str:
        try:
            start_date = datetime.strptime(from_str, '%Y-%m-%d').date()
            end_date   = datetime.strptime(to_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = end_date = None
        if start_date and end_date:
            if start_date > end_date:
                start_date, end_date = end_date, start_date
            label = f"{start_date.strftime('%b %d, %Y')} \u2013 {end_date.strftime('%b %d, %Y')}"
            return start_date, end_date, label
    return _report_range(range_key)


def _bucket_counts(rows, get_date, start_date, end_date):
    """Bucket rows into a chart-friendly time series across [start_date, end_date].
    Buckets by day when the span is <=31 days (typical for a focused date-range
    lookup), otherwise by month (so a 'This Year' / 'All Time' / long custom
    range still renders a readable handful of bars instead of hundreds)."""
    if start_date is None:
        dates = [get_date(r) for r in rows]
        if not dates:
            return []
        start_date = min(dates)
    if end_date is None:
        end_date = _today_manila()
    if start_date > end_date:
        return []

    span_days = (end_date - start_date).days + 1

    if span_days <= 31:
        buckets = OrderedDict()
        d = start_date
        while d <= end_date:
            buckets[d] = 0
            d += timedelta(days=1)
        for r in rows:
            d = get_date(r)
            if d in buckets:
                buckets[d] += 1
        return [{'label': d.strftime('%b %d'), 'value': v} for d, v in buckets.items()]

    buckets = OrderedDict()
    d = date(start_date.year, start_date.month, 1)
    end_marker = date(end_date.year, end_date.month, 1)
    while d <= end_marker:
        buckets[(d.year, d.month)] = 0
        d = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    for r in rows:
        rd = get_date(r)
        key = (rd.year, rd.month)
        if key in buckets:
            buckets[key] += 1
    return [{'label': date(y, m, 1).strftime('%b %Y'), 'value': v} for (y, m), v in buckets.items()]


def _revenue_report(start_date, end_date, method=None):
    """Verified payments within range: totals, a breakdown per plan, a
    breakdown by payment method (Cash vs GCash — every payment verified by
    staff or admin lands here automatically, no manual entry needed), and a
    breakdown of Cash payments by the staff member who recorded them (so
    front-desk cash collected by each staff member is visible at a glance).
    Pass method='Cash' to restrict the whole report to Cash payments only
    (used by the staff dashboard, which shouldn't see GCash figures)."""
    q = Payment.query.options(
        joinedload(Payment.member), joinedload(Payment.plan), joinedload(Payment.recorded_by)
    ).filter(Payment.status == 'verified')
    if method is not None:
        q = q.filter(Payment.method == method)
    if start_date is not None:
        q = q.filter(Payment.paid_at >= datetime.combine(start_date, datetime.min.time()))
    q = q.filter(Payment.paid_at < datetime.combine(end_date + timedelta(days=1), datetime.min.time()))
    rows = q.order_by(Payment.paid_at.desc()).all()

    total = sum((r.amount for r in rows), start=0)
    by_plan = {}
    for r in rows:
        plan_name = r.plan.name if r.plan else 'Unknown'
        by_plan[plan_name] = by_plan.get(plan_name, 0) + float(r.amount)

    # Every verified payment — Cash (staff-recorded) or GCash (admin-verified) —
    # is tallied here automatically straight from the Payment table.
    by_method = {}
    for r in rows:
        by_method[r.method] = by_method.get(r.method, 0) + float(r.amount)

    # Cash collected, grouped by the staff member who recorded it.
    cash_by_staff = {}
    cash_total = 0.0
    for r in rows:
        if r.method != 'Cash':
            continue
        cash_total += float(r.amount)
        staff_name = r.recorded_by.full_name if r.recorded_by else 'Unrecorded / Unknown'
        entry = cash_by_staff.setdefault(staff_name, {'total': 0.0, 'count': 0})
        entry['total'] += float(r.amount)
        entry['count'] += 1

    gcash_total = by_method.get('GCash', 0.0)

    return {
        'total_revenue':   f'{float(total):,.2f}',
        'transaction_count': len(rows),
        'by_plan': [{'plan': k, 'total': f'{v:,.2f}'} for k, v in sorted(by_plan.items(), key=lambda kv: -kv[1])],
        'by_method': [{'method': k, 'total': f'{v:,.2f}'} for k, v in sorted(by_method.items(), key=lambda kv: -kv[1])],
        'cash_total': f'{cash_total:,.2f}',
        'gcash_total': f'{gcash_total:,.2f}',
        'cash_by_staff': [
            {'staff': k, 'total': f'{v["total"]:,.2f}', 'count': v['count']}
            for k, v in sorted(cash_by_staff.items(), key=lambda kv: -kv[1]['total'])
        ],
        'rows': rows,
    }


def _membership_report():
    """Snapshot of every member's current status, plus new signups this month.
    Declined (and cancelled) plan requests never became a real membership,
    so they're excluded here — they'd otherwise inflate member/status counts
    with requests that were never actually granted."""
    all_members = _get_members_with_plans()
    members = [m for m in all_members if m['status'] not in ('Declined', 'Cancelled')]
    counts = {'Active': 0, 'Pending': 0, 'Expired': 0, 'No Plan': 0}
    for m in members:
        counts[m['status']] = counts.get(m['status'], 0) + 1

    today = _today_manila()
    month_start = datetime.combine(date(today.year, today.month, 1), datetime.min.time())
    new_this_month = User.query.filter(User.role == 'member', User.created_at >= month_start).count()

    return {
        'total_members': len(members),
        'counts': counts,
        'new_this_month': new_this_month,
        'members': members,
    }


def _attendance_report(start_date, end_date):
    """Check-ins within range: totals, unique members, and average visit duration."""
    start_dt = datetime.combine(start_date, datetime.min.time()) if start_date else None
    end_dt   = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    q = Attendance.query.options(joinedload(Attendance.member)).filter(Attendance.check_in < end_dt)
    if start_dt is not None:
        q = q.filter(Attendance.check_in >= start_dt)
    rows = q.order_by(Attendance.check_in.desc()).all()

    unique_members = len({r.member_id for r in rows})
    completed = [r for r in rows if r.check_out is not None]
    avg_minutes = int(sum(
        (r.duration_min if r.duration_min is not None else int((r.check_out - r.check_in).total_seconds() // 60))
        for r in completed
    ) / len(completed)) if completed else 0

    return {
        'total_checkins':  len(rows),
        'unique_members':  unique_members,
        'avg_duration_min': avg_minutes,
        'rows': rows,
    }


def _csv_response(filename, header, row_iter):
    """Build a downloadable CSV Response from a header row and an iterable of rows."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in row_iter:
        writer.writerow(row)
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@app.route('/staff/reports/revenue.csv')
def staff_report_revenue_csv():
    if session.get('role') not in ('staff', 'admin'):
        return redirect(url_for('login'))
    range_key = request.args.get('range', 'this_month')
    start_date, end_date, label = _report_range(range_key)
    # Front-desk staff only ever see Cash — GCash is verified and reported
    # on by Admin. Keep this in lockstep with /api/staff/reports/revenue.
    report = _revenue_report(start_date, end_date, method='Cash')

    def rows():
        for p in report['rows']:
            yield [
                f'TXN-{9000 + p.id}',
                p.member.full_name if p.member else '—',
                p.plan.name if p.plan else '—',
                p.method,
                f'{float(p.amount):,.2f}',
                _to_manila(p.paid_at).strftime('%Y-%m-%d'),
                p.recorded_by.full_name if p.recorded_by else '—',
            ]

    return _csv_response(
        f'revenue-report-cash-{range_key}.csv',
        ['Txn#', 'Member', 'Plan', 'Method', 'Amount (₱)', 'Date', 'Recorded By'],
        rows(),
    )


@app.route('/staff/reports/membership.csv')
def staff_report_membership_csv():
    if session.get('role') not in ('staff', 'admin'):
        return redirect(url_for('login'))
    report = _membership_report()

    def rows():
        for m in report['members']:
            yield [m['name'], m['email'], m['plan'], m['expiry'], m['status']]

    return _csv_response(
        'membership-report.csv',
        ['Name', 'Email', 'Plan', 'Expiry', 'Status'],
        rows(),
    )


@app.route('/staff/reports/attendance.csv')
def staff_report_attendance_csv():
    if session.get('role') not in ('staff', 'admin'):
        return redirect(url_for('login'))
    range_key = request.args.get('range', 'this_month')
    start_date, end_date, label = _report_range(range_key)
    report = _attendance_report(start_date, end_date)

    def rows():
        for a in report['rows']:
            check_in_m  = _to_manila(a.check_in)
            check_out_m = _to_manila(a.check_out) if a.check_out else None
            duration_text = '—'
            if a.check_out:
                mins = a.duration_min if a.duration_min is not None else int((a.check_out - a.check_in).total_seconds() // 60)
                h, m = divmod(mins, 60)
                duration_text = f'{h}h {m}m' if h else f'{m}m'
            yield [
                a.member.full_name if a.member else '—',
                check_in_m.strftime('%Y-%m-%d'),
                check_in_m.strftime('%I:%M %p').lstrip('0'),
                check_out_m.strftime('%I:%M %p').lstrip('0') if check_out_m else '—',
                duration_text,
            ]

    return _csv_response(
        f'attendance-report-{range_key}.csv',
        ['Member', 'Date', 'Check-in', 'Check-out', 'Duration'],
        rows(),
    )


# ── Admin Analytics tab: live JSON report generator ──────────────────────
# Powers the "Report Generator" panel on the admin dashboard — every verified
# Cash or GCash payment is picked up automatically (no manual entry) since it
# reads straight from the same Payment table that staff/admin verification
# writes to. Accepts either a preset ?range= key or an explicit ?from=&to=
# calendar date range (the latter always wins when both are given).
@app.route('/api/admin/reports/<report_type>')
def api_admin_report(report_type):
    if session.get('role') != 'admin':
        return jsonify(success=False, error='Unauthorized.'), 403

    range_key = request.args.get('range', 'this_month')
    from_str  = request.args.get('from')
    to_str    = request.args.get('to')
    start_date, end_date, range_label = _resolve_report_window(range_key, from_str, to_str)

    if report_type == 'membership':
        report = _membership_report()
        status_order = ['Active', 'Pending', 'Expired', 'No Plan']
        # Membership is always a live current snapshot — it isn't filtered
        # by the From/To pickers (there's no "membership status as of a
        # past date" to look up), so the label must say that plainly
        # instead of echoing back a date range that was never applied.
        payload = {
            'title': 'Membership Report',
            'range_label': f"Current Snapshot \u2014 {_today_manila().strftime('%b %d, %Y')}",
            'stats': [
                {'label': 'Total Members', 'value': str(report['total_members'])},
                {'label': 'Active',        'value': str(report['counts'].get('Active', 0))},
                {'label': 'Pending',       'value': str(report['counts'].get('Pending', 0))},
                {'label': 'Expired',       'value': str(report['counts'].get('Expired', 0))},
                {'label': 'New This Month','value': str(report['new_this_month'])},
            ],
            'headers': ['Member', 'Email', 'Plan', 'Status', 'Expiry'],
            'rows': [[m['name'], m['email'], m['plan'], m['status'], m['expiry']] for m in report['members']],
            'chart_label': 'Membership Status',
            'chart_series': [
                {'label': s, 'value': report['counts'].get(s, 0)}
                for s in status_order if report['counts'].get(s, 0)
            ],
        }

    elif report_type == 'revenue':
        report = _revenue_report(start_date, end_date)
        by_method_map = {row['method']: row['total'] for row in report['by_method']}
        payload = {
            'title': 'Revenue Report',
            'range_label': range_label,
            'stats': [
                {'label': 'Total Revenue',  'value': f"\u20b1{report['total_revenue']}"},
                {'label': 'Transactions',   'value': str(report['transaction_count'])},
                {'label': 'Cash Collected', 'value': f"\u20b1{by_method_map.get('Cash', '0.00')}"},
                {'label': 'GCash Collected','value': f"\u20b1{by_method_map.get('GCash', '0.00')}"},
            ],
            'headers': ['Txn#', 'Member', 'Plan', 'Method', 'Amount (\u20b1)', 'Date', 'Recorded By'],
            'rows': [[
                f'TXN-{9000 + p.id}',
                p.member.full_name if p.member else '\u2014',
                p.plan.name if p.plan else '\u2014',
                p.method,
                f'{float(p.amount):,.2f}',
                _to_manila(p.paid_at).strftime('%b %d, %Y'),
                p.recorded_by.full_name if p.recorded_by else '\u2014',
            ] for p in report['rows']],
            'chart_label': 'Revenue by Payment Method',
            'chart_series': [{'label': row['method'], 'value': float(row['total'].replace(',', ''))} for row in report['by_method']],
            'by_plan': report['by_plan'],
            'cash_by_staff': report['cash_by_staff'],
        }

    elif report_type == 'attendance':
        report = _attendance_report(start_date, end_date)
        chart_series = _bucket_counts(
            report['rows'], lambda a: _to_manila(a.check_in).date(), start_date, end_date
        )
        payload = {
            'title': 'Attendance Report',
            'range_label': range_label,
            'stats': [
                {'label': 'Total Check-ins',  'value': str(report['total_checkins'])},
                {'label': 'Unique Members',   'value': str(report['unique_members'])},
                {'label': 'Avg Duration',     'value': f"{report['avg_duration_min']} min"},
            ],
            'headers': ['Member', 'Date', 'Check-in', 'Check-out', 'Duration'],
            'rows': [[
                a.member.full_name if a.member else '\u2014',
                _to_manila(a.check_in).strftime('%b %d, %Y'),
                _to_manila(a.check_in).strftime('%I:%M %p').lstrip('0'),
                _to_manila(a.check_out).strftime('%I:%M %p').lstrip('0') if a.check_out else '\u2014',
                (lambda mins: (f'{mins // 60}h {mins % 60}m' if mins >= 60 else f'{mins}m'))(
                    a.duration_min if a.duration_min is not None else int((a.check_out - a.check_in).total_seconds() // 60)
                ) if a.check_out else '\u2014',
            ] for a in report['rows']],
            'chart_label': 'Check-ins Over Time',
            'chart_series': chart_series,
        }

    else:
        return jsonify(success=False, error='Unknown report type.'), 400

    return jsonify(success=True, report=payload)


# ── Staff Analytics tab: live JSON report generator ──────────────────────
# Same "Report Generator" experience as the admin dashboard — staff can pull
# Membership, Attendance, and Revenue reports. Membership and Attendance are
# full snapshots identical to what Admin sees (they aren't tied to a payment
# method). Revenue is the one exception: it's always restricted to Cash
# payments — GCash is verified and reported on by Admin, not front-desk staff.
@app.route('/api/staff/reports/<report_type>')
def api_staff_report(report_type):
    if session.get('role') not in ('staff', 'admin'):
        return jsonify(success=False, error='Unauthorized.'), 403

    if report_type not in ('revenue', 'membership', 'attendance'):
        return jsonify(success=False, error='Unknown report type.'), 400

    from_str = request.args.get('from')
    to_str   = request.args.get('to')
    start_date, end_date, range_label = _resolve_report_window('this_month', from_str, to_str)

    if report_type == 'membership':
        report = _membership_report()
        status_order = ['Active', 'Pending', 'Expired', 'No Plan']
        payload = {
            'title': 'Membership Report',
            'range_label': f"Current Snapshot \u2014 {_today_manila().strftime('%b %d, %Y')}",
            'stats': [
                {'label': 'Total Members', 'value': str(report['total_members'])},
                {'label': 'Active',        'value': str(report['counts'].get('Active', 0))},
                {'label': 'Pending',       'value': str(report['counts'].get('Pending', 0))},
                {'label': 'Expired',       'value': str(report['counts'].get('Expired', 0))},
                {'label': 'New This Month','value': str(report['new_this_month'])},
            ],
            'headers': ['Member', 'Email', 'Plan', 'Status', 'Expiry'],
            'rows': [[m['name'], m['email'], m['plan'], m['status'], m['expiry']] for m in report['members']],
            'chart_label': 'Membership Status',
            'chart_series': [
                {'label': s, 'value': report['counts'].get(s, 0)}
                for s in status_order if report['counts'].get(s, 0)
            ],
        }

    elif report_type == 'attendance':
        report = _attendance_report(start_date, end_date)
        chart_series = _bucket_counts(
            report['rows'], lambda a: _to_manila(a.check_in).date(), start_date, end_date
        )
        payload = {
            'title': 'Attendance Report',
            'range_label': range_label,
            'stats': [
                {'label': 'Total Check-ins',  'value': str(report['total_checkins'])},
                {'label': 'Unique Members',   'value': str(report['unique_members'])},
                {'label': 'Avg Duration',     'value': f"{report['avg_duration_min']} min"},
            ],
            'headers': ['Member', 'Date', 'Check-in', 'Check-out', 'Duration'],
            'rows': [[
                a.member.full_name if a.member else '\u2014',
                _to_manila(a.check_in).strftime('%b %d, %Y'),
                _to_manila(a.check_in).strftime('%I:%M %p').lstrip('0'),
                _to_manila(a.check_out).strftime('%I:%M %p').lstrip('0') if a.check_out else '\u2014',
                (lambda mins: (f'{mins // 60}h {mins % 60}m' if mins >= 60 else f'{mins}m'))(
                    a.duration_min if a.duration_min is not None else int((a.check_out - a.check_in).total_seconds() // 60)
                ) if a.check_out else '\u2014',
            ] for a in report['rows']],
            'chart_label': 'Check-ins Over Time',
            'chart_series': chart_series,
        }

    else:  # revenue — staff only ever sees Cash
        report = _revenue_report(start_date, end_date, method='Cash')
        payload = {
            'title': 'Revenue Report (Cash)',
            'range_label': range_label,
            'stats': [
                {'label': 'Total Cash Revenue', 'value': f"\u20b1{report['total_revenue']}"},
                {'label': 'Transactions',       'value': str(report['transaction_count'])},
            ],
            'headers': ['Txn#', 'Member', 'Plan', 'Amount (\u20b1)', 'Date', 'Recorded By'],
            'rows': [[
                f'TXN-{9000 + p.id}',
                p.member.full_name if p.member else '\u2014',
                p.plan.name if p.plan else '\u2014',
                f'{float(p.amount):,.2f}',
                _to_manila(p.paid_at).strftime('%b %d, %Y'),
                p.recorded_by.full_name if p.recorded_by else '\u2014',
            ] for p in report['rows']],
            'chart_label': 'Cash Revenue',
            'chart_series': [{'label': 'Cash', 'value': float(report['total_revenue'].replace(',', ''))}] if report['transaction_count'] else [],
            'by_plan': report['by_plan'],
            'cash_by_staff': report['cash_by_staff'],
        }

    return jsonify(success=True, report=payload)


def _get_coach_occupancy():
    """Return {coach_name: count} of members currently occupying a slot with
    that coach — i.e. members with an active membership whose most recent
    verified payment requested that coach. Renewals without a coach, or
    expired/declined memberships, don't count."""
    active_member_ids = {
        m.member_id for m in Membership.query.filter_by(status='active').all()
    }
    latest_verified_payment = {}
    for p in (Payment.query.filter(Payment.status == 'verified')
              .order_by(Payment.paid_at.asc()).all()):
        latest_verified_payment[p.member_id] = p  # last one wins -> most recent

    occupancy = {}
    for member_id, p in latest_verified_payment.items():
        if member_id in active_member_ids and p.wants_coach and p.coach_name:
            occupancy[p.coach_name] = occupancy.get(p.coach_name, 0) + 1
    return occupancy


def _get_coaches_data():
    """Coaches with computed occupancy/slots-left, for both the staff Coach
    tab (management) and the member plan-request form (availability)."""
    occupancy = _get_coach_occupancy()
    coaches = Coach.query.order_by(Coach.name).all()
    return [{
        'id':             c.id,
        'name':           c.name,
        'available_days': c.available_days_list,
        'max_members':    c.max_members,
        'fee':            float(c.fee),
        'is_active':      c.is_active,
        'current_members': occupancy.get(c.name, 0),
        'slots_left':     max(c.max_members - occupancy.get(c.name, 0), 0),
        'is_full':        occupancy.get(c.name, 0) >= c.max_members,
    } for c in coaches]


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
            elif membership.status == 'declined':
                status_label = 'Declined'
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

    # ── Overview stat cards — real counts/totals, not placeholders ──
    month_start, month_end, _ = _report_range('this_month')
    monthly_revenue_report = _revenue_report(month_start, month_end)
    stats = {
        'total_members':   len(members),
        'active_members':  len([m for m in members if m['status'] == 'Active']),
        'checkins_today':  len(attendance_today),
        'monthly_revenue': _format_currency_short(monthly_revenue_report['total_revenue'].replace(',', '')),
    }

    # This list spans every in-progress stage: 'approval' (plan request
    # awaiting staff sign-off), 'awaiting_payment' (approved, member hasn't
    # chosen a method yet), 'verify_cash' (Cash — staff's job) and
    # 'verify_gcash' (GCash — admin's job). Admin sees all four for
    # visibility but can only act on 'verify_gcash'.
    pending_payments_rows = (
        Payment.query
        .options(joinedload(Payment.member), joinedload(Payment.plan))
        .filter(Payment.status.in_(['pending', 'approved']))
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
        'stage': _payment_stage(p),
        'staff_viewed': p.staff_viewed,
    } for p in pending_payments_rows]

    # ── Payment history — approved only, same as staff's Recently Processed.
    #    A declined request just disappears from the list; it only shows up
    #    again once the member submits a new request and that one is approved. ──
    payment_history_rows = (
        Payment.query
        .options(joinedload(Payment.member), joinedload(Payment.plan))
        .filter(Payment.status == 'verified')
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

    # Admin sees every announcement (including unpublished ones) so it can
    # manage/unpublish/delete them, not just the ones currently live.
    announcements = Announcement.query.order_by(Announcement.created_at.desc()).all()

    coaches_data = _get_coaches_data()

    return render_template(
        'admin-dashboard.html',
        members=members,
        stats=stats,
        pending_payments=pending_payments,
        payment_history=payment_history,
        attendance_today=attendance_today,
        attendance_calendar=attendance_calendar,
        announcements=announcements,
        current_user=User.query.get(session['user_id']),
        gcash_settings=_get_gym_settings(),
        coaches=coaches_data,
        coach_days=VALID_COACH_DAYS,
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
        {'name': 'Daily',   'duration_days': 1,   'price': 100.0,
         'description': 'Perfect for a casual visit — walk in, train, and go, no commitment required.',
         'inclusions': 'Gym Equipment Access\nGym Services', 'sort_order': 1},
        {'name': 'Weekly',  'duration_days': 14,  'price': 450.0,
         'description': 'A short-term option for building a routine — full access for a full week.',
         'inclusions': 'Gym Equipment Access\nGym Services', 'sort_order': 2},
        {'name': 'Monthly', 'duration_days': 30,  'price': 900.0,
         'description': 'Our most popular plan — unlimited visits with trainer support to keep you on track.',
         'inclusions': 'Gym Equipment Access\nGym Services', 'sort_order': 3},
        {'name': 'Yearly',  'duration_days': 365, 'price': 7000.0,
         'description': 'Full coaching support — a personal trainer and nutrition plan built around your goals.',
         'inclusions': 'Gym Equipment Access\nGym Services', 'sort_order': 4},
    ]
    for p in defaults:
        existing = MembershipPlan.query.filter_by(name=p['name']).first()
        if existing is None:
            db.session.add(MembershipPlan(
                name=p['name'],
                duration_days=p['duration_days'],
                price=p['price'],
                description=p['description'],
                inclusions=p['inclusions'],
                sort_order=p['sort_order'],
            ))
        else:
            # Keep an already-seeded row in sync if the defaults above change
            # (e.g. Weekly's duration moving from 7 to 14 days). Content
            # fields (description/inclusions/image) are left alone once set,
            # so staff/admin edits made from the dashboard aren't overwritten.
            existing.duration_days = p['duration_days']
            existing.price         = p['price']
            if existing.description is None:
                existing.description = p['description']
            if existing.inclusions is None:
                existing.inclusions = p['inclusions']
            if not existing.sort_order:
                existing.sort_order = p['sort_order']
    db.session.commit()


def seed_default_coaches():
    defaults = [
        {'name': 'Ronel Samar',        'available_days': 'Mon,Wed,Fri', 'max_members': 10},
        {'name': 'Jonathan Natividad', 'available_days': 'Tue,Thu,Sat', 'max_members': 10},
    ]
    for c in defaults:
        if Coach.query.filter_by(name=c['name']).first() is None:
            db.session.add(Coach(
                name=c['name'],
                available_days=c['available_days'],
                max_members=c['max_members'],
            ))
    db.session.commit()


def seed_default_equipment():
    defaults = [
        {'name': 'Weight Area',          'image_path': 'images/facility-weight.png',      'sort_order': 1, 'category': 'Strengthening', 'icon': '💪'},
        {'name': 'Cardio Area',          'image_path': 'images/facility-cardio.png',      'sort_order': 2, 'category': 'Cardio Zone',    'icon': '🏃'},
        {'name': 'Functional Training',  'image_path': 'images/facility-functional.png',  'sort_order': 3, 'category': 'Functional Training', 'icon': '🤸'},
        {'name': 'Reception',            'image_path': 'images/facility-reception.png',   'sort_order': 4, 'category': 'General',        'icon': '🛎️'},
    ]
    if GymEquipment.query.count() == 0:
        for e in defaults:
            db.session.add(GymEquipment(
                name=e['name'], image_path=e['image_path'], sort_order=e['sort_order'],
                category=e['category'], icon=e['icon'],
                is_facility=True,  # facility-zone photo, not a real machine — see model docstring
            ))
        db.session.commit()


def _run_startup_migrations():
    """db.create_all() only creates brand-new tables — it won't add columns
    to a table that already exists from a previous run. This adds any
    columns introduced after the database was first created, so existing
    installs don't need a manual ALTER TABLE."""
    migrations = [
        ('payments', 'requested_start_date', "ALTER TABLE payments ADD COLUMN requested_start_date DATE NULL"),
        ('payments', 'notified', "ALTER TABLE payments ADD COLUMN notified TINYINT(1) NOT NULL DEFAULT 0"),
        ('payments', 'staff_viewed', "ALTER TABLE payments ADD COLUMN staff_viewed TINYINT(1) NOT NULL DEFAULT 0"),
        ('membership_plans', 'description', "ALTER TABLE membership_plans ADD COLUMN description TEXT NULL"),
        ('membership_plans', 'image_path',  "ALTER TABLE membership_plans ADD COLUMN image_path VARCHAR(255) NULL"),
        ('membership_plans', 'inclusions',  "ALTER TABLE membership_plans ADD COLUMN inclusions TEXT NULL"),
        ('membership_plans', 'sort_order',  "ALTER TABLE membership_plans ADD COLUMN sort_order INT NOT NULL DEFAULT 0"),
        ('gym_services',   'category', "ALTER TABLE gym_services ADD COLUMN category VARCHAR(60) NULL"),
        ('gym_services',   'icon',     "ALTER TABLE gym_services ADD COLUMN icon VARCHAR(8) NULL"),
        ('gym_equipment',  'category', "ALTER TABLE gym_equipment ADD COLUMN category VARCHAR(60) NULL"),
        ('gym_equipment',  'icon',     "ALTER TABLE gym_equipment ADD COLUMN icon VARCHAR(8) NULL"),
        ('gym_equipment',  'is_facility', "ALTER TABLE gym_equipment ADD COLUMN is_facility TINYINT(1) NOT NULL DEFAULT 0"),
        ('users', 'last_seen_announcements_at', "ALTER TABLE users ADD COLUMN last_seen_announcements_at DATETIME NULL"),
        ('coaches', 'fee', "ALTER TABLE coaches ADD COLUMN fee DECIMAL(10,2) NOT NULL DEFAULT 0"),
    ]
    with db.engine.connect() as conn:
        for table, column, ddl in migrations:
            result = conn.execute(text(f"SHOW COLUMNS FROM {table} LIKE '{column}'"))
            if result.fetchone() is None:
                conn.execute(text(ddl))
                conn.commit()
                print(f"Migration: added {table}.{column}")

        # ── Indexes on columns that are filtered/sorted/joined on constantly
        #    (payment status, plan lookups, attendance date-range queries).
        #    Missing indexes here were forcing full table scans on every
        #    dashboard load as the amount of data grew — this is what was
        #    making the system feel slower and slower over time. ──
        index_migrations = [
            ('payments',   'idx_payments_plan_id',        'payments',   'plan_id'),
            ('payments',   'idx_payments_status',          'payments',   'status'),
            ('payments',   'idx_payments_recorded_by_id',  'payments',   'recorded_by_id'),
            ('payments',   'idx_payments_paid_at',         'payments',   'paid_at'),
            ('memberships','idx_memberships_plan_id',      'memberships','plan_id'),
            ('attendance', 'idx_attendance_check_in',      'attendance', 'check_in'),
        ]
        for table, index_name, idx_table, column in index_migrations:
            result = conn.execute(text(f"SHOW INDEX FROM {table} WHERE Key_name = '{index_name}'"))
            if result.fetchone() is None:
                conn.execute(text(f"CREATE INDEX {index_name} ON {idx_table} ({column})"))
                conn.commit()
                print(f"Migration: added index {index_name} on {table}.{column}")


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _run_startup_migrations()
        seed_default_plans()
        seed_default_coaches()
        seed_default_equipment()
        seed_default_users()
        _get_gym_settings()  # ensures the GCash settings row exists on first boot
        print("Tables created, plans and demo users seeded!")
    # threaded=True lets the dev server handle multiple requests at once
    # instead of one at a time. Without it, every asset a page needs (CSS,
    # JS, fonts, images) gets served sequentially even though the browser
    # requests them all in parallel — which is what was making a simple
    # page refresh feel slow. Not related to debug mode; safe to keep on
    # even after you turn debug off for a real deployment.
    app.run(debug=True, threaded=True)