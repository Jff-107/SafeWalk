import os
import csv
import time
from datetime import datetime

import cv2
from ultralytics import YOLO

# ---------------------------------------------------------------------------
# Clases de interes para SafeWalk, segun los IDs del dataset COCO que usa el
# modelo YOLO preentrenado (yolov8n.pt). Solo estas tres clases se
# contabilizan en los paneles de conteo. El resto de clases que YOLO pueda
# detectar en el fotograma (bus, truck, bicycle, traffic light, etc.) se
# siguen dibujando con su caja delimitadora normal -gracias a
# resultados[0].plot()-, pero NO se suman a ninguna categoria de SafeWalk,
# tal como se pidio explicitamente (no clasificar buses/camiones/bicicletas
# como autos o motos).
CLASE_PEATON = 0        # 'person'
CLASE_AUTOMOVIL = 2     # 'car'
CLASE_MOTOCICLETA = 3   # 'motorcycle'
CLASES_CONTABILIZADAS = {CLASE_PEATON, CLASE_AUTOMOVIL, CLASE_MOTOCICLETA}

NOMBRES_CATEGORIA = {
    CLASE_PEATON: 'peatones',
    CLASE_AUTOMOVIL: 'automoviles',
    CLASE_MOTOCICLETA: 'motocicletas',
}

# Colores BGR (OpenCV usa BGR, no RGB) para diferenciar cada categoria en el
# panel superpuesto.
COLOR_PEATON = (80, 220, 80)        # verde
COLOR_AUTOMOVIL = (255, 170, 0)     # celeste/azul
COLOR_MOTOCICLETA = (0, 165, 255)   # naranja
COLOR_TEXTO = (255, 255, 255)       # blanco

RUTA_REGISTRO_CSV = 'outputs/registro_conteos.csv'


