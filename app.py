"""SafeWalk: panel web, carga de video y análisis YOLO en el navegador."""
from contextlib import closing
from pathlib import Path
import sqlite3
import io
import os
import json
import secrets
import cv2
import numpy as np
import pandas as pd
from flask import Flask, render_template, jsonify, send_from_directory, send_file, request, redirect, url_for, Response, flash, session
from werkzeug.utils import secure_filename
from src.web_processor import processor
from src.main import preparar_base_datos, exportar_reportes
from src import reporting

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "outputs" / "safewalk.db"
OUTPUTS = ROOT / "outputs"
UPLOADS = ROOT / "data" / "videos" / "uploads"
ALLOWED_EXTENSIONS = {"mp4", "avi", "mov", "mkv", "webm"}
app = Flask(__name__)
app.secret_key = os.environ.get("SAFEWALK_SECRET_KEY") or secrets.token_hex(32)
app.config['MOBILE_ACCESS_KEY'] = os.environ.get('SAFEWALK_MOBILE_KEY', '')
app.config['MOBILE_CONFIG'] = os.environ.get('SAFEWALK_MOBILE_CONFIG', '')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB
preparar_base_datos()


@app.context_processor
def asset_versions():
    return {'asset_version': max(path.stat().st_mtime_ns for path in (ROOT / 'static').glob('*.js'))}


@app.after_request
def no_stale_camera_pages(response):
    if response.mimetype == 'text/html' or request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.before_request
def mobile_access():
    if not app.config['MOBILE_ACCESS_KEY'] or request.endpoint in {'static', 'pair_mobile', 'mobile_login'}:
        return None
    if not session.get('mobile_access'):
        if request.path.startswith('/api/'):
            return jsonify(message='Vincula este dispositivo con el enlace de la laptop.'), 401
        return redirect('/vincular')


@app.get('/vincular')
def pair_mobile():
    return render_template('pair.html')


@app.post('/api/acceso')
def mobile_login():
    expected = app.config['MOBILE_ACCESS_KEY']
    supplied = (request.get_json(silent=True) or {}).get('clave', '')
    if not expected or not isinstance(supplied, str) or not secrets.compare_digest(expected.encode(), supplied.encode()):
        return jsonify(message='Enlace inválido. Usa el QR de la laptop.'), 403
    session.clear()
    session['mobile_access'] = True
    return jsonify(ok=True)


def mobile_link():
    config_path = app.config['MOBILE_CONFIG']
    if not config_path:
        return None
    try:
        base = json.loads(Path(config_path).read_text(encoding='utf-8'))['url']
        if not base.startswith('https://') or not base.endswith('.trycloudflare.com'):
            return None
        return base + '/vincular#' + app.config['MOBILE_ACCESS_KEY']
    except (OSError, ValueError, KeyError):
        return None


@app.get('/api/mobile/link')
def mobile_connection():
    return jsonify(url=mobile_link(), enabled=bool(app.config['MOBILE_ACCESS_KEY']))


@app.get('/api/mobile/qr')
def mobile_qr():
    link = mobile_link()
    if not link:
        return jsonify(message='Inicia el modo celular para generar el QR.'), 409
    import qrcode
    from qrcode.image.svg import SvgPathImage
    output = io.BytesIO()
    qrcode.make(link, image_factory=SvgPathImage).save(output)
    return Response(output.getvalue(), mimetype='image/svg+xml', headers={'Cache-Control': 'no-store'})


@app.get('/camara-movil')
def mobile_camera():
    return render_template('mobile.html')


