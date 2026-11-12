from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from jinja2 import TemplateNotFound
from werkzeug.security import generate_password_hash, check_password_hash
from database import db, School, Student, Result, AnswerKey
import os, re, uuid

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-me')

# Render/Heroku give postgres://, SQLAlchemy needs postgresql://
db_url = os.environ.get('DATABASE_URL') or f"sqlite:///{os.path.join(os.getcwd(), 'gradeai.db')}"
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Neon/Render drop idle connections; this reconnects instead of crashing
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True, 'pool_recycle': 280}
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB per request
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)
with app.app_context():
    db.create_all()


def safe_upload_path(p):
    """Only allow files inside the uploads folder (blocks path traversal)."""
    name = os.path.basename(p or '')
    if not name:
        return None
    full = os.path.join(app.config['UPLOAD_FOLDER'], name)
    return full if os.path.exists(full) else None


@app.route('/health')
def health():
    return 'ok'


# ── AUTH ──────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return redirect(url_for('login') if 'school_id' not in session else url_for('classes_page'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        school = School.query.filter_by(email=email).first()
        if school and check_password_hash(school.password, password):
            session['school_id'] = school.id
            session['school_name'] = school.name
            return redirect(url_for('classes_page'))
        return render_template('login.html', error='Invalid email or password')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = (request.form.get('school_name') or request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        if not name or not email or not password:
            return render_template('signup.html', error='Please fill all fields')
        if School.query.filter_by(email=email).first():
            return render_template('signup.html', error='Email already registered')
        school = School(name=name, email=email, password=generate_password_hash(password))
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

# ── CLASSES ───────────────────────────────────────────────────────────────────
@app.route('/classes')
def classes_page():
    if 'school_id' not in session: return redirect(url_for('login'))
    return render_template('classes.html', school_name=session['school_name'])

# ── DASHBOARD ─────────────────────────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    if 'school_id' not in session: return redirect(url_for('login'))
    return render_template('dashboard.html',
        school_name=session['school_name'],
        selected_class=request.args.get('class', ''),
        selected_section=request.args.get('section', ''))

@app.route('/past-tests')
def past_tests():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    results = Result.query.filter_by(
        school_id=session['school_id'],
        class_name=request.args.get('class', ''),
        section=request.args.get('section', '')
    ).order_by(Result.created_at.desc()).all()

    seen = {}
    for r in results:
        key = f"{r.subject}_{r.exam_type}_{r.created_at.date()}"
        if key not in seen:
            seen[key] = {'exam_type': r.exam_type, 'subject': r.subject,
                         'date': r.created_at.strftime('%d %b %Y'),
                         'total_marks': r.total_marks or 0, 'rankings': []}
        try:
            match = re.search(r'TOTAL:\s*([\d.]+)', r.feedback or '')
            score = float(match.group(1)) if match else 0
        except Exception:
            score = 0
        seen[key]['rankings'].append({'name': r.student_name or 'Unknown', 'score': score})

    subjects = {}
    for test in seen.values():
        test['rankings'].sort(key=lambda x: x['score'], reverse=True)
        scores = [s['score'] for s in test['rankings']]
        test['average'] = round(sum(scores) / len(scores), 1) if scores else 0
        test['total_students'] = len(test['rankings'])
        subjects.setdefault(test['subject'], []).append(test)
    return jsonify({'subjects': subjects})

# ── LIVE SCAN PAGE ────────────────────────────────────────────────────────────
@app.route('/live-scan-page')
def live_scan_page():
    if 'school_id' not in session: return redirect(url_for('login'))
    students = Student.query.filter_by(
        school_id=session['school_id'],
        class_name=request.args.get('class', ''),
        section=request.args.get('section', '')
    ).order_by(Student.name).all()
    return render_template('live_scan.html',
        class_name=request.args.get('class', ''),
        section=request.args.get('section', ''),
        exam_type=request.args.get('exam_type', ''),
        subject=request.args.get('subject', ''),
        students=students)

# ── SCANNER SETUP (safe even if template is missing) ──────────────────────────
@app.route('/scanner-setup')
def scanner_setup():
    if 'school_id' not in session: return redirect(url_for('login'))
    try:
        return render_template('scanner_setup.html')
    except TemplateNotFound:
        return redirect(url_for('classes_page'))

# ── UPLOAD (camera or PDF) ────────────────────────────────────────────────────
@app.route('/upload-camera-image', methods=['POST'])
def upload_camera_image():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    file = request.files.get('image')
    if not file: return jsonify({'error': 'No file uploaded'}), 400

    ext = os.path.splitext(file.filename or '')[1].lower() or '.jpg'
    if ext not in ['.jpg', '.jpeg', '.png', '.webp', '.heic', '.bmp', '.pdf']:
        ext = '.jpg'

    filename = f"upload_{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(save_path)

    # Optional contrast enhancement (only if opencv is installed)
    if ext != '.pdf':
        try:
            import cv2
            img = cv2.imread(save_path)
            if img is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                cv2.imwrite(save_path, cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR))
        except Exception:
            pass

    return jsonify({'success': True, 'path': save_path, 'name': filename})