def _dibujar_panel(frame, visibles, acumulados):
    """Dibuja, sobre el frame ya anotado por YOLO, el panel de conteo con
    dos secciones: lo que hay en pantalla ahora y el acumulado del video."""
    overlay = frame.copy()
    panel_x, panel_y = 10, 70
    panel_w, panel_h = 290, 250
    cv2.rectangle(overlay, (panel_x, panel_y),
                  (panel_x + panel_w, panel_y + panel_h), (20, 20, 20), -1)
    # Fondo semitransparente: mezcla el rectangulo oscuro con el frame real
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, dst=frame)

    def linea(texto, y, color=COLOR_TEXTO, tam=0.5, grosor=1):
        cv2.putText(frame, texto, (panel_x + 10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, tam, color, grosor)

    y = panel_y + 25
    linea('EN PANTALLA AHORA', y, tam=0.55, grosor=2)
    y += 25
    linea(f"Peatones: {visibles['peatones']}", y, COLOR_PEATON)
    y += 20
    linea(f"Automoviles: {visibles['automoviles']}", y, COLOR_AUTOMOVIL)
    y += 20
    linea(f"Motocicletas: {visibles['motocicletas']}", y, COLOR_MOTOCICLETA)
    y += 20
    total_veh_vis = visibles['automoviles'] + visibles['motocicletas']
    linea(f"Vehiculos: {total_veh_vis}", y)
    y += 20
    total_vis = visibles['peatones'] + total_veh_vis
    linea(f"Total objetos: {total_vis}", y)

    y += 35
    linea('ACUMULADO DEL VIDEO', y, tam=0.55, grosor=2)
    y += 25
    linea(f"Peatones: {len(acumulados['peatones'])}", y, COLOR_PEATON)
    y += 20
    linea(f"Automoviles: {len(acumulados['automoviles'])}", y, COLOR_AUTOMOVIL)
    y += 20
    linea(f"Motocicletas: {len(acumulados['motocicletas'])}", y, COLOR_MOTOCICLETA)
    y += 20
    total_veh_acum = len(acumulados['automoviles']) + len(acumulados['motocicletas'])
    linea(f"Vehiculos: {total_veh_acum}", y)
    y += 20
    total_acum = len(acumulados['peatones']) + total_veh_acum
    linea(f"Total general: {total_acum}", y)


def _guardar_registro(nombre_video, acumulados, duracion_seg):
    """Agrega una fila al CSV de registro (no sobrescribe lo ya guardado).

    Se eligio CSV, en lugar de una base de datos nueva, porque SafeWalk
    todavia no tiene ninguna persistencia implementada (SQLite esta
    prevista recien para el Sprint 3), y un CSV es la alternativa mas
    simple compatible con lo que existe hoy.
    """
    os.makedirs('outputs', exist_ok=True)
    existe = os.path.isfile(RUTA_REGISTRO_CSV)

    total_vehiculos = len(acumulados['automoviles']) + len(acumulados['motocicletas'])
    total_general = len(acumulados['peatones']) + total_vehiculos

    with open(RUTA_REGISTRO_CSV, mode='a', newline='', encoding='utf-8') as f:
        escritor = csv.writer(f)
        if not existe:
            escritor.writerow([
                'nombre_video', 'fecha_hora', 'total_peatones',
                'total_automoviles', 'total_motocicletas',
                'total_vehiculos', 'total_general', 'duracion_segundos',
            ])
        escritor.writerow([
            nombre_video,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            len(acumulados['peatones']),
            len(acumulados['automoviles']),
            len(acumulados['motocicletas']),
            total_vehiculos,
            total_general,
            f"{duracion_seg:.1f}" if duracion_seg is not None else '',
        ])


def procesar_video(ruta_video, ruta_modelo='data/models/yolov8n.pt'):
    """Lee un video o camara, detecta y da seguimiento (tracking) a peatones
    y vehiculos, muestra conteos en tiempo real y acumulados sin duplicar
    objetos, y al terminar guarda un resumen en outputs/registro_conteos.csv.
    """

    modelo = YOLO(ruta_modelo)
    captura = cv2.VideoCapture(ruta_video)

    if not captura.isOpened():
        raise ValueError(f"No se pudo abrir el video: {ruta_video}")

    # Duracion del video, si la fuente lo permite (un stream de camara en
    # vivo no reporta un numero de frames valido, por eso se valida).
    fps_video = captura.get(cv2.CAP_PROP_FPS) or 0
    total_frames = captura.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duracion_seg = (total_frames / fps_video) if fps_video > 0 and total_frames > 0 else None

    # Conjuntos de IDs de seguimiento ya contabilizados, uno por categoria.
    # Un mismo track_id solo se agrega una vez (set.add es idempotente), asi
    # un peaton que aparece durante 100 fotogramas se cuenta una sola vez.
    # Esta es la estructura clave para evitar conteos duplicados (seccion 4
    # del pedido). Se reinicia en cada llamada a procesar_video(), asi que
    # un segundo video siempre arranca con contadores en cero.
    acumulados = {
        'peatones': set(),
        'automoviles': set(),
        'motocicletas': set(),
    }

    while True:
        ret, frame = captura.read()
        if not ret:
            break

        tiempo_inicio = time.time()

        # modelo.track() en lugar de modelo(): activa el seguimiento de
        # objetos que Ultralytics trae integrado (ByteTrack). persist=True
        # mantiene los mismos IDs de un fotograma a otro durante toda esta
        # llamada a procesar_video(). La primera vez que se usa .track(),
        # Ultralytics puede instalar automaticamente una dependencia
        # pequena (lap) si no esta presente -ver seccion de dependencias-.
        resultados = modelo.track(
            frame, persist=True, tracker='bytetrack.yaml', verbose=False
        )

        visibles = {'peatones': 0, 'automoviles': 0, 'motocicletas': 0}

        for resultado in resultados:
            if resultado.boxes is None:
                continue
            for caja in resultado.boxes:
                clase_id = int(caja.cls[0])
                if clase_id not in CLASES_CONTABILIZADAS:
                    continue  # bus/truck/bicycle/etc.: se dibujan pero no se cuentan

                categoria = NOMBRES_CATEGORIA[clase_id]
                visibles[categoria] += 1

                # Si el tracker todavia no le asigno un ID a esta caja (puede
                # pasar en los primerisimos fotogramas), no se puede saber
                # si es un objeto nuevo o uno ya contado, asi que por
                # precaucion no se suma al acumulado en ese fotograma
                # puntual; en cuanto el tracker le asigne un ID se contara.
                if caja.id is not None:
                    track_id = int(caja.id[0])
                    acumulados[categoria].add(track_id)

        # Conserva las cajas delimitadoras y etiquetas de deteccion tal como
        # ya las dibujaba el proyecto (incluye las clases no contabilizadas).
        frame_anotado = resultados[0].plot()

        _dibujar_panel(frame_anotado, visibles, acumulados)

        tiempo_fin = time.time()
        fps = 1 / (tiempo_fin - tiempo_inicio)
        alto, ancho = frame_anotado.shape[:2]
        cv2.putText(frame_anotado, f"FPS: {fps:.1f}", (ancho - 130, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_TEXTO, 2)

        cv2.imshow('SafeWalk - Deteccion', frame_anotado)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    captura.release()
    cv2.destroyAllWindows()

    # --- Resumen final (usa los acumulados, no el ultimo frame) ---
    total_vehiculos = len(acumulados['automoviles']) + len(acumulados['motocicletas'])
    total_general = len(acumulados['peatones']) + total_vehiculos

    print('\n===== RESUMEN FINAL - SafeWalk =====')
    print(f'Video procesado: {ruta_video}')
    if duracion_seg is not None:
        print(f'Duracion del video: {duracion_seg:.1f} segundos')
    else:
        print('Duracion del video: no disponible (por ejemplo, camara en vivo)')
    print(f"Peatones unicos registrados: {len(acumulados['peatones'])}")
    print(f"Automoviles unicos registrados: {len(acumulados['automoviles'])}")
    print(f"Motocicletas unicas registradas: {len(acumulados['motocicletas'])}")
    print(f'Total de vehiculos unicos: {total_vehiculos}')
    print(f'Total general de objetos unicos: {total_general}')
    print('=====================================\n')

    _guardar_registro(os.path.basename(str(ruta_video)), acumulados, duracion_seg)
    print(f'Registro guardado en {RUTA_REGISTRO_CSV}\n')


if __name__ == '__main__':
    procesar_video('data/videos/test.mp4')