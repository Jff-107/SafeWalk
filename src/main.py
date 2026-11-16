"""
SafeWalk - Incremento 02: prevención de riesgos.
Uso:
  python -m src.main data/videos/videoTransito.mp4
  python -m src.main 0                 # webcam
  python -m src.main                   # usa data/videos/videoTransito.mp4
Salir: tecla q
"""
from contextlib import closing
import argparse
import os
import sqlite3
import time
from datetime import datetime

import cv2
import pandas as pd
from ultralytics import YOLO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DEFAULT = os.path.join(ROOT, "data", "models", "yolov8n.pt")
VIDEO_DEFAULT = os.path.join(ROOT, "data", "videos", "videoTransito.mp4")
OUTPUT_DIR = os.path.join(ROOT, "outputs")
DB_PATH = os.path.join(OUTPUT_DIR, "safewalk.db")

# Clases COCO de YOLOv8: persona, bicicleta, auto, moto, bus y camión.
CLASES = {0: "Peatón", 1: "Bicicleta", 2: "Auto", 3: "Motocicleta",
          5: "Bus", 7: "Camión"}
VEHICULOS = {1, 2, 3, 5, 7}


def database_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def preparar_base_datos():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with closing(database_connection()) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS sesiones (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fuente TEXT NOT NULL,
            nombre TEXT NOT NULL, inicio TEXT NOT NULL, fin TEXT, estado TEXT NOT NULL
        )""")
        if 'zona' not in {row[1] for row in conn.execute('PRAGMA table_info(sesiones)')}:
            conn.execute("ALTER TABLE sesiones ADD COLUMN zona TEXT NOT NULL DEFAULT 'Sin especificar'")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metricas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora TEXT NOT NULL,
                peatones_frame INTEGER NOT NULL,
                vehiculos_frame INTEGER NOT NULL,
                peatones_cruzaron INTEGER NOT NULL,
                vehiculos_cruzaron INTEGER NOT NULL,
                fps REAL NOT NULL,
                riesgo_medio_frame INTEGER NOT NULL,
                riesgo_alto_frame INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alertas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora TEXT NOT NULL,
                nivel TEXT NOT NULL,
                descripcion TEXT NOT NULL
            )
        """)
        for tabla in ("metricas", "alertas"):
            columnas = {row[1] for row in conn.execute(f"PRAGMA table_info({tabla})")}
            if "sesion_id" not in columnas:
                conn.execute(f"ALTER TABLE {tabla} ADD COLUMN sesion_id INTEGER REFERENCES sesiones(id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabla}_sesion ON {tabla}(sesion_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabla}_fecha ON {tabla}(fecha_hora)")
        conn.execute('''CREATE VIEW IF NOT EXISTS resumen_sesiones AS SELECT s.*,
            (SELECT COUNT(*) FROM metricas m WHERE m.sesion_id=s.id) AS metricas_total,
            (SELECT COUNT(*) FROM alertas a WHERE a.sesion_id=s.id) AS alertas_total,
            (SELECT MAX(m.peatones_cruzaron) FROM metricas m WHERE m.sesion_id=s.id) AS cruces_peatonales,
            (SELECT MAX(m.vehiculos_cruzaron) FROM metricas m WHERE m.sesion_id=s.id) AS cruces_vehiculares
            FROM sesiones s''')
        conn.execute('PRAGMA user_version=2')
        conn.commit()


def iniciar_sesion(fuente, nombre, zona='Sin especificar'):
    with closing(database_connection()) as conn, conn:
        return conn.execute(
            "INSERT INTO sesiones(fuente,nombre,inicio,estado,zona) VALUES (?,?,?,?,?)",
            (fuente, nombre, datetime.now().isoformat(timespec="seconds"), "en curso", zona)
        ).lastrowid


def finalizar_sesion(sesion_id, estado):
    with closing(database_connection()) as conn, conn:
        conn.execute("UPDATE sesiones SET fin=?, estado=? WHERE id=?",
                     (datetime.now().isoformat(timespec="seconds"), estado, sesion_id))


