from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from celery import Celery
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv


load_dotenv()  


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///emails.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.example.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME', 'your_email@example.com')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD', 'your_email_password')
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'

db = SQLAlchemy(app)
mail = Mail(app)

def make_celery(app):
    celery = Celery(app.import_name, broker=app.config['CELERY_BROKER_URL'])
    celery.conf.update(app.config)
    celery.Task = ContextTask
    return celery

class ContextTask(Celery.Task):
    def __call__(self, *args, **kwargs):
        with app.app_context():
            return self.run(*args, **kwargs)

celery = make_celery(app)

class ScheduledEmail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipient = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    schedule_time = db.Column(db.DateTime, nullable=False)
    recurrence = db.Column(db.String(50), nullable=True)
    recurrence_detail = db.Column(db.String(50), nullable=True)  
    status = db.Column(db.String(50), default='scheduled')
    attachments = db.Column(db.PickleType, nullable=True)

    def serialize(self):
        return {
            'id': self.id,
            'recipient': self.recipient,
            'subject': self.subject,
            'body': self.body,
            'schedule_time': self.schedule_time.isoformat(),
            'recurrence': self.recurrence,
            'recurrence_detail': self.recurrence_detail,
            'status': self.status,
            'attachments': self.attachments
        }

db.create_all()

@app.route('/schedule-email', methods=['POST'])
def schedule_email():
    try:
        data = request.get_json()
        schedule_time = datetime.strptime(data['schedule_time'], '%Y-%m-%dT%H:%M:%S')
        new_email = ScheduledEmail(
            recipient=data['recipient'],
            subject=data['subject'],
            body=data['body'],
            schedule_time=schedule_time,
            recurrence=data.get('recurrence'),
            recurrence_detail=data.get('recurrence_detail'),
            attachments=data.get('attachments')
        )
        db.session.add(new_email)
        db.session.commit()
        schedule_email_task.apply_async((new_email.id,), eta=schedule_time)
        return jsonify({'id': new_email.id}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/scheduled-emails', methods=['GET'])
def get_scheduled_emails():
    emails = ScheduledEmail.query.all()
    return jsonify([email.serialize() for email in emails]), 200

@app.route('/scheduled-emails/<int:id>', methods=['GET'])
def get_scheduled_email(id):
    email = ScheduledEmail.query.get_or_404(id)
    return jsonify(email.serialize()), 200

@app.route('/scheduled-emails/<int:id>', methods=['DELETE'])
def cancel_scheduled_email(id):
    email = ScheduledEmail.query.get_or_404(id)
    email.status = 'cancelled'
    db.session.commit()
    return '', 204

@celery.task
def schedule_email_task(email_id):
    email = ScheduledEmail.query.get(email_id)
    if email and email.status == 'scheduled':
        with app.app_context():
            msg = Message(
                email.subject,
                recipients=[email.recipient],
                body=email.body
            )
            if email.attachments:
                for attachment in email.attachments:
                    with app.open_resource(attachment) as fp:
                        msg.attach(os.path.basename(attachment), "application/octet-stream", fp.read())
            mail.send(msg)
            email.status = 'sent'
            db.session.commit()
        if email.recurrence:
            next_schedule_time = calculate_next_schedule(email.schedule_time, email.recurrence, email.recurrence_detail)
            new_email = ScheduledEmail(
                recipient=email.recipient,
                subject=email.subject,
                body=email.body,
                schedule_time=next_schedule_time,
                recurrence=email.recurrence,
                recurrence_detail=email.recurrence_detail,
                attachments=email.attachments
            )
            db.session.add(new_email)
            db.session.commit()
            schedule_email_task.apply_async((new_email.id,), eta=next_schedule_time)

def calculate_next_schedule(current_time, recurrence, recurrence_detail):
    if recurrence == 'daily':
        return current_time + timedelta(days=1)
    elif recurrence == 'weekly':
        return current_time + timedelta(weeks=1)
    elif recurrence == 'monthly':
        return current_time + timedelta(days=30)  
    elif recurrence == 'quarterly':
        return current_time + timedelta(days=90)  
    return current_time

if __name__ == '__main__':
    app.run(debug=True)

