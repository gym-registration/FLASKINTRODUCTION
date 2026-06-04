import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy

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
    username = db.Column(db.VARCHAR(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False)

class MembershipPlan(db.Model):
    __tablename__ = 'membership_plans'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    price = db.Column(db.Float, nullable=False)

class Membership(db.Model):
    __tablename__ = 'members'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    membership_type = db.Column(db.String(50), nullable=False)
    
class Payment(db.Model):
    __tablename__ = 'payment'
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_date = db.Column(db.DateTime, nullable=False)

class Attendance(db.Model):
    __tablename__ = 'attendance'
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    check_in_time = db.Column(db.DateTime, nullable=False)
    check_out_time = db.Column(db.DateTime)

class BodyGoal(db.Model):
    __tablename__ = 'body_goals'
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    goal_description = db.Column(db.String(255), nullable=False)

class Announcement(db.Model):
    __tablename__ = 'announcements'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)


@app.route('/')
@app.route('/trmem')
@app.route('/trmem.html')
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Please enter both email and password.', 'error')
            return render_template('trmem.html')

        user = User.query.filter_by(email=email).first()
        if user is None or user.password != password:
            flash('Email or password is incorrect.', 'error')
            return render_template('trmem.html')

        session['user_id'] = user.id
        session['role'] = user.role
        session['email'] = user.email
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

def seed_default_users():
    defaults = [
        {
            'first_name': 'Administrator',
            'last_name': 'User',
            'email': 'admin@powergym.com',
            'username': 'admin',
            'password': 'admin123',
            'role': 'admin'
        },
        {
            'first_name': 'Staff',
            'last_name': 'Member',
            'email': 'staff@powergym.com',
            'username': 'staff',
            'password': 'staff123',
            'role': 'staff'
        },
        {
            'first_name': 'Maria',
            'last_name': 'Santos',
            'email': 'maria@email.com',
            'username': 'maria',
            'password': 'member123',
            'role': 'member'
        }
    ]
    for user_data in defaults:
        if User.query.filter_by(email=user_data['email']).first() is None:
            db.session.add(User(
                first_name=user_data['first_name'],
                last_name=user_data['last_name'],
                email=user_data['email'],
                phone=None,
                birthday=None,
                username=user_data['username'],
                password=user_data['password'],
                role=user_data['role']
            ))
    db.session.commit()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_default_users()
        print("Tables created and demo users seeded!")
    app.run(debug=True)