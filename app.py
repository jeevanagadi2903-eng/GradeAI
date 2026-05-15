from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from database import db, School, Student, Result, AnswerKey
import os
import re
import threading
import time

app = Flask(__name__)
app.secret_key = 'gradeai-secret-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///gradeai.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

os.makedirs('uploads', exist_ok=True)
db.init_app(app)

with app.app_context():
    db.create_all()

# ─── FOLDER WATCHER ───────────────────────────────────────────────────────────
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

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

# ─── AUTH ─────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('classes_page'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        school = School.query.filter_by(email=email, password=password).first()
        if school:
            session['school_id'] = school.id
            session['school_name'] = school.name
            return redirect(url_for('classes_page'))
        return render_template('login.html', error='Invalid email or password')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('school_name')
        email = request.form.get('email')
        password = request.form.get('password')
        existing = School.query.filter_by(email=email).first()
        if existing:
            return render_template('signup.html', error='Email already registered')
        school = School(name=name, email=email, password=password)
        db.session.add(school)
        db.session.commit()
        session['school_id'] = school.id
        session['school_name'] = school.name
        return redirect(url_for('classes_page'))
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─── PAGE 1: CLASSES ──────────────────────────────────────────────────────────
@app.route('/classes')
def classes_page():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    return render_template('classes.html', school_name=session['school_name'])

# ─── PAGE 2: DASHBOARD ────────────────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    selected_class = request.args.get('class', '')
    selected_section = request.args.get('section', '')
    return render_template('dashboard.html',
        school_name=session['school_name'],
        selected_class=selected_class,
        selected_section=selected_section)

@app.route('/past-tests')
def past_tests():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    class_name = request.args.get('class', '')
    section = request.args.get('section', '')

    results = Result.query.filter_by(
        school_id=session['school_id'],
        class_name=class_name,
        section=section
    ).order_by(Result.created_at.desc()).all()

    subjects = {}
    seen = {}
    for r in results:
        key = f"{r.subject}_{r.exam_type}_{r.created_at.date()}"
        if key not in seen:
            seen[key] = {
                'exam_type': r.exam_type,
                'subject': r.subject,
                'date': r.created_at.strftime('%d %b %Y'),
                'total_marks': r.total_marks or 0,
                'rankings': []
            }
        try:
            match = re.search(r'TOTAL:\s*([\d.]+)', r.feedback or '')
            score = float(match.group(1)) if match else 0
        except:
            score = 0
        name = r.student_name or 'Unknown'
        seen[key]['rankings'].append({'name': name, 'score': score})

    for key, test in seen.items():
        test['rankings'].sort(key=lambda x: x['score'], reverse=True)
        scores = [s['score'] for s in test['rankings']]
        test['average'] = round(sum(scores)/len(scores), 1) if scores else 0
        test['total_students'] = len(test['rankings'])
        subj = test['subject']
        if subj not in subjects:
            subjects[subj] = []
        subjects[subj].append(test)

    return jsonify({'subjects': subjects})

# ─── PAGE 3: LIVE SCAN ────────────────────────────────────────────────────────
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
        class_name=class_name,
        section=section
    ).order_by(Student.name).all()
    return render_template('live_scan.html',
        class_name=class_name, section=section,
        exam_type=exam_type, subject=subject,
        students=students)

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

    from checker import grade_answer
    result_text = grade_answer(image_path, answer_key, total_marks)

    total_score = ''
    for line in result_text.split('\n'):
        if 'TOTAL:' in line or 'SCORE:' in line:
            total_score = line.split(':', 1)[1].strip()
            break

    student = Student.query.filter_by(
        student_id=student_id,
        school_id=session['school_id']
    ).first()

    if student:
        result = Result(
            student_id=student.id,
            school_id=session['school_id'],
            exam_type=exam_type,
            subject=subject,
            class_name=class_name,
            section=section,
            student_wrote='Live scan',
            score=total_score,
            feedback=result_text,
            student_name=student.name,
            total_marks=int(total_marks) if total_marks else 0
        )
        db.session.add(result)
        db.session.commit()

    return jsonify({'success': True, 'score': total_score, 'result': result_text})

@app.route('/extract-pdf-text', methods=['POST'])
def extract_pdf_text():
    file = request.files['pdf']
    path = os.path.join(app.config['UPLOAD_FOLDER'], 'temp_ak.pdf')
    file.save(path)
    from checker import extract_text_from_pdf
    text = extract_text_from_pdf(path)
    return jsonify({'text': text})

# ─── BULK GRADE ───────────────────────────────────────────────────────────────
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
        class_name=class_name,
        section=section
    ).all()
    return render_template('bulk_grade.html',
        class_name=class_name, section=section,
        exam_type=exam_type, subject=subject,
        students=students)

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

        student = Student.query.filter_by(
            student_id=student_id,
            school_id=session['school_id']
        ).first()

        if student:
            result = Result(
                student_id=student.id,
                school_id=session['school_id'],
                exam_type=exam_type, subject=subject,
                class_name=class_name, section=section,
                student_wrote='PDF submission',
                score=total_score,
                feedback=result_text,
                student_name=student.name,
                total_marks=int(total_marks) if total_marks else 0
            )
            db.session.add(result)
            db.session.commit()

        results.append({'student_id': student_id, 'result': result_text, 'score': total_score})

    return jsonify({'results': results})

