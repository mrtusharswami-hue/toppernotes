import os
import io
import time
import json
import psycopg2
import psycopg2.extras
import os
import urllib.request
import urllib.parse
from flask import Flask, g, render_template, request, jsonify, send_from_directory, session, url_for, redirect, make_response
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from pypdf import PdfReader
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
import firebase_admin
from firebase_admin import credentials, firestore
from firebase_admin import auth as firebase_auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

def get_db_connection():
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'), cursor_factory=psycopg2.extras.RealDictCursor)
    return conn
# Initialize the Flask application
app = Flask(__name__)
app.secret_key = 'rankuphub_super_secret_key'

# Configure ProxyFix for Render.com to correctly handle HTTPS and domain forwarding
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Configure Real File Upload Directory
UPLOAD_FOLDER = os.path.join('static', 'uploaded_notes')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DATABASE = 'database.db'
FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID', '1gHM_olyUyGgQ9_vwDlZ2pbJs0OQkStTZ') # Add your 5TB Google Drive Folder ID here

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = get_db_connection()
    return db

def init_db():
    with app.app_context():
        db = get_db()
        try:
            with app.open_resource('schema.sql', mode='rb') as f:
                with db.cursor() as cursor:
                    cursor.execute(f.read().decode('utf-8'))
        except Exception as e:
            print(f"Schema run error: {e}")
        db.commit()

