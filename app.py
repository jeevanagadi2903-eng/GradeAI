from database import db, School, Student, Result, AnswerKey
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from checker import grade_answer
from werkzeug.security import generate_password_hash, check_password_hash
import os
import time
import threading
from dotenv import load_dotenv
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

load_dotenv()

app = Flask(__name__)
app.secret_key = 'gradeai-secret-2024'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///gradeai.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

with app.app_context():
    db.create_all()

# ── Folder Watcher ──────────────────────────────────────────
WATCH_FOLDER = os.path.join(os.path.expanduser("~"), "Pictures", "Scanned Documents")
os.makedirs(WATCH_FOLDER, exist_ok=True)

scanned_queue = []
queue_lock = threading.Lock()

class ScanWatcher(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = event.src_path.lower()
        if path.endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.pdf')):
            time.sleep(1)
            with queue_lock:
                if event.src_path not in scanned_queue:
                    scanned_queue.append(event.src_path)
                    print(f"📄 New scan detected: {os.path.basename(event.src_path)}")

def start_watcher():
    observer = Observer()
    observer.schedule(ScanWatcher(), WATCH_FOLDER, recursive=False)
    observer.start()
    print(f"👀 Watching: {WATCH_FOLDER}")

watcher_thread = threading.Thread(target=start_watcher, daemon=True)
watcher_thread.start()

# ── Auth ────────────────────────────────────────────────────
@app.route('/')
def index():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        school = School.query.filter_by(email=email).first()
        if school and check_password_hash(school.password, password):
            session['school_id'] = school.id
            session['school_name'] = school.name
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid email or password')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        if School.query.filter_by(email=email).first():
            return render_template('signup.html', error='Email already registered')
        hashed = generate_password_hash(password)
        school = School(name=name, email=email, password=hashed)
        db.session.add(school)
        db.session.commit()
        session['school_id'] = school.id
        session['school_name'] = school.name
        return redirect(url_for('dashboard'))
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── Dashboard ───────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html', school_name=session['school_name'])

@app.route('/dashboard-stats')
def dashboard_stats():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    from sqlalchemy import func

    tests = db.session.query(
        Result.class_name, Result.section, Result.exam_type, Result.subject,
        func.count(Result.id).label('total_students'),
        func.max(Result.date).label('latest_date')
    ).filter_by(school_id=session['school_id'])\
     .group_by(Result.class_name, Result.section, Result.exam_type, Result.subject)\
     .order_by(func.max(Result.date).desc())\
     .limit(10).all()

    result_data = []
    for test in tests:
        results = Result.query.filter_by(
            school_id=session['school_id'],
            class_name=test.class_name, section=test.section,
            exam_type=test.exam_type, subject=test.subject
        ).all()

        scored = []
        for r in results:
            student = Student.query.get(r.student_id)
            if not student or not r.score:
                continue
            try:
                num = float(r.score.split('/')[0].strip())
                total = float(r.score.split('/')[1].strip())
                scored.append({
                    'name': student.name, 'student_id': student.student_id,
                    'score': r.score, 'numeric': num, 'total': total,
                    'percent': round((num/total)*100)
                })
            except:
                continue

        if not scored:
            continue

        scored.sort(key=lambda x: x['numeric'], reverse=True)
        avg = round(sum(s['numeric'] for s in scored) / len(scored), 1)

        result_data.append({
            'class_name': test.class_name, 'section': test.section,
            'exam_type': test.exam_type, 'subject': test.subject,
            'date': test.latest_date.strftime('%d %b %Y') if test.latest_date else '',
            'total_students': len(scored),
            'average': avg, 'total_marks': scored[0]['total'],
            'top3': scored[:3], 'rankings': scored
        })

    return jsonify(result_data)

# ── Students ────────────────────────────────────────────────
@app.route('/students')
def students_page():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    students = Student.query.filter_by(school_id=session['school_id'])\
        .order_by(Student.class_name, Student.section, Student.name).all()
    return render_template('students.html', students=students, school_name=session['school_name'])

@app.route('/add-student', methods=['POST'])
def add_student():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    student = Student(
        student_id=data['student_id'], name=data['name'],
        class_name=data['class_name'], section=data['section'],
        school_id=session['school_id']
    )
    db.session.add(student)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/get-students')
def get_students():
    if 'school_id' not in session:
        return jsonify([])
    students = Student.query.filter_by(
        school_id=session['school_id'],
        class_name=request.args.get('class'),
        section=request.args.get('section')
    ).all()
    return jsonify([{'id': s.student_id, 'name': s.name} for s in students])

@app.route('/import-students', methods=['POST'])
def import_students():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    file = request.files['csv_file']
    filename = file.filename.lower()
    import pandas as pd
    import io
    content = file.read()
    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(content))
        else:
            df = pd.read_excel(io.BytesIO(content))
        df.columns = [c.strip().lower() for c in df.columns]
        added, skipped = 0, 0
        for _, row in df.iterrows():
            sid = str(row.get('student_id', '')).strip()
            name = str(row.get('name', '')).strip()
            cls = str(row.get('class', '')).strip()
            sec = str(row.get('section', '')).strip().upper()
            if not sid or not name or not cls or not sec:
                skipped += 1
                continue
            exists = Student.query.filter_by(student_id=sid, school_id=session['school_id']).first()
            if exists:
                exists.name = name
                exists.class_name = cls
                exists.section = sec
                skipped += 1
            else:
                db.session.add(Student(student_id=sid, name=name, class_name=cls, section=sec, school_id=session['school_id']))
                added += 1
        db.session.commit()
        return jsonify({'success': True, 'added': added, 'skipped': skipped})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/edit-student', methods=['POST'])