def distancia_cajas(a, b):
    """Distancia mínima entre rectángulos en píxeles; cero si se superponen."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    dx = max(ax1 - bx2, bx1 - ax2, 0)
    dy = max(ay1 - by2, by1 - ay2, 0)
    return (dx * dx + dy * dy) ** 0.5


def evaluar_riesgo(caja_persona, cajas_vehiculos):
    """
    Heurística visual inicial basada en distancia entre bounding boxes.
    No representa probabilidad de atropello validada ni reemplaza evaluación vial.
    """
    if not cajas_vehiculos:
        return "BAJO", None
    x1, y1, x2, y2 = caja_persona
    altura_persona = max(y2 - y1, 1)
    distancia = min(distancia_cajas(caja_persona, v) for v in cajas_vehiculos)
    relativa = distancia / altura_persona
    if relativa <= 0.35:
        return "ALTO", distancia
    if relativa <= 0.90:
        return "MEDIO", distancia
    return "BAJO", distancia


def guardar_alerta(nivel, descripcion, sesion_id=None):
    with closing(database_connection()) as conn, conn:
        conn.execute(
            "INSERT INTO alertas(fecha_hora, nivel, descripcion, sesion_id) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), nivel, descripcion, sesion_id)
        )
        conn.commit()


def guardar_metrica(peatones, vehiculos, peatones_cruzaron, vehiculos_cruzaron,
                    fps, medios, altos, sesion_id=None):
    with closing(database_connection()) as conn, conn:
        conn.execute("""
            INSERT INTO metricas(fecha_hora, peatones_frame, vehiculos_frame,
                peatones_cruzaron, vehiculos_cruzaron, fps,
                riesgo_medio_frame, riesgo_alto_frame, sesion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(timespec="seconds"), peatones, vehiculos,
              peatones_cruzaron, vehiculos_cruzaron, fps, medios, altos, sesion_id))
        conn.commit()


def exportar_reportes():
    """Exporta las tablas SQLite a CSV para facilitar su revisión."""
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        metricas = pd.read_sql_query("SELECT * FROM metricas ORDER BY id", conn)
        alertas = pd.read_sql_query("SELECT * FROM alertas ORDER BY id", conn)
    metricas.to_csv(os.path.join(OUTPUT_DIR, "metricas.csv"), index=False, encoding="utf-8-sig")
    alertas.to_csv(os.path.join(OUTPUT_DIR, "alertas.csv"), index=False, encoding="utf-8-sig")
    if not metricas.empty:
        resumen = pd.DataFrame([{
            "registros_totales": len(metricas),
            "max_peatones_en_un_frame": int(metricas["peatones_frame"].max()),
            "max_vehiculos_en_un_frame": int(metricas["vehiculos_frame"].max()),
            "peatones_que_cruzaron": int(metricas.groupby("sesion_id", dropna=False)["peatones_cruzaron"].max().sum()),
            "vehiculos_que_cruzaron": int(metricas.groupby("sesion_id", dropna=False)["vehiculos_cruzaron"].max().sum()),
            "alertas_registradas": len(alertas),
            "fps_promedio": round(float(metricas["fps"].mean()), 2)
        }])
        resumen.to_csv(os.path.join(OUTPUT_DIR, "resumen.csv"), index=False, encoding="utf-8-sig")
    print(f"\nDatos guardados en: {OUTPUT_DIR}")
    print(f"Base de datos: {DB_PATH}")