# Ensure database is initialized automatically on startup, especially for production
with app.app_context():
    try:
        db = get_db_connection()
        try:
            with app.open_resource('schema.sql', mode='rb') as f:
                with db.cursor() as cursor:
                    cursor.execute(f.read().decode('utf-8'))
        except Exception as e:
            print(f"Schema run error: {e}")
        
        # Initialize Users Table dynamically
        with db.cursor() as cursor:
            # Safely add description column to notes table
            try:
                cursor.execute('ALTER TABLE notes ADD COLUMN description TEXT')
            except Exception:
                db.rollback() # Postgres needs rollback after error

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE,
                    name TEXT,
                    profile_pic TEXT,
                    points INTEGER DEFAULT 3
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS visitor_count (
                    id INTEGER PRIMARY KEY,
                    count INTEGER DEFAULT 0
                )
            ''')
            
            # Ensure the single row exists
            cursor.execute('SELECT count FROM visitor_count WHERE id = 1')
            if not cursor.fetchone():
                cursor.execute('INSERT INTO visitor_count (id, count) VALUES (1, 0)')
                
        db.commit()
        print("Database initialized automatically on startup.")
    except Exception as e:
        print(f"Startup DB Error: {e}")
    finally:
        if 'db' in locals():
            db.close()

@app.cli.command('init-db')
def init_db_command():
    """Clears the existing data and creates new tables."""
    init_db()
    print('Initialized the database.')

@app.route('/')
def index():
    db = get_db()
    visit_count = 0
    with db.cursor() as cursor:
        cursor.execute('UPDATE visitor_count SET count = count + 1 WHERE id = 1 RETURNING count')
        row = cursor.fetchone()
        if row:
            visit_count = row['count']
    db.commit()
    return render_template('index.html', visit_count=visit_count)

@app.route('/audio-reader')
def audio_reader():
    return render_template('audio_reader.html')

@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('q', '').strip()
    subject = request.args.get('subject', '').strip().lower()
    
    db = get_db()
    with db.cursor() as cursor:
        sql_query = "SELECT title, subject, filename, drive_link, uploader_email, description FROM notes"
        params = []
        conditions = []

        if query:
            conditions.append("(title ILIKE %s OR description ILIKE %s)")
            params.append(f"%{query}%")
            params.append(f"%{query}%")

        if subject:
            conditions.append("LOWER(subject) = %s")
            params.append(subject)
        
        if conditions:
            sql_query += " WHERE " + " AND ".join(conditions)
            
        cursor.execute(sql_query, params)
        results = cursor.fetchall()
        
    return jsonify(results)

@app.route('/api/firebase-config')
def firebase_config():
    api_key = os.environ.get("FIREBASE_API_KEY")
    
    # Safe fallback dictionary to guarantee zero fetch loop failures during testing
    if not api_key or not api_key.strip():
        return jsonify({
            "apiKey": "AIzaSy_FALLBACK_TEST_KEY_REPLACE_ME",
            "authDomain": "rankup-hub-test.firebaseapp.com",
            "projectId": "rankup-hub-test",
            "storageBucket": "rankup-hub-test.appspot.com",
            "messagingSenderId": "123456789012",
            "appId": "1:123456789012:web:abcdef1234567890"
        })

    return jsonify({
        "apiKey": api_key,
        "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
        "projectId": os.environ.get("FIREBASE_PROJECT_ID", ""),
        "storageBucket": os.environ.get("FIREBASE_STORAGE_BUCKET", ""),
        "messagingSenderId": os.environ.get("FIREBASE_MESSAGING_SENDER_ID", ""),
        "appId": os.environ.get("FIREBASE_APP_ID", "")
    })

@app.route('/api/sync-user', methods=['POST'])
def sync_user():
    data = request.get_json()
    email = data.get('email')
    name = data.get('name')
    profile_pic = data.get('profile_pic')
    
    if not email:
        return jsonify({"error": "Email required"}), 400
        
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, points FROM users WHERE email = %s', (email,))
    user = cursor.fetchone()
    
    if user is None:
        cursor.execute('INSERT INTO users (email, name, profile_pic, points) VALUES (%s, %s, %s, 3)', 
                       (email, name, profile_pic))
    else:
        cursor.execute('UPDATE users SET name = %s, profile_pic = %s WHERE email = %s',
                       (name, profile_pic, email))
    db.commit()
    
    session['user_email'] = email
    if user:
        session['points'] = user['points']
    else:
        session['points'] = 3
        
    return jsonify({"status": "success"})

@app.route('/api/credits')
def get_credits():
    if 'user_email' in session:
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute('SELECT points FROM users WHERE email = %s', (session['user_email'],))
            row = cursor.fetchone()
            if row:
                session['points'] = row['points']
                return jsonify({"credits": row['points']})
    return jsonify({"credits": session.get('points', 3)})

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute('SELECT name AS username, points, profile_pic FROM users WHERE name IS NOT NULL ORDER BY points DESC LIMIT 5')
        results = cursor.fetchall()
    return jsonify(results)

# In-Memory Stream Upload to Google Drive & Firestore Sync
@app.route('/api/upload', methods=['POST'])
def api_upload():
    token = request.form.get("firebase_token")
    frontend_email = request.form.get("email")
    if not token:
        print("Upload Error: Missing firebase_token in form data.")
        return jsonify({"status": "error", "message": "Please login first!"}), 401

    # Ensure Firebase app is initialized inside the request context
    if not firebase_admin._apps:
        cred = credentials.Certificate('google-credentials.json')
        firebase_admin.initialize_app(cred, options={'projectId': 'toppernotes-auth'})
    try:
        decoded_token = firebase_auth.verify_id_token(token)
        user_email = decoded_token.get("email") or frontend_email
    except Exception as e:
        print(f"Upload Verification Error: {e}")
        return jsonify({"status": "error", "message": "Please login first!"}), 401        
    copyright_consent = request.form.get('copyrightCheck')
    title = request.form.get('title', 'Untitled Note')
    description = request.form.get('description', '')

    # Secure Subject Extraction & Fallback Logic    subject = request.form.get("subject", "general")
    if not subject:
        subject = "general"
    subject = subject.strip().title() # Capitalize for clean UI display representation
    
    # Validating the mandatory copyright checkbox natively in backend
    if not copyright_consent or copyright_consent.lower() != 'true':
        return jsonify({"status": "error", "message": "Copyright consent is mandatory."}), 400
        
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part in the request."}), 400
        
    uploaded_file = request.files['file']
    if uploaded_file.filename == '':
        return jsonify({"status": "error", "message": "No file selected."}), 400

    if uploaded_file:
        base_filename = secure_filename(uploaded_file.filename)
        filename = f"{int(time.time())}_{base_filename}"
        
        try:
            # Load account credentials dynamically
            # 🚀 NEW OAUTH L0GIC: Bypass Service Account Quota Error
            SCOPES = ['https://www.googleapis.com/auth/drive']
            creds = None
            if os.path.exists('token.json'):
                creds = Credentials.from_authorized_user_file('token.json', SCOPES)
                
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())

            # Start Google Drive API with the Master Token
            service = build('drive', 'v3', credentials=creds)
            
            # Pull the file binary directly into RAM using an in-memory byte buffer
            file_stream = io.BytesIO(uploaded_file.read())
            
            # Stream this buffer into Google Drive via MediaIoBaseUpload
            file_metadata = {
                'name': filename,
                'parents': ['1gHM_oIyUyGgQ9_vwDlZ2pbJs0OQkStTZ']
            }
            # Direct Multipart Upload payload to seamlessly bypass service account storage quota natively
            media = MediaIoBaseUpload(file_stream, mimetype='application/pdf', resumable=False)
            
            drive_file = service.files().create(
                body=file_metadata, 
                media_body=media, 
                fields='id, webViewLink',
                supportsAllDrives=True
            ).execute(num_retries=3)
            
            drive_link = drive_file.get('webViewLink', '')
            
            # Move ownership instantly from Service Account to your personal Google Account to bypass storage quota
            try:
                permission_body = {
                    'type': 'user',
                    'role': 'owner',
                    'emailAddress': 'mr.tusharswami@gmail.com'
                }
                service.permissions().create(
                    fileId=drive_file.get('id'),
                    body=permission_body,
                    transferOwnership=True
                ).execute()
            except Exception as perm_err:
                print(f"Ownership Transfer Warning (Safe to ignore if file uploaded): {perm_err}")
            
            # Instantly log metadata into Firebase Firestore "study_materials" collection
            '''try:
                db_firestore = firestore.client()
                db_firestore.collection('study_materials').add({
                    'title': title,
                    'subject': subject,
                    'filename': filename,
                    'drive_link': drive_link,
                    'uploader_email': user_email,
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
            except Exception as fs_error:
                print(f"Firestore Error: {fs_error}")'''

            # Also Insert into our PostgreSQL database for local display/sync
            db = get_db()
            with db.cursor() as cursor:
                cursor.execute('INSERT INTO notes (title, subject, filename, drive_link, uploader_email, description) VALUES (%s, %s, %s, %s, %s, %s)',
                           (title, subject, filename, drive_link, user_email, description))
                
                # Reward user with 1 Point for uploading
                session['points'] = session.get('points', 3) + 1
                
                # Save points back to users table
                cursor.execute('UPDATE users SET points = %s WHERE email = %s', (session['points'], user_email))
            db.commit()
            
            return jsonify({"status": "success", "message": f"File '{filename}' uploaded securely. You earned +1 Point!"})
        except Exception as e:
            print(f"Upload Error: {e}")
            return jsonify({'error': str(e)}), 500

# --- STEP 2: PDF Extraction Routes ---
@app.route('/extract-audio/<filename>')
def extract_audio(filename):
    if 'points' not in session:
        session['points'] = 3
        
    if session['points'] >= 1:
        db = get_db()
        drive_link = None
        with db.cursor() as cursor:
            cursor.execute('SELECT drive_link FROM notes WHERE filename = %s', (filename,))
            note = cursor.fetchone()
            if note:
                drive_link = note['drive_link']

        if not drive_link:
            return jsonify({"status": "error", "message": "File not found in database."}), 404

        file_id = ''
        if '/d/' in drive_link:
            file_id = drive_link.split('/d/')[1].split('/')[0]
        elif 'id=' in drive_link:
            file_id = drive_link.split('id=')[1].split('&')[0]

        if not file_id:
            return jsonify({"status": "error", "message": "Invalid Drive Link."}), 404
            
        try:
            SCOPES = ['https://www.googleapis.com/auth/drive']
            creds = None
            if os.path.exists('token.json'):
                creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            service = build('drive', 'v3', credentials=creds)

            request_drive = service.files().get_media(fileId=file_id)
            file_stream = io.BytesIO()
            downloader = MediaIoBaseDownload(file_stream, request_drive)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            file_stream.seek(0)

            reader = PdfReader(file_stream)
            text = "".join(page.extract_text() + "\n" for page in reader.pages if page.extract_text())
            session['points'] -= 1
            if 'user_email' in session:
                with db.cursor() as cursor:
                    cursor.execute('UPDATE users SET points = %s WHERE email = %s', (session['points'], session['user_email']))
                db.commit()
            return jsonify({"status": "success", "text": text, "new_balance": session['points']})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    
    return jsonify({"status": "error", "message": "insufficient_credits"}), 403

@app.route('/view-pdf/<filename>')
def view_pdf(filename):
    db = get_db()
    embed_url = ""
    with db.cursor() as cursor:
        cursor.execute('SELECT drive_link FROM notes WHERE filename = %s', (filename,))
        note = cursor.fetchone()
        if note and note['drive_link']:
            drive_link = note['drive_link']
            # Safely extract Google Drive File ID and reformat for preview iframe
            if '/d/' in drive_link:
                file_id = drive_link.split('/d/')[1].split('/')[0]
                embed_url = f"https://drive.google.com/file/d/{file_id}/preview"
            elif 'id=' in drive_link:
                file_id = drive_link.split('id=')[1].split('&')[0]
                embed_url = f"https://drive.google.com/file/d/{file_id}/preview"

    return render_template('view_pdf.html', filename=filename, embed_url=embed_url)

# User Feedback Route
@app.route('/api/feedback', methods=['POST'])
def submit_feedback():
    data = request.get_json()
    rating = data.get('rating')
    message = data.get('message')
    email = data.get('email', 'guest')
    
    # Safely log the feedback
    # TODO: In the future, this can be inserted into the database or sent via email alerts.
    print(f"[FEEDBACK RECEIVED - {time.ctime()}] User: {email} | Rating: {rating}/5 | Message: {message}")
    
    return jsonify({"status": "success", "message": "Feedback submitted successfully!"})

# Jarvis AI Route Placeholder
@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.get_json()
    user_message = data.get('message', '')
    # TODO: Integrate live Gemini API logic here later
    reply = f"RankUp Hub AI (Jarvis) received: '{user_message}'. Full AI integration coming soon!"
    return jsonify({"reply": reply})

# Route to securely serve uploaded files to prevent 404 errors
@app.route('/files/<filename>')
def serve_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    # रेंडर क्लाउड सर्वर का पोर्ट हैंडल करने के लिए
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)