def edit_student():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    student = Student.query.filter_by(id=data['id'], school_id=session['school_id']).first()
    if not student:
        return jsonify({'error': 'Not found'}), 404
    student.name = data['name']
    student.student_id = data['student_id']
    student.class_name = data['class_name']
    student.section = data['section']
    db.session.commit()
    return jsonify({'success': True})

@app.route('/delete-student', methods=['POST'])
def delete_student():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    student = Student.query.filter_by(id=data['id'], school_id=session['school_id']).first()
    if student:
        Result.query.filter_by(student_id=student.id).delete()
        db.session.delete(student)
        db.session.commit()
    return jsonify({'success': True})

@app.route('/promote-class', methods=['POST'])
def promote_class():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    students = Student.query.filter_by(
        school_id=session['school_id'],
        class_name=str(data['from_class']),
        section=data['section']
    ).all()
    count = 0
    for s in students:
        s.class_name = str(data['to_class'])
        count += 1
    db.session.commit()
    return jsonify({'success': True, 'promoted': count})

# ── Grading ─────────────────────────────────────────────────
@app.route('/grade-page')
def grade_page():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    class_name = request.args.get('class')
    section = request.args.get('section')
    exam_type = request.args.get('exam_type')
    subject = request.args.get('subject')
    students = Student.query.filter_by(
        school_id=session['school_id'],
        class_name=class_name, section=section
    ).all()
    return render_template('grade.html',
        class_name=class_name, section=section,
        exam_type=exam_type, subject=subject, students=students)

@app.route('/grade', methods=['POST'])
def grade():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    image = request.files['image']
    answer_key = request.form['answer_key']
    total_marks = request.form['total_marks']
    student_id = request.form['student_id']
    exam_type = request.form['exam_type']
    subject = request.form['subject']
    class_name = request.form['class_name']
    section = request.form['section']

    image_path = os.path.join(app.config['UPLOAD_FOLDER'], image.filename)
    image.save(image_path)
    result_text = grade_answer(image_path, answer_key, total_marks)

    lines = result_text.split('\n')
    student_wrote, score, feedback = '', '', ''
    for line in lines:
        if line.startswith('STUDENT WROTE:'): student_wrote = line.replace('STUDENT WROTE:', '').strip()
        elif line.startswith('SCORE:'): score = line.replace('SCORE:', '').strip()
        elif line.startswith('FEEDBACK:'): feedback = line.replace('FEEDBACK:', '').strip()

    student = Student.query.filter_by(student_id=student_id, school_id=session['school_id']).first()
    if student:
        result = Result(
            student_id=student.id, school_id=session['school_id'],
            exam_type=exam_type, subject=subject,
            class_name=class_name, section=section,
            student_wrote=student_wrote, score=score, feedback=feedback
        )
        db.session.add(result)
        db.session.commit()

    return jsonify({'result': result_text})

