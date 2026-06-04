import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone


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
        if user is None or user.password != password:
            flash('Email or password is incorrect.', 'error')
            return render_template('trmem.html')

        session['user_id'] = user.id
        session['role']    = user.role
        session['email']   = user.email
        session['name']    = f"{user.first_name} {user.last_name}"
        return redirect(url_for(user.role))

    return render_template('trmem.html')


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
    return render_template('staff-dashboard.html')


@app.route('/admin')
def admin():
    if 'role' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'admin':
        return redirect(url_for(session.get('role', 'login')))
    return render_template('admin-dashboard.html')


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
                password=u['password'],
                role=u['role'],
            ))
    db.session.commit()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_default_users()
        print("Tables created and demo users seeded!")
    app.run(debug=True)