def read_dashboard():
    data = {"available": DB_PATH.exists(), "peatones": 0, "vehiculos": 0,
            "peatones_cruzaron": 0, "vehiculos_cruzaron": 0, "riesgo_medio": 0,
            "riesgo_alto": 0, "fps": 0, "metricas_total": 0, "alertas_total": 0,
            "alertas": [], "ultima": None, "resumen": None}
    if not DB_PATH.exists():
        return data
    try:
        with closing(sqlite3.connect(DB_PATH, timeout=5)) as conn, conn:
            conn.row_factory = sqlite3.Row
            last = conn.execute("SELECT * FROM metricas ORDER BY id DESC LIMIT 1").fetchone()
            counts = conn.execute("SELECT COUNT(*) n FROM metricas").fetchone()
            alert_count = conn.execute("SELECT COUNT(*) n FROM alertas").fetchone()
            alerts = conn.execute("SELECT fecha_hora, nivel, descripcion FROM alertas ORDER BY id DESC LIMIT 12").fetchall()
            max_cross = conn.execute("SELECT SUM(p) p, SUM(v) v FROM (SELECT MAX(peatones_cruzaron) p, MAX(vehiculos_cruzaron) v FROM metricas GROUP BY sesion_id)").fetchone()
            avg_fps = conn.execute("SELECT AVG(fps) f FROM metricas").fetchone()
        data["metricas_total"] = counts["n"]
        data["alertas_total"] = alert_count["n"]
        data["alertas"] = [dict(a) for a in alerts]
        data["peatones_cruzaron"] = int(max_cross["p"] or 0)
        data["vehiculos_cruzaron"] = int(max_cross["v"] or 0)
        data["fps"] = round(float(avg_fps["f"] or 0), 1)
        if last:
            data["ultima"] = dict(last)
            data["peatones"] = last["peatones_frame"]
            data["vehiculos"] = last["vehiculos_frame"]
            data["riesgo_medio"] = last["riesgo_medio_frame"]
            data["riesgo_alto"] = last["riesgo_alto_frame"]
    except sqlite3.Error as exc:
        data["error"] = str(exc)
    return data


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", d=read_dashboard(), p=processor.snapshot(), mode="video")


@app.get('/videos')
def videos():
    return render_template("index.html", d=read_dashboard(), p=processor.snapshot(), mode="video")


@app.get('/en-vivo')
def live_camera():
    return render_template("index.html", d=read_dashboard(), p=processor.snapshot(), mode="camera")


@app.route("/subir-video", methods=["POST"])
def upload_video():
    file = request.files.get("video")
    if not file or not file.filename:
        flash("Selecciona un video antes de iniciar el análisis.", "error")
        return redirect(url_for("videos") + "#monitoreo")
    if not allowed_file(file.filename):
        flash("Formato no compatible. Usa MP4, AVI, MOV, MKV o WEBM.", "error")
        return redirect(url_for("videos") + "#monitoreo")
    if processor.snapshot()["running"]:
        flash("Ya hay un análisis en ejecución. Espera a que termine antes de subir otro video.", "error")
        return redirect(url_for("videos") + "#monitoreo")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    safe_name = secure_filename(file.filename) or "video.mp4"
    # Evita sobrescribir un archivo que ya se subió con el mismo nombre.
    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    target = UPLOADS / safe_name
    number = 2
    while target.exists():
        target = UPLOADS / f"{stem}_{number}{suffix}"
        number += 1
    file.save(target)
    quality = request.form.get('quality')
    started, message = processor.start(str(target), target.name, quality == 'fast',
                                       zone=request.form.get('zona', 'Sin especificar'),
                                       offline=quality == 'offline')
    if not started:
        flash(message, "error")
    else:
        flash(f"Video '{target.name}' cargado. YOLO inició el análisis.", "success")
    return redirect(url_for("videos") + "#monitoreo")


@app.route("/detener", methods=["POST"])
def stop_analysis():
    if processor.stop():
        flash("Se solicitó detener el análisis.", "success")
    else:
        flash("No hay un análisis activo.", "error")
    source = processor.snapshot()['source']
    return redirect(url_for('live_camera' if source == 'camera' else 'videos') + '#monitoreo')


@app.route("/video_feed")
def video_feed():
    def generate():
        version = -1
        while True:
            with processor.condition:
                processor.condition.wait_for(lambda: processor.frame_version != version, timeout=1)
                frame = processor.frame_jpeg
                current = processor.frame_version
            if current == version:
                continue
            version = current
            if frame:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get('/api/video/frame')
def current_video_frame():
    after = request.args.get('after', type=int)
    with processor.condition:
        if after is not None and after == processor.frame_version and processor.running:
            processor.condition.wait_for(lambda: processor.frame_version != after or not processor.running, timeout=.6)
        frame = processor.frame_jpeg
        version = processor.frame_version
    if frame is None or version == after:
        return Response(status=204)
    return Response(frame, mimetype='image/jpeg', headers={'X-Frame-Version': str(version)})


@app.post('/api/video/control')
def video_control():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(message='Solicitud inválida.'), 400
    ok, message = processor.control_video(data.get('action'), data.get('seconds'))
    return jsonify(message=message, analysis=processor.snapshot()), 200 if ok else 409