@app.route('/bulk-grade-page')
def bulk_grade_page():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    class_name = request.args.get('class')
    section = request.args.get('section')
    exam_type = request.args.get('exam_type')
    subject = request.args.get('subject')
    students = Student.query.filter_by(
        school_id=session['school_id'],
        class_name=class_name, section=section
    ).all()
    return render_template('bulk_grade.html',
        class_name=class_name, section=section,
        exam_type=exam_type, subject=subject, students=students)

@app.route('/bulk-grade', methods=['POST'])
def bulk_grade():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    answer_key_file = request.files.get('answer_key_pdf')
    student_files = request.files.getlist('student_pdfs')
    student_ids = request.form.getlist('student_ids')
    total_marks = request.form.get('total_marks')
    exam_type = request.form.get('exam_type')
    subject = request.form.get('subject')
    class_name = request.form.get('class_name')
    section = request.form.get('section')

    ak_path = os.path.join(app.config['UPLOAD_FOLDER'], 'answer_key.pdf')
    answer_key_file.save(ak_path)

    from checker import extract_text_from_pdf, grade_answer_pdf
    answer_key_text = extract_text_from_pdf(ak_path)

    results = []
    for i, student_file in enumerate(student_files):
        student_id = student_ids[i] if i < len(student_ids) else f'STU{i}'
        student_path = os.path.join(app.config['UPLOAD_FOLDER'], f'student_{i}_{student_file.filename}')
        student_file.save(student_path)

        try:
            result_text = grade_answer_pdf(student_path, answer_key_text, total_marks)
        except Exception as e:
            result_text = f"Error grading: {str(e)}"

        total_score = ''
        for line in result_text.split('\n'):
            if line.startswith('TOTAL:'):
                total_score = line.replace('TOTAL:', '').strip()
                break

        student = Student.query.filter_by(student_id=student_id, school_id=session['school_id']).first()
        if student:
            result = Result(
                student_id=student.id, school_id=session['school_id'],
                exam_type=exam_type, subject=subject,
                class_name=class_name, section=section,
                student_wrote='PDF submission', score=total_score, feedback=result_text
            )
            db.session.add(result)
            db.session.commit()

        results.append({'student_id': student_id, 'result': result_text, 'score': total_score})

    return jsonify({'results': results})

# ── Live Scan ────────────────────────────────────────────────
@app.route('/live-scan-page')
def live_scan_page():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    class_name = request.args.get('class', '')
    section = request.args.get('section', '')
    exam_type = request.args.get('exam_type', '')
    subject = request.args.get('subject', '')
    students = Student.query.filter_by(
        school_id=session['school_id'],
        class_name=class_name, section=section
    ).order_by(Student.name).all()
    return render_template('live_scan.html',
        class_name=class_name, section=section,
        exam_type=exam_type, subject=subject, students=students)

@app.route('/scan-folder')
def get_scan_folder():
    return jsonify({'folder': WATCH_FOLDER})

@app.route('/scan-queue')
def scan_queue():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    with queue_lock:
        files = [{'path': p, 'name': os.path.basename(p)} for p in scanned_queue if os.path.exists(p)]
    return jsonify({'files': files, 'folder': WATCH_FOLDER})

@app.route('/pop-scan-queue', methods=['POST'])
def pop_scan_queue():
    data = request.json
    path = data.get('path')
    with queue_lock:
        if path in scanned_queue:
            scanned_queue.remove(path)
    return jsonify({'success': True})