def procesar_video(origen, ruta_modelo=MODEL_DEFAULT, confianza=0.35):
    preparar_base_datos()
    if not os.path.isfile(ruta_modelo):
        raise FileNotFoundError(
            f"No se encontró el modelo YOLO: {ruta_modelo}. "
            "Coloca yolov8n.pt dentro de data/models/."
        )

    modelo = YOLO(ruta_modelo)
    captura = cv2.VideoCapture(origen)
    if not captura.isOpened():
        raise ValueError(
            f"No se pudo abrir el origen de video: {origen}. "
            "Comprueba que el archivo existe o utiliza 0 para la webcam."
        )

    ancho = int(captura.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
    alto = int(captura.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
    linea_y = int(alto * 0.65)
    anteriores = {}  # track_id -> (centro_y, clase_id)
    ya_contados = set()
    total_peatones_cruzaron = 0
    total_vehiculos_cruzaron = 0
    ultimo_guardado = time.time()
    ultimo_aviso = {}
    inicio = time.time()
    frames = 0
    print("SafeWalk Incremento 02 iniciado. Presiona q para finalizar.")

    try:
        while True:
            ok, frame = captura.read()
            if not ok:
                break
            t0 = time.time()
            # ByteTrack intenta mantener IDs entre fotogramas para contar cruces.
            resultados = modelo.track(
                frame, persist=True, tracker="bytetrack.yaml",
                classes=list(CLASES.keys()), conf=confianza, verbose=False
            )
            resultado = resultados[0]
            detecciones = []
            cajas_vehiculos = []
            cajas_personas = []
            peatones = vehiculos = 0

            ids = resultado.boxes.id
            ids_lista = ids.int().cpu().tolist() if ids is not None else [None] * len(resultado.boxes)

            for caja, track_id in zip(resultado.boxes, ids_lista):
                clase_id = int(caja.cls[0].item())
                if clase_id not in CLASES:
                    continue
                xyxy = tuple(int(v) for v in caja.xyxy[0].cpu().tolist())
                deteccion = {"clase": clase_id, "nombre": CLASES[clase_id],
                             "caja": xyxy, "track_id": track_id,
                             "confianza": float(caja.conf[0].item())}
                detecciones.append(deteccion)
                if clase_id == 0:
                    peatones += 1
                    cajas_personas.append(deteccion)
                else:
                    vehiculos += 1
                    cajas_vehiculos.append(xyxy)

                # Conteo aproximado de flujo al cruzar una línea horizontal.
                if track_id is not None:
                    x1, y1, x2, y2 = xyxy
                    centro_y = (y1 + y2) // 2
                    previo = anteriores.get(track_id)
                    if previo is not None and track_id not in ya_contados:
                        y_anterior, clase_anterior = previo
                        cruzo = (y_anterior < linea_y <= centro_y) or (centro_y < linea_y <= y_anterior)
                        if cruzo:
                            ya_contados.add(track_id)
                            if clase_id == 0:
                                total_peatones_cruzaron += 1
                            else:
                                total_vehiculos_cruzaron += 1
                    anteriores[track_id] = (centro_y, clase_id)

            # Bounding boxes y etiquetas con riesgo para peatones.
            frame_out = frame.copy()
            medios = altos = 0
            riesgos = []
            for persona in cajas_personas:
                nivel, distancia = evaluar_riesgo(persona["caja"], cajas_vehiculos)
                riesgos.append((persona, nivel, distancia))
                if nivel == "MEDIO":
                    medios += 1
                elif nivel == "ALTO":
                    altos += 1

            for d in detecciones:
                x1, y1, x2, y2 = d["caja"]
                color = (255, 150, 0) if d["clase"] == 0 else (0, 200, 255)
                etiqueta = f'{d["nombre"]} {d["confianza"]:.2f}'
                if d["clase"] == 0:
                    for persona, nivel, _ in riesgos:
                        if persona is d:
                            color = {"BAJO": (0, 200, 0), "MEDIO": (0, 200, 255),
                                     "ALTO": (0, 0, 255)}[nivel]
                            etiqueta = f'Peaton - Riesgo {nivel}'
                            break
                cv2.rectangle(frame_out, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame_out, etiqueta, (x1, max(22, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

            # Línea de conteo y panel de estadísticas.
            cv2.line(frame_out, (0, linea_y), (ancho, linea_y), (255, 255, 0), 2)
            cv2.putText(frame_out, "LINEA DE CONTEO", (10, max(20, linea_y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
            dt = max(time.time() - t0, 0.001)
            fps = 1.0 / dt
            panel = [
                f"PEATONES: {peatones} | VEHICULOS: {vehiculos}",
                f"CRUCES: peatones {total_peatones_cruzaron} | vehiculos {total_vehiculos_cruzaron}",
                f"RIESGO MEDIO: {medios} | ALTO: {altos} | FPS: {fps:.1f}"
            ]
            cv2.rectangle(frame_out, (0, 0), (min(ancho, 760), 94), (20, 25, 40), -1)
            for i, linea in enumerate(panel):
                cv2.putText(frame_out, linea, (12, 25 + i * 29),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)

            # Alerta visual destacada y registro con intervalo para evitar duplicados por frame.
            nivel_alerta = "ALTO" if altos else ("MEDIO" if medios else None)
            if nivel_alerta:
                texto = f"ALERTA DE RIESGO {nivel_alerta}"
                color_alerta = (0, 0, 255) if nivel_alerta == "ALTO" else (0, 165, 255)
                cv2.rectangle(frame_out, (10, alto - 55), (min(ancho - 10, 470), alto - 12),
                              color_alerta, -1)
                cv2.putText(frame_out, texto, (20, alto - 27),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
                ahora = time.time()
                if ahora - ultimo_aviso.get(nivel_alerta, 0) >= 5:
                    guardar_alerta(nivel_alerta,
                                   f"Personas en riesgo {nivel_alerta.lower()}: {altos if altos else medios}")
                    ultimo_aviso[nivel_alerta] = ahora

            frames += 1
            ahora = time.time()
            # Guardar un resumen aproximadamente cada segundo, no cada fotograma.
            if ahora - ultimo_guardado >= 1:
                guardar_metrica(peatones, vehiculos, total_peatones_cruzaron,
                                total_vehiculos_cruzaron, fps, medios, altos)
                ultimo_guardado = ahora

            cv2.imshow("SafeWalk - Incremento 02 | q para salir", frame_out)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        # Guardar métrica final incluso si el video duró menos de un segundo.
        guardar_metrica(0, 0, total_peatones_cruzaron, total_vehiculos_cruzaron,
                        frames / max(time.time() - inicio, 0.001), 0, 0)
    finally:
        captura.release()
        cv2.destroyAllWindows()
        exportar_reportes()


def main():
    parser = argparse.ArgumentParser(description="SafeWalk - Incremento 02")
    parser.add_argument("video", nargs="?", default=VIDEO_DEFAULT,
                        help="Ruta del video o índice de cámara, por ejemplo 0")
    parser.add_argument("--modelo", default=MODEL_DEFAULT, help="Ruta al archivo .pt")
    parser.add_argument("--conf", type=float, default=0.35,
                        help="Confianza mínima de detección (0 a 1)")
    args = parser.parse_args()
    origen = int(args.video) if args.video.isdigit() else args.video
    procesar_video(origen, args.modelo, args.conf)


if __name__ == "__main__":
    main()
