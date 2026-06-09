import os
import uuid
import zipfile
import shutil
from io import BytesIO
from pathlib import Path
from functools import wraps

from flask import (
    Flask, request, session, redirect, url_for,
    render_template, send_file, jsonify, abort
)
from PIL import Image
from rembg import remove, new_session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "changeme-set-in-env")

APP_PASSWORD = os.environ.get("APP_PASSWORD", "zaneva2024")
REMBG_MODEL = os.environ.get("REMBG_MODEL", "birefnet-portrait")
MAX_FILES = int(os.environ.get("MAX_FILES", 20))
MAX_FILE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", 10))
SESSION_TTL = int(os.environ.get("SESSION_TTL_MINUTES", 60)) * 60
TMP_BASE = Path("/tmp/bgremover")
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

TMP_BASE.mkdir(exist_ok=True)

print(f"[BG Remover] Loading model: {REMBG_MODEL} ...")
rembg_session = new_session(REMBG_MODEL)
print(f"[BG Remover] Model ready.")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def get_work_dir():
    sid = session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        session["sid"] = sid
    work = TMP_BASE / sid
    work.mkdir(exist_ok=True)
    (work / "input").mkdir(exist_ok=True)
    (work / "output").mkdir(exist_ok=True)
    return work

@app.route("/", methods=["GET"])
@login_required
def index():
    return render_template("index.html", max_files=MAX_FILES, max_mb=MAX_FILE_MB)

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == APP_PASSWORD:
            session["authenticated"] = True
            session.pop("sid", None)
            return redirect(url_for("index"))
        error = "Password salah. Coba lagi."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    _cleanup_session()
    session.clear()
    return redirect(url_for("login"))

@app.route("/upload", methods=["POST"])
@login_required
def upload():
    files = request.files.getlist("photos")
    if not files:
        return jsonify({"error": "Tidak ada file yang dikirim."}), 400
    work = get_work_dir()
    accepted = []
    rejected = []
    for i, f in enumerate(files[:MAX_FILES]):
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            rejected.append({"name": f.filename, "reason": "Format tidak didukung (JPG/PNG/WEBP)"})
            continue
        content = f.read()
        if len(content) > MAX_FILE_MB * 1024 * 1024:
            rejected.append({"name": f.filename, "reason": f"Ukuran melebihi {MAX_FILE_MB} MB"})
            continue
        safe_name = f"{uuid.uuid4().hex}{ext}"
        in_path = work / "input" / safe_name
        in_path.write_bytes(content)
        accepted.append({"id": safe_name, "original": f.filename})
    if len(files) > MAX_FILES:
        rejected.append({"name": "...", "reason": f"Hanya {MAX_FILES} file pertama yang diproses"})
    return jsonify({"accepted": accepted, "rejected": rejected})

@app.route("/process/<file_id>", methods=["POST"])
@login_required
def process(file_id):
    work = get_work_dir()
    matches = list((work / "input").glob(f"{file_id}.*"))
    if not matches:
        return jsonify({"error": "File tidak ditemukan."}), 404
    in_path = matches[0]
    out_name = in_path.stem + "_nobg.png"
    out_path = work / "output" / out_name
    try:
        img = Image.open(in_path).convert("RGBA")
        result = remove(img, session=rembg_session)
        result.save(out_path, format="PNG")
        return jsonify({"output_id": out_name, "status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"}), 500

@app.route("/preview/<output_id>")
@login_required
def preview(output_id):
    work = get_work_dir()
    out_path = work / "output" / output_id
    if not out_path.exists():
        abort(404)
    return send_file(out_path, mimetype="image/png")

@app.route("/preview-input/<file_id>")
@login_required
def preview_input(file_id):
    work = get_work_dir()
    matches = list((work / "input").glob(f"{file_id}.*"))
    if not matches:
        abort(404)
    return send_file(matches[0])

@app.route("/download/<output_id>")
@login_required
def download_single(output_id):
    work = get_work_dir()
    out_path = work / "output" / output_id
    if not out_path.exists():
        abort(404)
    return send_file(out_path, as_attachment=True, download_name=output_id, mimetype="image/png")

@app.route("/download-all", methods=["POST"])
@login_required
def download_all():
    work = get_work_dir()
    output_ids = request.json.get("output_ids", [])
    if not output_ids:
        return jsonify({"error": "Tidak ada file untuk didownload."}), 400
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for oid in output_ids:
            p = work / "output" / oid
            if p.exists():
                zf.write(p, arcname=oid)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="zaneva_nobg.zip", mimetype="application/zip")

@app.route("/reset", methods=["POST"])
@login_required
def reset():
    _cleanup_session()
    session.pop("sid", None)
    return jsonify({"status": "ok"})

def _cleanup_session():
    sid = session.get("sid")
    if sid:
        work = TMP_BASE / sid
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