@app.route('/clear-scan-queue', methods=['POST'])
def clear_scan_queue():
    with queue_lock:
        scanned_queue.clear()
    return jsonify({'success': True})

@app.route('/grade-live-scan', methods=['POST'])
def grade_live_scan():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    data = request.json
    image_path = data.get('image_path')
    answer_key = data.get('answer_key')
    total_marks = data.get('total_marks')
    student_id = data.get('student_id')
    exam_type = data.get('exam_type')
    subject = data.get('subject')
    class_name = data.get('class_name')
    section = data.get('section')

    if not os.path.exists(image_path):
        return jsonify({'error': 'Image file not found'}), 404

    result_text = grade_answer(image_path, answer_key, total_marks)

    total_score = ''
    for line in result_text.split('\n'):
        if 'TOTAL:' in line or 'SCORE:' in line:
            total_score = line.split(':', 1)[1].strip()
            break

    student = Student.query.filter_by(student_id=student_id, school_id=session['school_id']).first()
    if student:
        result = Result(
            student_id=student.id, school_id=session['school_id'],
            exam_type=exam_type, subject=subject,
            class_name=class_name, section=section,
            student_wrote='Live scan', score=total_score, feedback=result_text
        )
        db.session.add(result)
        db.session.commit()

    return jsonify({'success': True, 'score': total_score, 'result': result_text})

# ── History & Answer Keys ────────────────────────────────────
@app.route('/history')
def history():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    results = Result.query.filter_by(school_id=session['school_id'])\
        .order_by(Result.date.desc()).limit(50).all()
    data = []
    for r in results:
        student = Student.query.get(r.student_id)
        data.append({
            'student_name': student.name if student else 'Unknown',
            'student_id': student.student_id if student else '-',
            'class': r.class_name, 'section': r.section,
            'exam_type': r.exam_type, 'subject': r.subject,
            'score': r.score, 'feedback': r.feedback,
            'date': r.date.strftime('%d %b %Y')
        })
    return render_template('history.html', results=data, school_name=session['school_name'])

@app.route('/save-answer-key', methods=['POST'])
def save_answer_key():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    existing = AnswerKey.query.filter_by(
        school_id=session['school_id'],
        class_name=data['class_name'], section=data['section'],
        exam_type=data['exam_type'], subject=data['subject']
    ).first()
    if existing:
        existing.answer_key = data['answer_key']
        existing.total_marks = data['total_marks']
    else:
        db.session.add(AnswerKey(
            school_id=session['school_id'],
            class_name=data['class_name'], section=data['section'],
            exam_type=data['exam_type'], subject=data['subject'],
            answer_key=data['answer_key'], total_marks=data['total_marks']
        ))
    db.session.commit()
    return jsonify({'success': True})

@app.route('/get-answer-key')
def get_answer_key():
    if 'school_id' not in session:
        return jsonify({}), 401
    key = AnswerKey.query.filter_by(
        school_id=session['school_id'],
        class_name=request.args.get('class'),
        section=request.args.get('section'),
        exam_type=request.args.get('exam_type'),
        subject=request.args.get('subject')
    ).first()
    if key:
        return jsonify({'answer_key': key.answer_key, 'total_marks': key.total_marks})
    return jsonify({})

@app.route('/list-answer-keys')
def list_answer_keys():
    if 'school_id' not in session:
        return jsonify([])
    keys = AnswerKey.query.filter_by(school_id=session['school_id'])\
        .order_by(AnswerKey.created_at.desc()).all()
    return jsonify([{
        'id': k.id, 'class_name': k.class_name, 'section': k.section,
        'exam_type': k.exam_type, 'subject': k.subject, 'total_marks': k.total_marks
    } for k in keys])

@app.route('/extract-pdf-text', methods=['POST'])
def extract_pdf_text():
    file = request.files['pdf']
    path = os.path.join(app.config['UPLOAD_FOLDER'], 'temp_ak.pdf')
    file.save(path)
    from checker import extract_text_from_pdf
    text = extract_text_from_pdf(path)
    return jsonify({'text': text})

if __name__ == '__main__':
    app.run(debug=True)