import os
import secrets
import string
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone, date, timedelta


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_secret_key')

app.config['SQLALCHEMY_DATABASE_URI'] = ('mysql+pymysql://root:admin123@127.0.0.1:3306/gym_db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True, index=True)
    phone = db.Column(db.String(20), nullable=True)
    birthday = db.Column(db.Date, nullable=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default='member')
    status = db.Column(db.String(15), nullable=False, default='pending')
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    membership       = db.relationship('Membership', back_populates='member', uselist=False, cascade='all, delete-orphan')
    payments         = db.relationship('Payment', foreign_keys='Payment.member_id', back_populates='member', cascade='all, delete-orphan')
    recorded_payments= db.relationship('Payment', foreign_keys='Payment.recorded_by_id', back_populates='recorded_by')
    attendance       = db.relationship('Attendance', foreign_keys='Attendance.member_id', back_populates='member', cascade='all, delete-orphan')
    body_goals       = db.relationship('BodyGoal', back_populates='member', cascade='all, delete-orphan')

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
    status           = db.Column(db.String(10), nullable=False, default='pending')
    recorded_by_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    notes            = db.Column(db.Text, nullable=True)
    paid_at          = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    verified_at      = db.Column(db.DateTime, nullable=True)

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
            flash('Email or password is incorrect.', 'error')
            return render_template('trmem.html')

        session['user_id'] = user.id
        session['role']    = user.role
        session['email']   = user.email
        session['name']    = f"{user.first_name} {user.last_name}"
        return redirect(url_for(user.role))

    return render_template('trmem.html')


@app.route('/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or request.form

    first_name = (data.get('first_name') or '').strip()
    last_name  = (data.get('last_name')  or '').strip()
    email      = (data.get('email')      or '').strip().lower()
    phone      = (data.get('phone')      or '').strip()
    birthday   = (data.get('birthday')   or '').strip()
    password   = data.get('password')    or ''
    plan_key   = (data.get('plan')       or '').strip().lower()
    pay_method = (data.get('payment_method') or '').strip().lower()

    # ── Validation ──
    if not first_name or not last_name or not email or not password:
        return jsonify(success=False, error='Please fill in all required fields.'), 400

    if len(password) < 8:
        return jsonify(success=False, error='Password must be at least 8 characters.'), 400

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
        last_name=last_name,
        email=email,
        phone=phone or None,
        birthday=birthday_date,
        password=generate_password_hash(password),
        role='member',
        status='pending',
    )
    db.session.add(new_user)
    db.session.flush()  # get new_user.id before commit

    # ── Attach the selected membership plan, if valid ──
    plan_name_map = {
        'daily': 'Daily',
        'monthly': 'Monthly',
        'quarterly': 'Quarterly',
        'annual': 'Annual',
    }
    plan = None
    if plan_key in plan_name_map:
        plan = MembershipPlan.query.filter_by(name=plan_name_map[plan_key]).first()

    if plan is not None:
        start = date.today()
        expiry = start + timedelta(days=plan.duration_days)
        new_membership = Membership(
            member_id=new_user.id,
            plan_id=plan.id,
            start_date=start,
            expiry_date=expiry,
            status='pending',
        )
        db.session.add(new_membership)

    db.session.commit()

    return jsonify(success=True, message='Registration submitted! Awaiting verification.')


def _generate_temp_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


@app.route('/admin/add-member', methods=['POST'])
def admin_add_member():
    if session.get('role') != 'admin':
        return jsonify(success=False, error='Unauthorized.'), 403

    data = request.get_json(silent=True) or request.form

    first_name = (data.get('first_name') or '').strip()
    last_name  = (data.get('last_name')  or '').strip()
    email      = (data.get('email')      or '').strip().lower()
    phone      = (data.get('phone')      or '').strip()
    plan_name  = (data.get('plan')       or '').strip()

    if not first_name or not last_name or not email:
        return jsonify(success=False, error='Please fill in first name, last name, and email.'), 400

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
        last_name=last_name,
        email=email,
        phone=phone or None,
        password=generate_password_hash(temp_password),
        role='member',
        status='active',
    )
    db.session.add(new_user)
    db.session.flush()

    start = date.today()
    expiry = start + timedelta(days=plan.duration_days)
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
            'name': f'{first_name} {last_name}',
            'email': email,
            'plan': plan.name,
            'expiry': expiry.strftime('%b %d, %Y'),
            'temp_password': temp_password,
        }
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
    today = date.today()
    membership = Membership.query.filter_by(member_id=member.id).first()
    if membership is None:
        membership = Membership(member_id=member.id, plan_id=plan.id, start_date=today,
                                 expiry_date=today, status='active')
        db.session.add(membership)

    base_date = membership.expiry_date if membership.expiry_date and membership.expiry_date > today else today
    membership.plan_id     = plan.id
    membership.expiry_date = base_date + timedelta(days=plan.duration_days)
    membership.status      = 'active'
    if member.status != 'active':
        member.status = 'active'

    db.session.commit()

    return jsonify(
        success=True,
        message='Payment recorded successfully.',
        payment={
            'member_name': f'{member.first_name} {member.last_name}',
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
        member_name=f'{member.first_name} {member.last_name}',
        time=now.strftime('%I:%M %p').lstrip('0'),
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
        member_name=f'{member.first_name} {member.last_name}',
        time=now.strftime('%I:%M %p').lstrip('0'),
        duration=duration_text,
    )


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/member')
def member():
    if 'role' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'member':
        return redirect(url_for(session.get('role', 'login')))
    return render_template('member-dashboard.html')


@app.route('/staff')
def staff():
    if 'role' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'staff':
        return redirect(url_for(session.get('role', 'login')))

    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    today_end   = datetime.combine(today, datetime.max.time())

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
        attendance_today.append({
            'member_name': f'{a.member.first_name} {a.member.last_name}',
            'check_in': a.check_in.strftime('%I:%M %p').lstrip('0'),
            'check_out': a.check_out.strftime('%I:%M %p').lstrip('0') if a.check_out else '—',
            'duration': duration_text,
            'status': 'Out' if a.check_out else 'In',
        })

    members = _get_members_with_plans()
    active_members   = [m for m in members if m['status'] == 'Active']
    pending_payments = [m for m in members if m['status'] == 'Pending']
    expiring_soon    = [
        m for m in members
        if m['status'] == 'Active' and m['expiry_date'] and 0 <= (m['expiry_date'] - today).days <= 7
    ]

    stats = {
        'checkins_today':   len(attendance_today),
        'active_members':   len(active_members),
        'pending_payments': len(pending_payments),
        'expiring_soon':    len(expiring_soon),
    }

    return render_template(
        'staff-dashboard.html',
        attendance_today=attendance_today,
        members=members,
        expiring_soon=expiring_soon,
        stats=stats,
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

    today = date.today()
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
            'name': f'{user.first_name} {user.last_name}',
            'email': user.email,
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
    return render_template('admin-dashboard.html', members=members)


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
        {'name': 'Daily',     'duration_days': 1,   'price': 80.0},
        {'name': 'Monthly',   'duration_days': 30,  'price': 999.0},
        {'name': 'Quarterly', 'duration_days': 90,  'price': 2499.0},
        {'name': 'Annual',    'duration_days': 365, 'price': 7999.0},
    ]
    for p in defaults:
        if MembershipPlan.query.filter_by(name=p['name']).first() is None:
            db.session.add(MembershipPlan(
                name=p['name'],
                duration_days=p['duration_days'],
                price=p['price'],
            ))
    db.session.commit()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_default_plans()
        seed_default_users()
        print("Tables created, plans and demo users seeded!")
    app.run(debug=True)