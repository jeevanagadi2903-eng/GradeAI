from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    students = db.relationship('Student', backref='school', lazy=True)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    class_name = db.Column(db.String(10), nullable=False)
    section = db.Column(db.String(5), nullable=False)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'), nullable=False)
    results = db.relationship('Result', backref='student', lazy=True)

class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    school_id = db.Column(db.Integer, nullable=False)
    exam_type = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(50), nullable=False)
    class_name = db.Column(db.String(10), nullable=False)
    section = db.Column(db.String(5), nullable=False)
    student_wrote = db.Column(db.Text)
    score = db.Column(db.String(20))
    feedback = db.Column(db.Text)
    date = db.Column(db.DateTime, default=datetime.utcnow)

class AnswerKey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, nullable=False)
    class_name = db.Column(db.String(10), nullable=False)
    section = db.Column(db.String(5), nullable=False)
    exam_type = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(50), nullable=False)
    answer_key = db.Column(db.Text, nullable=False)
    total_marks = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)