# ── GRADE ─────────────────────────────────────────────────────────────────────
@app.route('/grade-live-scan', methods=['POST'])
def grade_live_scan():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401

    data = request.json or {}
    answer_key = data.get('answer_key')
    total_marks = data.get('total_marks')
    student_id = data.get('student_id')
    exam_type = data.get('exam_type')
    subject = data.get('subject')
    class_name = data.get('class_name')
    section = data.get('section')

    image_path = safe_upload_path(data.get('image_path'))
    if not image_path:
        return jsonify({'error': 'Uploaded file not found. Please upload again.'}), 404

    if not answer_key or not answer_key.strip():
        return jsonify({'error': 'Answer key is empty. Please upload the answer key PDF first.'}), 400

    try:
        from checker import grade_answer
        result_text = grade_answer(image_path, answer_key, total_marks)
    except Exception as e:
        return jsonify({'error': f'Grading failed: {str(e)}'}), 500

    total_score = ''
    for line in result_text.split('\n'):
        if 'TOTAL:' in line or 'SCORE:' in line:
            total_score = line.split(':', 1)[1].strip()
            break

    student = Student.query.filter_by(student_id=student_id, school_id=session['school_id']).first()
    if student:
        db.session.add(Result(
            student_id=student.id, school_id=session['school_id'],
            exam_type=exam_type, subject=subject,
            class_name=class_name, section=section,
            student_name=student.name, student_wrote='Upload',
            score=total_score, feedback=result_text,
            total_marks=int(total_marks) if total_marks else 0
        ))
        db.session.commit()

    try: os.remove(image_path)
    except Exception: pass

    return jsonify({'success': True, 'score': total_score, 'result': result_text})

# ── EXTRACT PDF TEXT (answer key) ─────────────────────────────────────────────
@app.route('/extract-pdf-text', methods=['POST'])
def extract_pdf_text():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    file = request.files.get('pdf')
    if not file: return jsonify({'error': 'No file'}), 400
    path = os.path.join(app.config['UPLOAD_FOLDER'], f'ak_{uuid.uuid4().hex}.pdf')
    file.save(path)
    try:
        from checker import extract_text_from_pdf
        text = extract_text_from_pdf(path)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        try: os.remove(path)
        except Exception: pass
    return jsonify({'text': text})

# ── BULK GRADE ────────────────────────────────────────────────────────────────
@app.route('/bulk-grade-page')
def bulk_grade_page():
    if 'school_id' not in session: return redirect(url_for('login'))
    students = Student.query.filter_by(
        school_id=session['school_id'],
        class_name=request.args.get('class'),
        section=request.args.get('section')
    ).all()
    return render_template('bulk_grade.html',
        class_name=request.args.get('class'), section=request.args.get('section'),
        exam_type=request.args.get('exam_type'), subject=request.args.get('subject'),
        students=students)

@app.route('/bulk-grade', methods=['POST'])
def bulk_grade():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    answer_key_file = request.files.get('answer_key_pdf')
    student_files = request.files.getlist('student_pdfs')
    student_ids = request.form.getlist('student_ids')
    total_marks = request.form.get('total_marks')
    exam_type = request.form.get('exam_type')
    subject = request.form.get('subject')
    class_name = request.form.get('class_name')
    section = request.form.get('section')

    if not answer_key_file:
        return jsonify({'error': 'Answer key PDF is required'}), 400
    if not student_files:
        return jsonify({'error': 'No student PDFs uploaded'}), 400

    ak_path = os.path.join(app.config['UPLOAD_FOLDER'], f'ak_{uuid.uuid4().hex}.pdf')
    answer_key_file.save(ak_path)

    from checker import extract_text_from_pdf, grade_answer_pdf
    answer_key_text = extract_text_from_pdf(ak_path)
    results = []

    for i, student_file in enumerate(student_files):
        sid = student_ids[i] if i < len(student_ids) else f'STU{i}'
        sp = os.path.join(app.config['UPLOAD_FOLDER'], f'stu_{uuid.uuid4().hex}.pdf')
        student_file.save(sp)
        try:
            result_text = grade_answer_pdf(sp, answer_key_text, total_marks)
        except Exception as e:
            result_text = f"Error: {str(e)}"

        total_score = ''
        for line in result_text.split('\n'):
            if line.startswith('TOTAL:'):
                total_score = line.replace('TOTAL:', '').strip()
                break

        student = Student.query.filter_by(student_id=sid, school_id=session['school_id']).first()
        if student:
            db.session.add(Result(
                student_id=student.id, school_id=session['school_id'],
                exam_type=exam_type, subject=subject,
                class_name=class_name, section=section,
                student_name=student.name, student_wrote='PDF submission',
                score=total_score, feedback=result_text,
                total_marks=int(total_marks) if total_marks else 0
            ))
            db.session.commit()
        try: os.remove(sp)
        except Exception: pass
        results.append({'student_id': sid, 'result': result_text, 'score': total_score})

    try: os.remove(ak_path)
    except Exception: pass
    return jsonify({'results': results})

