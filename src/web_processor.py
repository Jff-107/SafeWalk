"""Procesamiento YOLO en segundo plano para la interfaz web de SafeWalk."""
import os
import threading
import time
import queue
import secrets
import cv2
import numpy as np
import torch
from .live_video import LiveVideo
from ultralytics import YOLO
from .main import (ROOT, MODEL_DEFAULT, OUTPUT_DIR, CLASES, evaluar_riesgo,
                   preparar_base_datos, guardar_alerta, guardar_metrica, exportar_reportes,
                   iniciar_sesion, finalizar_sesion)

# Mayor resolución recupera objetos pequeños/lejanos; menor confianza prioriza
# no perder peatones o vehículos parcialmente visibles.
CONFIDENCE_THRESHOLD = .25
FAST_IMAGE_SIZE = 768
PRECISE_IMAGE_SIZE = 832
OFFLINE_IMAGE_SIZE = 960

class WebVideoProcessor:
    def __init__(self):
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.frame_version = 0
        self.camera_frames = queue.Queue(maxsize=1)
        self.camera_latest = None
        self.camera_sequence = 0
        self.camera_token = None
        self.camera_received_fps = 0
        self.camera_last_received = None
        self.camera_display_thread = None
        self.model = None
        self.zone = 'Sin especificar'
        self.source = "video"
        self.fast = True
        self.overlay = None
        self.display_fps = 0
        self.session_id = None
        self.accident_simulation_enabled = False
        self.simulated_call_active = False
        self.simulated_call_count = 0
        self.frame_jpeg = None
        self.status = "Listo para analizar un video"
        self.running = False
        self.stop_requested = False
        self.video_paused = False
        self.video_position = 0.0
        self.video_duration = 0.0
        self.video_seek_target = None
        self.live_cap = None
        self.offline = False
        self.video_name = ""
        self.error = None
        self.thread = None
        self.stats = {"peatones": 0, "vehiculos": 0, "riesgo_medio": 0,
                      "riesgo_alto": 0, "peatones_cruzaron": 0,
                      "vehiculos_cruzaron": 0, "fps": 0}

    def snapshot(self):
        with self.lock:
            return {"running": self.running, "status": self.status,
                    "source": self.source, "has_frame": self.frame_jpeg is not None,
                    "display_fps": round(self.display_fps, 1), "session_id": self.session_id,
                    "camera_received_fps": round(self.camera_received_fps, 1),
                    "camera_age_ms": round((time.monotonic() - self.camera_last_received) * 1000) if self.camera_last_received else None,
                    "accident_simulation_enabled": self.accident_simulation_enabled,
                    "simulated_call_active": self.simulated_call_active,
                    "simulated_call_count": self.simulated_call_count,
                    "video_name": self.video_name, "error": self.error,
                    "video_paused": self.video_paused,
                    "video_position": round(self.video_position, 2),
                    "video_duration": round(self.video_duration, 2),
                    "offline": self.offline,
                    **self.stats}

    def start(self, video_path, video_name, fast=True, zone='Sin especificar', offline=False):
        with self.lock:
            if self.running:
                return False, "Ya hay un análisis en ejecución. Espera a que termine."
            self.running = True
            self.source = "camera" if video_path is None else "video"
            self.fast = fast
            self.offline = bool(offline and video_path is not None)
            self.zone = str(zone).strip()[:120] or 'Sin especificar'
            self.overlay = None
            self.display_fps = 0
            self.session_id = None
            self.camera_latest = None
            self.camera_sequence = 0
            self.camera_received_fps = 0
            self.camera_last_received = None
            self.camera_token = secrets.token_urlsafe(24) if video_path is None else None
            while not self.camera_frames.empty():
                self.camera_frames.get_nowait()
            self.stop_requested = False
            self.video_paused = False
            self.video_position = 0.0
            self.video_duration = 0.0
            self.video_seek_target = None
            self.live_cap = None
            self.frame_jpeg = None
            self.status = "Iniciando modelo YOLO…"
            self.video_name = video_name
            self.error = None
            self.simulated_call_active = False
            self.stats = {"peatones": 0, "vehiculos": 0, "riesgo_medio": 0,
                          "riesgo_alto": 0, "peatones_cruzaron": 0,
                          "vehiculos_cruzaron": 0, "fps": 0}
            self.thread = threading.Thread(target=self._run, args=(video_path,), daemon=True)
            self.thread.start()
            if video_path is None:
                self.camera_display_thread = threading.Thread(target=self._display_camera, args=(self.camera_token,), daemon=True)
                self.camera_display_thread.start()
            return True, "Análisis iniciado"

    def submit_camera_frame(self, frame, sequence=None, token=None):
        with self.lock:
            if not self.running or self.source != "camera" or self.stop_requested:
                return False
            if self.camera_token is not None and token != self.camera_token:
                return False
            if sequence is not None and sequence <= self.camera_sequence:
                return True  # Una petición antigua terminó después de una más reciente.
            now = time.monotonic()
            if self.camera_last_received is not None:
                rate = min(60, 1 / max(now - self.camera_last_received, .001))
                self.camera_received_fps = rate if not self.camera_received_fps else .85 * self.camera_received_fps + .15 * rate
            self.camera_last_received = now
            self.camera_sequence = sequence if sequence is not None else self.camera_sequence + 1
            self.camera_latest = (frame, now, self.camera_sequence)
            if self.camera_frames.full():
                try:
                    self.camera_frames.get_nowait()
                except queue.Empty:
                    pass  # El consumidor pudo retirar la imagen mientras tanto.
            self.camera_frames.put_nowait((frame, now))
            self.condition.notify_all()
            return True

    def _display_camera(self, token):
        """Muestra la imagen recién recibida aunque YOLO siga cargando o analizando."""
        version = 0
        last_publish = None
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stop_requested or not self.running or self.camera_sequence != version, timeout=.25)
                if self.stop_requested or not self.running or token != self.camera_token:
                    return
                latest = self.camera_latest
                if latest is None or latest[2] == version:
                    continue
                version = latest[2]
            self._publish_live(latest[0])
            now = time.monotonic()
            if last_publish is not None:
                with self.lock:
                    rate = min(60, 1 / max(now - last_publish, .001))
                    self.display_fps = rate if not self.display_fps else .85 * self.display_fps + .15 * rate
            last_publish = now

    def stop(self):
        with self.lock:
            if self.running:
                self.stop_requested = True
                self.status = "Deteniendo análisis…"
                return True
            return False

    def control_video(self, action, seconds=None):
        with self.lock:
            if not self.running or self.source != "video" or self.offline:
                return False, "No hay un video en análisis."
            if action == "pause":
                self.video_paused = True
            elif action == "play":
                self.video_paused = False
            elif action in ("seek", "forward", "back"):
                if action == "seek":
                    if not isinstance(seconds, (int, float)) or not 0 <= seconds <= self.video_duration:
                        return False, "Posición fuera del video."
                    target = seconds
                else:
                    position = self.video_seek_target if self.video_seek_target is not None else self.video_position
                    target = position + (10 if action == "forward" else -10)
                self.video_seek_target = max(0.0, min(float(target), max(0.0, self.video_duration - .01)))
                self.overlay = None
            else:
                return False, "Control no reconocido."
            if self.live_cap is not None:
                self.live_cap.control(paused=self.video_paused, seek=self.video_seek_target)
                self.video_seek_target = None
            return True, "Control aplicado."

    def _run(self, video_path):
        cap = None
        recording = None
        session_id = None
        try:
            if not os.path.isfile(MODEL_DEFAULT):
                raise FileNotFoundError(f"No se encontró el modelo YOLO: {MODEL_DEFAULT}")
            preparar_base_datos()
            session_id = iniciar_sesion('video_diferido' if self.offline else self.source, self.video_name, self.zone)
            with self.lock:
                self.session_id = session_id
            if not torch.cuda.is_available():
                # Reserva CPU para decodificar, dibujar y transmitir el video.
                torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
            size = OFFLINE_IMAGE_SIZE if self.offline else (FAST_IMAGE_SIZE if self.fast else PRECISE_IMAGE_SIZE)
            if self.model is None:
                self._set_status("Preparando YOLO; la cámara ya puede mostrar imágenes…")
                self.model = YOLO(MODEL_DEFAULT)
                self.model.predict(np.zeros((size, size, 3), dtype=np.uint8), imgsz=size, verbose=False)
            model = self.model
            for tracker in getattr(getattr(model, 'predictor', None), 'trackers', []):
                tracker.reset()
            cap = (LiveVideo(video_path, self._publish_live, lambda: self.stop_requested)
                   if self.fast and video_path is not None and not self.offline else
                   cv2.VideoCapture(video_path) if video_path is not None else None)
            if isinstance(cap, LiveVideo):
                with self.lock:
                    self.live_cap = cap
                    cap.control(paused=self.video_paused, seek=self.video_seek_target)
                    self.video_seek_target = None
            if cap is not None and not cap.isOpened():
                raise ValueError("No se pudo abrir el video. Prueba con MP4, AVI o MOV.")
            width, height = 640, 480
            source_fps = (cap.get(cv2.CAP_PROP_FPS) if cap is not None else 0) or 30
            if not 1 <= source_fps <= 240:
                source_fps = 30
            if cap is not None:
                with self.lock:
                    self.video_duration = max(0.0, cap.get(cv2.CAP_PROP_FRAME_COUNT) / source_fps)
            video_dir = os.path.join(OUTPUT_DIR, "videos")
            os.makedirs(video_dir, exist_ok=True)
            recording_path = os.path.join(video_dir, f"sesion_{session_id}.mp4")
            recording_fps = source_fps if video_path is not None else 5
            playback_start = None
            source_index = 0
            last_epoch = 0
            line_y = int(height * .65)
            previous, counted = {}, set()
            tracking_shape = None
            total_p, total_v = 0, 0
            p_count = v_count = medium = high = 0
            fps = 0.0
            last_save = time.time()
            last_saved_counts = None
            last_alert = {}
            overlap_frames = {}
            last_simulated_call = 0.0
            last_camera_frame = time.monotonic()
            self._set_status("Analizando video con YOLO…")
            while not self.stop_requested:
                t0 = time.perf_counter()
                if cap is not None and not isinstance(cap, LiveVideo):
                    with self.lock:
                        target = self.video_seek_target
                        self.video_seek_target = None
                        paused = self.video_paused
                    if target is not None:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, round(target * source_fps))
                        source_index = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                        playback_start = None
                        previous.clear()
                        counted.clear()
                        overlap_frames.clear()
                        total_p = total_v = 0
                        for tracker in getattr(getattr(model, 'predictor', None), 'trackers', []):
                            tracker.reset()
                        with self.lock:
                            self.video_position = source_index / source_fps
                    if paused and target is None:
                        time.sleep(.05)
                        continue
                if cap is None:
                    try:
                        frame, frame_received_at = self.camera_frames.get(timeout=.25)
                    except queue.Empty:
                        if time.monotonic() - last_camera_frame > 30:
                            raise TimeoutError("La cámara dejó de enviar imágenes durante 30 segundos. Actívala nuevamente.")
                        continue
                    last_camera_frame = time.monotonic()
                    if last_camera_frame - frame_received_at > .75:
                        continue
                    t0 = time.perf_counter()
                else:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if isinstance(cap, LiveVideo):
                        source_index = cap.frame_index
                        if cap.frame_epoch != last_epoch:
                            last_epoch = cap.frame_epoch
                            previous.clear()
                            counted.clear()
                            overlap_frames.clear()
                            total_p = total_v = 0
                            for tracker in getattr(getattr(model, 'predictor', None), 'trackers', []):
                                tracker.reset()
                    else:
                        source_index += 1
                        with self.lock:
                            self.video_position = source_index / source_fps
                    t0 = time.perf_counter()
                # Limita tambien la salida grabada: codificar un 1080p completo
                # por cada inferencia ralentiza la reproduccion en CPU.
                max_edge = None if self.offline else (960 if self.fast else 1280)
                longest_edge = max(frame.shape[:2])
                if max_edge is not None and longest_edge > max_edge:
                    scale = max_edge / longest_edge
                    frame = cv2.resize(frame, (round(frame.shape[1] * scale),
                                               round(frame.shape[0] * scale)))
                if self.offline and (frame.shape[0] % 2 or frame.shape[1] % 2):
                    frame = frame[:frame.shape[0] - frame.shape[0] % 2,
                                  :frame.shape[1] - frame.shape[1] % 2]
                height, width = frame.shape[:2]
                line_y = int(height * .65)
                if cap is None and tracking_shape is not None and tracking_shape != (height, width):
                    # Un giro cambia el sistema de coordenadas; no es un cruce vial.
                    previous.clear()
                    counted.clear()
                    for tracker in getattr(getattr(model, 'predictor', None), 'trackers', []):
                        tracker.reset()
                tracking_shape = (height, width)
                result = model.track(frame, persist=True, tracker="bytetrack.yaml",
                                     classes=list(CLASES.keys()), conf=CONFIDENCE_THRESHOLD,
                                     imgsz=size,
                                     verbose=False)[0]
                boxes = result.boxes
                ids = boxes.id.int().cpu().tolist() if boxes.id is not None else [None] * len(boxes)
                detections, vehicle_boxes, people = [], [], []
                p_count = v_count = 0
                for box, track_id in zip(boxes, ids):
                    cls = int(box.cls[0].item())
                    if cls not in CLASES:
                        continue
                    xyxy = tuple(int(v) for v in box.xyxy[0].cpu().tolist())
                    item = {"clase": cls, "nombre": CLASES[cls], "caja": xyxy,
                            "track_id": track_id, "confianza": float(box.conf[0].item())}
                    detections.append(item)
                    if cls == 0:
                        p_count += 1
                        people.append(item)
                    else:
                        v_count += 1
                        vehicle_boxes.append(xyxy)
                    if track_id is not None:
                        x1, y1, x2, y2 = xyxy
                        cy = (y1+y2)//2
                        old = previous.get(track_id)
                        if old and track_id not in counted:
                            old_y, _ = old
                            if (old_y < line_y <= cy) or (cy < line_y <= old_y):
                                counted.add(track_id)
                                if cls == 0: total_p += 1
                                else: total_v += 1
                        previous[track_id] = (cy, cls)
                risks=[]; medium=high=0
                overlapping_pairs = set()
                for person in people:
                    level, distance = evaluar_riesgo(person["caja"], vehicle_boxes)
                    risks.append((person, level))
                    if level == "MEDIO": medium += 1
                    elif level == "ALTO": high += 1
                    px1, py1, px2, py2 = person["caja"]
                    person_id = person["track_id"]
                    for vehicle in detections:
                        if vehicle["clase"] == 0:
                            continue
                        vx1, vy1, vx2, vy2 = vehicle["caja"]
                        intersection = max(0, min(px2, vx2) - max(px1, vx1)) * max(0, min(py2, vy2) - max(py1, vy1))
                        smaller_area = min(max(1, (px2-px1)*(py2-py1)), max(1, (vx2-vx1)*(vy2-vy1)))
                        if intersection / smaller_area >= .20 and person_id is not None and vehicle["track_id"] is not None:
                            overlapping_pairs.add((person_id, vehicle["track_id"]))
                overlap_frames = {pair: overlap_frames.get(pair, 0) + 1 for pair in overlapping_pairs}
                with self.lock:
                    simulation_enabled = self.accident_simulation_enabled
                now_mono = time.monotonic()
                if simulation_enabled and any(count >= 3 for count in overlap_frames.values()) and now_mono - last_simulated_call >= 30:
                    last_simulated_call = now_mono
                    with self.lock:
                        self.simulated_call_active = True
                        self.simulated_call_count += 1
                    guardar_alerta("ALTO", "SIMULACIÓN: posible accidente por solapamiento persistente de peatón y vehículo; llamada simulada a emergencias.", session_id)
                overlap_frames = {pair: count for pair, count in overlap_frames.items() if pair in overlapping_pairs}
                out = frame.copy()
                for d in detections:
                    x1,y1,x2,y2=d["caja"]
                    color=(255,150,0) if d["clase"] == 0 else (0,200,255)
                    label=f'{d["nombre"]} {d["confianza"]:.2f}'
                    if d["clase"] == 0:
                        for person, level in risks:
                            if person is d:
                                color={"BAJO":(0,200,0),"MEDIO":(0,200,255),"ALTO":(0,0,255)}[level]
                                label=f'Peaton - Riesgo {level}'
                                break
                    cv2.rectangle(out,(x1,y1),(x2,y2),color,2)
                    cv2.putText(out,label,(x1,max(22,y1-8)),cv2.FONT_HERSHEY_SIMPLEX,.55,color,2)
                cv2.line(out,(0,line_y),(width,line_y),(255,255,0),2)
                cv2.putText(out,"LINEA DE CONTEO",(10,max(20,line_y-8)),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,0),1)
                fps=1/max(time.perf_counter()-t0,.001)
                alert_level="ALTO" if high else ("MEDIO" if medium else None)
                with self.lock:
                    call_active = self.simulated_call_active
                if call_active:
                    cv2.rectangle(out,(10,105),(min(width-10,570),145),(0,0,180),-1)
                    cv2.putText(out,"LLAMADA SIMULADA A EMERGENCIAS",(18,132),cv2.FONT_HERSHEY_SIMPLEX,.62,(255,255,255),2)
                if recording is None:
                    codec = "avc1" if self.offline else "mp4v"
                    recording = cv2.VideoWriter(recording_path, cv2.VideoWriter_fourcc(*codec), recording_fps, (width, height))
                    if not recording.isOpened():
                        recording.release()
                        recording = None
                        raise RuntimeError("No se pudo crear el video del reporte.")
                recording.write(out)
                if alert_level:
                    alert_text=f"ALERTA DE RIESGO {alert_level}"
                    alert_color=(0,0,255) if alert_level=="ALTO" else (0,165,255)
                    cv2.rectangle(out,(10,height-55),(min(width-10,470),height-12),alert_color,-1)
                    cv2.putText(out,alert_text,(20,height-27),cv2.FONT_HERSHEY_SIMPLEX,.75,(255,255,255),2)
                    now=time.time()
                    if now-last_alert.get(alert_level,0)>=5:
                        guardar_alerta(alert_level, f"Personas en riesgo {alert_level.lower()}: {high if high else medium}", session_id)
                        last_alert[alert_level]=now
                now=time.time()
                counts = (p_count, v_count, total_p, total_v, medium, high)
                # Una deteccion breve puede desaparecer antes de la siguiente muestra
                # periodica; guarda tambien cada cambio de los contadores.
                if now-last_save>=1 or counts != last_saved_counts:
                    guardar_metrica(p_count,v_count,total_p,total_v,fps,medium,high,session_id,
                                    source_index / source_fps if self.offline else None)
                    last_save=now
                    last_saved_counts=counts
                with self.lock:
                    self.overlay = (frame_received_at if cap is None else time.monotonic(), detections, risks, frame.shape[:2])
                    self.stats={"peatones":p_count,"vehiculos":v_count,"riesgo_medio":medium,
                                "riesgo_alto":high,"peatones_cruzaron":total_p,
                                "vehiculos_cruzaron":total_v,"fps":round(fps,1)}
                    if isinstance(cap, LiveVideo):
                        self.display_fps = cap.display_fps
                if cap is not None and not self.offline and not isinstance(cap, LiveVideo):
                    ok, encoded=cv2.imencode('.jpg',out,[int(cv2.IMWRITE_JPEG_QUALITY),70])
                    if ok:
                        with self.lock:
                            self.display_fps = fps
                            self.frame_jpeg=encoded.tobytes()
                            self.frame_version += 1
                            self.condition.notify_all()
                if cap is not None and not self.offline and not isinstance(cap, LiveVideo):
                    if playback_start is None:
                        playback_start = time.perf_counter() - source_index / source_fps
                    delay = playback_start + source_index / source_fps - time.perf_counter()
                    if delay > 0:
                        time.sleep(min(delay, .1))
            guardar_metrica(p_count,v_count,total_p,total_v,fps,medium,high,session_id,
                            source_index / source_fps if self.offline else None)
            exportar_reportes()
            self._set_status("Análisis detenido" if self.stop_requested else "Análisis finalizado")
        except Exception as exc:
            with self.lock:
                self.error=str(exc)
                self.status="Error en el análisis"
        finally:
            if recording is not None:
                recording.release()
            if cap is not None:
                cap.release()
            try:
                if session_id is not None:
                    finalizar_sesion(session_id, "error" if self.error else ("detenido" if self.stop_requested else "finalizado"))
            finally:
                with self.lock:
                    self.running=False
                    self.live_cap=None
                    self.condition.notify_all()

    def _set_status(self, status):
        with self.lock:
            self.status=status

    def _publish_live(self, frame):
        """No espera a YOLO: muestra fotogramas nuevos con la detección más reciente."""
        with self.lock:
            overlay = self.overlay
            stats = dict(self.stats)
            if self.live_cap is not None and self.source == "video":
                self.video_position = self.live_cap.playback_position()
        out = frame.copy()
        height, width = out.shape[:2]
        if overlay is not None and time.monotonic() - overlay[0] <= .5 and overlay[3] == frame.shape[:2]:
            _, detections, risks, _ = overlay
            levels = {id(person): level for person, level in risks}
            for detection in detections:
                x1, y1, x2, y2 = detection['caja']
                level = levels.get(id(detection))
                color = {'BAJO': (0, 200, 0), 'MEDIO': (0, 200, 255), 'ALTO': (0, 0, 255)}.get(level, (0, 200, 255))
                label = f"Peaton: {level}" if level else detection['nombre']
                cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
                cv2.putText(out, label, (x1, max(20, y1-6)), cv2.FONT_HERSHEY_SIMPLEX, .5, color, 1)
        line_y = int(height * .65)
        cv2.line(out, (0, line_y), (width, line_y), (255, 255, 0), 2)
        if stats['riesgo_alto'] or stats['riesgo_medio']:
            level = 'ALTO' if stats['riesgo_alto'] else 'MEDIO'
            color = (0, 0, 255) if level == 'ALTO' else (0, 165, 255)
            cv2.rectangle(out, (0, height-35), (width, height), color, -1)
            cv2.putText(out, f'RIESGO {level}', (10, height-12), cv2.FONT_HERSHEY_SIMPLEX, .6, (255,255,255), 2)
        ok, encoded = cv2.imencode('.jpg', out, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ok:
            with self.condition:
                self.frame_jpeg = encoded.tobytes()
                self.frame_version += 1
                self.condition.notify_all()

processor=WebVideoProcessor()