# ─── STUDENTS ─────────────────────────────────────────────────────────────────
@app.route('/students')
def students_page():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    students = Student.query.filter_by(school_id=session['school_id'])\
        .order_by(Student.class_name, Student.section, Student.name).all()
    return render_template('students.html', students=students, school_name=session['school_name'])

@app.route('/get-students')
def get_students():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    class_name = request.args.get('class', '')
    section = request.args.get('section', '')
    students = Student.query.filter_by(
        school_id=session['school_id'],
        class_name=class_name,
        section=section
    ).order_by(Student.name).all()
    return jsonify([{'id': s.id, 'student_id': s.student_id, 'name': s.name} for s in students])

@app.route('/add-student', methods=['POST'])
def add_student():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    exists = Student.query.filter_by(
        student_id=data['student_id'],
        school_id=session['school_id']
    ).first()
    if exists:
        return jsonify({'error': 'Student ID already exists'}), 400
    s = Student(
        student_id=data['student_id'],
        name=data['name'],
        class_name=data['class_name'],
        section=data['section'],
        school_id=session['school_id']
    )
    db.session.add(s)
    db.session.commit()
    return jsonify({'success': True})

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
            exists = Student.query.filter_by(
                student_id=sid, school_id=session['school_id']
            ).first()
            if exists:
                exists.name = name
                exists.class_name = cls
                exists.section = sec
                skipped += 1
            else:
                s = Student(student_id=sid, name=name, class_name=cls, section=sec, school_id=session['school_id'])
                db.session.add(s)
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

# ─── ANSWER KEYS ──────────────────────────────────────────────────────────────
@app.route('/save-answer-key', methods=['POST'])
def save_answer_key():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    existing = AnswerKey.query.filter_by(
        school_id=session['school_id'],
        class_name=data['class_name'],
        section=data['section'],
        exam_type=data['exam_type'],
        subject=data['subject']
    ).first()
    if existing:
        existing.content = data['content']
        existing.total_marks = data.get('total_marks', 0)
    else:
        ak = AnswerKey(
            school_id=session['school_id'],
            class_name=data['class_name'],
            section=data['section'],
            exam_type=data['exam_type'],
            subject=data['subject'],
            content=data['content'],
            total_marks=data.get('total_marks', 0)
        )
        db.session.add(ak)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/get-answer-key')
def get_answer_key():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    ak = AnswerKey.query.filter_by(
        school_id=session['school_id'],
        class_name=request.args.get('class_name'),
        section=request.args.get('section'),
        exam_type=request.args.get('exam_type'),
        subject=request.args.get('subject')
    ).first()
    if ak:
        return jsonify({'content': ak.content, 'total_marks': ak.total_marks})
    return jsonify({'content': '', 'total_marks': 0})

@app.route('/list-answer-keys')
def list_answer_keys():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    keys = AnswerKey.query.filter_by(school_id=session['school_id']).all()
    return jsonify([{
        'class_name': k.class_name, 'section': k.section,
        'exam_type': k.exam_type, 'subject': k.subject,
        'total_marks': k.total_marks
    } for k in keys])

# ─── CAMERA UPLOAD ────────────────────────────────────────────────────────────
@app.route('/upload-camera-image', methods=['POST'])
def upload_camera_image():
    if 'school_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    file = request.files.get('image')
    if not file:
        return jsonify({'error': 'No image uploaded'}), 400

    # Accept jpg, jpeg, png, heic, webp
    ext = os.path.splitext(file.filename)[1].lower() or '.jpg'
    if ext not in ['.jpg', '.jpeg', '.png', '.webp', '.heic', '.bmp']:
        ext = '.jpg'

    import uuid
    filename = f"camera_{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(save_path)

    # Optionally pre-process: straighten & enhance using OpenCV if available
    try:
        import cv2
        import numpy as np
        img = cv2.imread(save_path)
        if img is not None:
            # Auto-enhance contrast
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            # Save back as color for AI model
            enhanced_color = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
            cv2.imwrite(save_path, enhanced_color)
    except Exception:
        pass  # OpenCV not available — skip enhancement, still works

    # Add to scan queue so existing grading flow picks it up
    with queue_lock:
        if save_path not in scanned_queue:
            scanned_queue.append(save_path)

    return jsonify({'success': True, 'path': save_path, 'name': filename})

# ─── SCANNER SETUP ────────────────────────────────────────────────────────────
@app.route('/scanner-setup')
def scanner_setup():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    return render_template('scanner_setup.html')

# ─── HISTORY ──────────────────────────────────────────────────────────────────
@app.route('/history')
def history():
    if 'school_id' not in session:
        return redirect(url_for('login'))
    results = Result.query.filter_by(school_id=session['school_id'])\
        .order_by(Result.created_at.desc()).limit(100).all()
    return render_template('history.html', results=results, school_name=session['school_name'])

# ─── RUN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))