# ── STUDENTS ──────────────────────────────────────────────────────────────────
@app.route('/students')
def students_page():
    if 'school_id' not in session: return redirect(url_for('login'))
    students = Student.query.filter_by(school_id=session['school_id'])\
        .order_by(Student.class_name, Student.section, Student.name).all()
    return render_template('students.html', students=students, school_name=session['school_name'])

@app.route('/get-students')
def get_students():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    students = Student.query.filter_by(
        school_id=session['school_id'],
        class_name=request.args.get('class', ''),
        section=request.args.get('section', '')
    ).order_by(Student.name).all()
    return jsonify([{'id': s.id, 'student_id': s.student_id, 'name': s.name} for s in students])

@app.route('/add-student', methods=['POST'])
def add_student():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    if Student.query.filter_by(student_id=data['student_id'], school_id=session['school_id']).first():
        return jsonify({'error': 'Student ID already exists'}), 400
    db.session.add(Student(student_id=data['student_id'], name=data['name'],
        class_name=data['class_name'], section=data['section'], school_id=session['school_id']))
    db.session.commit()
    return jsonify({'success': True})

@app.route('/import-students', methods=['POST'])
def import_students():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    import pandas as pd, io
    file = request.files['csv_file']
    content = file.read()
    try:
        df = pd.read_csv(io.BytesIO(content)) if file.filename.lower().endswith('.csv') \
            else pd.read_excel(io.BytesIO(content))
        df.columns = [str(c).strip().lower() for c in df.columns]
        added, skipped = 0, 0
        for _, row in df.iterrows():
            sid = str(row.get('student_id', '')).strip()
            name = str(row.get('name', '')).strip()
            cls = str(row.get('class', '')).strip()
            sec = str(row.get('section', '')).strip().upper()
            if not all([sid, name, cls, sec]) or sid == 'nan':
                skipped += 1
                continue
            exists = Student.query.filter_by(student_id=sid, school_id=session['school_id']).first()
            if exists:
                exists.name = name; exists.class_name = cls; exists.section = sec
                skipped += 1
            else:
                db.session.add(Student(student_id=sid, name=name, class_name=cls,
                                       section=sec, school_id=session['school_id']))
                added += 1
        db.session.commit()
        return jsonify({'success': True, 'added': added, 'skipped': skipped})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/edit-student', methods=['POST'])
def edit_student():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    student = Student.query.filter_by(id=data['id'], school_id=session['school_id']).first()
    if not student: return jsonify({'error': 'Not found'}), 404
    student.name = data['name']; student.student_id = data['student_id']
    student.class_name = data['class_name']; student.section = data['section']
    db.session.commit()
    return jsonify({'success': True})

@app.route('/delete-student', methods=['POST'])
def delete_student():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    student = Student.query.filter_by(id=data['id'], school_id=session['school_id']).first()
    if student:
        Result.query.filter_by(student_id=student.id).delete()
        db.session.delete(student)
        db.session.commit()
    return jsonify({'success': True})

@app.route('/promote-class', methods=['POST'])
def promote_class():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    students = Student.query.filter_by(school_id=session['school_id'],
        class_name=str(data['from_class']), section=data['section']).all()
    for s in students:
        s.class_name = str(data['to_class'])
    db.session.commit()
    return jsonify({'success': True, 'promoted': len(students)})

# ── ANSWER KEYS ───────────────────────────────────────────────────────────────
@app.route('/save-answer-key', methods=['POST'])
def save_answer_key():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    existing = AnswerKey.query.filter_by(school_id=session['school_id'],
        class_name=data['class_name'], section=data['section'],
        exam_type=data['exam_type'], subject=data['subject']).first()
    if existing:
        existing.content = data['content']
        existing.total_marks = data.get('total_marks', 0)
    else:
        db.session.add(AnswerKey(school_id=session['school_id'], class_name=data['class_name'],
            section=data['section'], exam_type=data['exam_type'], subject=data['subject'],
            content=data['content'], total_marks=data.get('total_marks', 0)))
    db.session.commit()
    return jsonify({'success': True})

@app.route('/get-answer-key')
def get_answer_key():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    ak = AnswerKey.query.filter_by(school_id=session['school_id'],
        class_name=request.args.get('class_name'), section=request.args.get('section'),
        exam_type=request.args.get('exam_type'), subject=request.args.get('subject')).first()
    return jsonify({'content': ak.content, 'total_marks': ak.total_marks} if ak
                   else {'content': '', 'total_marks': 0})

@app.route('/list-answer-keys')
def list_answer_keys():
    if 'school_id' not in session: return jsonify({'error': 'Not logged in'}), 401
    return jsonify([{'class_name': k.class_name, 'section': k.section,
        'exam_type': k.exam_type, 'subject': k.subject, 'total_marks': k.total_marks}
        for k in AnswerKey.query.filter_by(school_id=session['school_id']).all()])

# ── HISTORY ───────────────────────────────────────────────────────────────────
@app.route('/history')
def history():
    if 'school_id' not in session: return redirect(url_for('login'))
    results = Result.query.filter_by(school_id=session['school_id'])\
        .order_by(Result.created_at.desc()).limit(100).all()
    return render_template('history.html', results=results, school_name=session['school_name'])

# ── RUN ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))