@app.post("/api/camera/start")
def start_camera():
    data = request.get_json(silent=True) or {}
    started, message = processor.start(None, "Cámara del navegador", zone=data.get('zona', 'Sin especificar'))
    with processor.lock:
        token = processor.camera_token if started else None
    return jsonify(message=message, camera_token=token), 200 if started else 409


@app.post("/api/camera/frame")
def camera_frame():
    if request.content_length is None or request.content_length > 1024 * 1024:
        return jsonify(message="Fotograma demasiado grande (máximo 1 MB)."), 413
    sequence = request.headers.get('X-Frame-Sequence')
    if sequence is not None:
        try:
            sequence = int(sequence)
            if sequence < 1:
                raise ValueError()
        except ValueError:
            return jsonify(message='Secuencia de imagen inválida.'), 400
    token = request.headers.get('X-Camera-Session')
    with processor.lock:
        if processor.camera_token is not None and token != processor.camera_token:
            return jsonify(message='La sesión de cámara cambió. Vuelve a conectar.'), 409
        if sequence is not None and sequence <= processor.camera_sequence:
            return jsonify(ok=True, discarded=True)
    frame = cv2.imdecode(np.frombuffer(request.get_data(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify(message="Imagen inválida."), 400
    # Mantiene las coordenadas del seguimiento aunque el móvil reduzca el JPEG.
    scale = min(1, 960 / max(frame.shape[:2]))
    if abs(scale - 1) > .01:
        frame = cv2.resize(frame, (max(1, round(frame.shape[1] * scale)), max(1, round(frame.shape[0] * scale))))
    if not processor.submit_camera_frame(frame, sequence, token):
        return jsonify(message="La cámara no está activa."), 409
    return jsonify(ok=True)


@app.post("/api/camera/stop")
def stop_camera():
    with processor.lock:
        is_camera = processor.source == "camera"
    if is_camera:
        processor.stop()
    return jsonify(ok=True)


@app.route("/api/dashboard")
def api_dashboard():
    return jsonify({"dashboard": read_dashboard(), "analysis": processor.snapshot()})


@app.post("/api/accident-simulation")
def accident_simulation():
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify(message="Indica enabled como true o false."), 400
    with processor.lock:
        processor.accident_simulation_enabled = enabled
        if not enabled:
            processor.simulated_call_active = False
    return jsonify(ok=True, enabled=enabled)


@app.get("/api/estadisticas")
def statistics():
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        metrics = pd.read_sql_query("SELECT * FROM metricas ORDER BY id DESC LIMIT 3600", conn)
    if metrics.empty:
        return jsonify(muestras=0, por_minuto=[])
    metrics["minuto"] = pd.to_datetime(metrics["fecha_hora"]).dt.floor("min").astype(str)
    summary = metrics.groupby("minuto", as_index=False).agg(
        peatones=("peatones_frame", "mean"), vehiculos=("vehiculos_frame", "mean"),
        fps=("fps", "mean"), riesgo_alto=("riesgo_alto_frame", "max"))
    return jsonify(muestras=len(metrics), por_minuto=summary.round(2).to_dict("records"))


@app.get("/api/tendencias")
def live_trends():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        session = conn.execute("SELECT * FROM sesiones ORDER BY id DESC LIMIT 1").fetchone()
        rows = conn.execute("""SELECT id, fecha_hora, peatones_frame, vehiculos_frame,
            riesgo_medio_frame, riesgo_alto_frame, fps FROM metricas
            WHERE sesion_id=? ORDER BY id DESC LIMIT 120""",
            (session['id'] if session else None,)).fetchall()
    response = jsonify(sesion=dict(session) if session else None,
                       muestras=[dict(row) for row in reversed(rows)])
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get("/api/sesiones")
def sessions():
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM resumen_sesiones ORDER BY id DESC LIMIT 200").fetchall()
    return jsonify([dict(row) for row in rows])


@app.delete("/api/sesiones/<int:session_id>")
def delete_session(session_id):
    with processor.lock:
        if processor.running and processor.session_id == session_id:
            return jsonify(message="Detén el análisis antes de eliminarlo."), 409
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        exists = conn.execute("SELECT 1 FROM sesiones WHERE id=?", (session_id,)).fetchone()
        if not exists:
            return jsonify(message="Ese análisis ya no existe."), 404
        conn.execute("DELETE FROM metricas WHERE sesion_id=?", (session_id,))
        conn.execute("DELETE FROM alertas WHERE sesion_id=?", (session_id,))
        conn.execute("DELETE FROM sesiones WHERE id=?", (session_id,))
        conn.commit()
    video_path = OUTPUTS / "videos" / f"sesion_{session_id}.mp4"
    video_path.unlink(missing_ok=True)
    exportar_reportes()
    return jsonify(ok=True)


@app.post("/api/sesiones/eliminar")
def delete_sessions():
    data = request.get_json(silent=True) or {}
    raw_ids = data.get('ids')
    if not isinstance(raw_ids, list) or not raw_ids or any(type(value) is not int or value < 1 for value in raw_ids):
        return jsonify(message="Selecciona uno o más análisis válidos."), 400
    session_ids = sorted(set(raw_ids))
    with processor.lock:
        if processor.running and processor.session_id in session_ids:
            return jsonify(message="Detén primero el análisis que sigue en ejecución."), 409
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        found = {row[0] for row in conn.execute(
            f"SELECT id FROM sesiones WHERE id IN ({','.join('?' for _ in session_ids)})", session_ids)}
        if found != set(session_ids):
            return jsonify(message="Uno o más análisis ya no existen. Actualiza el historial."), 404
        placeholders = ','.join('?' for _ in session_ids)
        conn.execute(f"DELETE FROM metricas WHERE sesion_id IN ({placeholders})", session_ids)
        conn.execute(f"DELETE FROM alertas WHERE sesion_id IN ({placeholders})", session_ids)
        conn.execute(f"DELETE FROM sesiones WHERE id IN ({placeholders})", session_ids)
        conn.commit()
    for session_id in session_ids:
        (OUTPUTS / "videos" / f"sesion_{session_id}.mp4").unlink(missing_ok=True)
    exportar_reportes()
    return jsonify(ok=True, deleted=len(session_ids))


@app.get("/reportes/sesion/<int:session_id>")
def review_session(session_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT fuente FROM sesiones WHERE id=?", (session_id,)).fetchone()
    if row is None:
        return "Análisis no encontrado", 404
    return render_template("review.html", session_id=session_id, offline=row[0] == 'video_diferido')


@app.get("/api/reportes/sesion/<int:session_id>")
def session_report(session_id):
    from datetime import datetime
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        session_row = conn.execute("SELECT * FROM sesiones WHERE id=?", (session_id,)).fetchone()
        if not session_row:
            return jsonify(message="No se encontró ese análisis."), 404
        rows = conn.execute("""SELECT fecha_hora, video_segundo, peatones_frame, vehiculos_frame,
            peatones_cruzaron, vehiculos_cruzaron, riesgo_medio_frame, riesgo_alto_frame, fps
            FROM metricas WHERE sesion_id=? ORDER BY id""", (session_id,)).fetchall()
    start = datetime.fromisoformat(session_row['inicio'])
    metrics = []
    for row in rows:
        item = dict(row)
        item['segundo'] = (item['video_segundo'] if item['video_segundo'] is not None else
                           max(0, (datetime.fromisoformat(item['fecha_hora']) - start).total_seconds()))
        metrics.append(item)
    video_name = f"sesion_{session_id}.mp4"
    video_path = OUTPUTS / "videos" / video_name
    video_info = None
    if video_path.is_file():
        cap = cv2.VideoCapture(str(video_path))
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS) or 5
            video_info = {"duration": cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps, "fps": fps,
                          "frame_url": f"/api/reportes/sesion/{session_id}/frame",
                          "video_url": f"/reportes/video/{session_id}"}
        cap.release()
    return jsonify(sesion=dict(session_row), muestras=metrics, video=video_info)


@app.get("/api/reportes/sesion/<int:session_id>/frame")
def session_video_frame(session_id):
    try:
        seconds = max(0.0, float(request.args.get('t', '0')))
    except ValueError:
        return jsonify(message="Posición de video inválida."), 400
    video_path = OUTPUTS / "videos" / f"sesion_{session_id}.mp4"
    if not video_path.is_file():
        return jsonify(message="Video no disponible."), 404
    cap = cv2.VideoCapture(str(video_path))
    cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return Response(status=204)
    ok, encoded = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:
        return jsonify(message="No se pudo decodificar el fotograma."), 500
    return Response(encoded.tobytes(), mimetype='image/jpeg', headers={'Cache-Control':'no-store'})


@app.get("/reportes/video/<int:session_id>")
def session_video(session_id):
    video_name = f"sesion_{session_id}.mp4"
    if not (OUTPUTS / "videos" / video_name).is_file():
        return "Video de este reporte no disponible", 404
    return send_from_directory(OUTPUTS / "videos", video_name, mimetype="video/mp4", conditional=True)


@app.get('/api/registros')
def record_explorer():
    try:
        return jsonify(reporting.records(DB_PATH, request.args))
    except ValueError as exc:
        return jsonify(message=str(exc)), 400


@app.get('/api/base-datos')
def database_info():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        counts = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ('sesiones', 'metricas', 'alertas')}
        tables = {table: [{'campo': row[1], 'tipo': row[2]} for row in conn.execute(f'PRAGMA table_info({table})')]
                  for table in counts}
        integrity = conn.execute('PRAGMA quick_check').fetchone()[0]
        fk_errors = len(conn.execute('PRAGMA foreign_key_check').fetchall())
    return jsonify(motor='SQLite', archivo='safewalk.db', tablas=counts, esquema=tables,
                   integridad=integrity, relaciones_invalidas=fk_errors)


@app.get('/reportes/descargar')
def download_filtered_report():
    try:
        if request.args.get('formato', 'csv') == 'zip':
            return send_file(reporting.report_zip(DB_PATH, request.args), as_attachment=True,
                             download_name='safewalk_reporte.zip', mimetype='application/zip')
        frame, _, _ = reporting.report_frames(DB_PATH, request.args)
        return send_file(io.BytesIO(reporting.csv_bytes(frame)), as_attachment=True,
                         download_name='safewalk_registros.csv', mimetype='text/csv')
    except ValueError as exc:
        return jsonify(message=str(exc)), 400


@app.get("/estadisticas.png")
def statistics_chart():
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        metrics = pd.read_sql_query(
            "SELECT * FROM metricas WHERE sesion_id=(SELECT MAX(id) FROM sesiones) ORDER BY id", conn)
    fig = Figure(figsize=(10, 4), layout="constrained")
    ax = fig.subplots()
    if metrics.empty:
        ax.text(.5, .5, "Realiza un análisis para ver las tendencias", ha="center", transform=ax.transAxes)
        ax.set_axis_off()
    else:
        metrics["fecha_hora"] = pd.to_datetime(metrics["fecha_hora"], errors="coerce")
        columns = ["peatones_frame", "vehiculos_frame", "riesgo_alto_frame"]
        clean = metrics.dropna(subset=["fecha_hora"]).set_index("fecha_hora")
        trend = clean[columns].resample("1min").mean()
        for column, label, color in zip(columns, ["Peatones", "Vehículos", "Personas en riesgo alto"], ["#315ee8", "#078ca7", "#c73b48"]):
            ax.plot(trend.index.strftime("%H:%M"), trend[column], marker="o", label=label, color=color)
        ax.set(title="Último análisis: promedio de detecciones por minuto", xlabel="Hora local", ylabel="Objetos por fotograma (promedio)")
        ax.legend()
        ax.grid(alpha=.2)
        ax.tick_params(axis="x", labelrotation=30)
    output = io.BytesIO()
    FigureCanvasAgg(fig).print_png(output)
    output.seek(0)
    return send_file(output, mimetype="image/png", max_age=0)


@app.route("/descargas/<path:filename>")
def download(filename):
    allowed = {"metricas.csv", "alertas.csv", "resumen.csv", "safewalk.db"}
    if filename not in allowed:
        return "Archivo no permitido", 404
    if filename.endswith(".csv"):
        exportar_reportes()
    if filename == "safewalk.db":
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / filename
            with closing(sqlite3.connect(DB_PATH)) as source, closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination)
            content = io.BytesIO(snapshot.read_bytes())
        return send_file(content, as_attachment=True, download_name=filename, mimetype="application/vnd.sqlite3")
    if not (OUTPUTS / filename).is_file():
        return "Todavía no existe este archivo. Ejecuta primero el análisis de SafeWalk.", 404
    return send_from_directory(OUTPUTS, filename, as_attachment=True)


@app.errorhandler(413)
def too_large(_error):
    flash("El archivo supera el límite de 500 MB.", "error")
    return redirect(url_for("videos") + "#monitoreo")


if __name__ == "__main__":
    print(f"SafeWalk Web disponible en http://127.0.0.1:{os.environ.get('SAFEWALK_PORT', '5000')}")
    app.run(host=os.environ.get("SAFEWALK_HOST", "127.0.0.1"), port=int(os.environ.get('SAFEWALK_PORT', '5000')), debug=False, threaded=True)
