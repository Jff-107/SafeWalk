import os
import csv
import time
from datetime import datetime

import cv2
import numpy as np
from ultralytics import YOLO

# HU06: organizacion de los datos de deteccion (modulo src/datos_deteccion.py)
from datos_deteccion import RegistroDetecciones, construir_detecciones
# HU07: clasificacion del nivel de riesgo (modulo src/riesgo.py)
from riesgo import AnalizadorRiesgo, NOMBRES_NIVEL, BAJO, MEDIO, ALTO

# Paleta del documento del proyecto (Tabla de colores), en BGR para OpenCV:
# verde #2ECC71, amarillo #F39C12, rojo #E74C3C.
COLOR_NIVEL = {BAJO: (113, 204, 46), MEDIO: (18, 156, 243), ALTO: (60, 76, 231)}

# Si es True, al terminar cada video se guarda outputs/detecciones_<video>_<fecha>.csv
# con una fila por objeto detectado en cada fotograma (evidencia del Sprint 2).
EXPORTAR_DETECCIONES_CSV = True

# ---------------------------------------------------------------------------
# Clases de interes para SafeWalk, segun los IDs del dataset COCO que usa el
# modelo YOLO preentrenado (yolov8n.pt). Solo estas tres clases se
# contabilizan en los paneles de conteo. El resto de clases que YOLO pueda
# detectar en el fotograma (bus, truck, bicycle, traffic light, etc.) se
# siguen dibujando con su caja delimitadora normal -gracias a
# resultados[0].plot()-, pero NO se suman a ninguna categoria de SafeWalk.
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

# Tracker: BoT-SORT con track_buffer ampliado (ver botsort_safewalk.yaml y
# la explicacion que acompaña la entrega). Ruta relativa a la carpeta raiz
# del proyecto, igual que 'data/models/yolov8n.pt'.
RUTA_TRACKER = 'botsort_safewalk.yaml'

# ---------------------------------------------------------------------------
# CAPA 2: reasociacion propia de trayectorias perdidas.
#
# El tracker (capa 1) ya resiste oclusiones cortas gracias al track_buffer
# ampliado. Esta capa es un respaldo adicional: actua solo cuando, pese a
# eso, el tracker descarta una trayectoria y le asigna un ID crudo nuevo al
# reaparecer. Antes de contar ese ID nuevo como un objeto distinto, se
# evalua si corresponde, con buena evidencia, a una trayectoria que se
# acaba de perder.
#
# Las cuatro condiciones de abajo deben cumplirse TODAS (no basta con una)
# para recuperar una identidad; esto evita fusionar por error a dos
# personas distintas que simplemente pasan cerca una de la otra.
DEBUG_TRACKING = True  # Pon esto en False para silenciar los mensajes de depuracion.

# Tiempo maximo (segundos) durante el cual una trayectoria perdida sigue
# siendo candidata a reasociacion. Mas alla de esto, se descarta: no hay
# evidencia suficientemente confiable de que siga siendo la misma persona.
# Se eligio un poco mas que el track_buffer del tracker (240 frames ~ 8 s a
# 30 FPS, ver botsort_safewalk.yaml) para cubrir el margen en el que el
# tracker no alcance a resolverlo por si solo. (Se subio desde 4.0: con las
# oclusiones reales de este proyecto, 4 segundos se quedaba corto y seguian
# generandose IDs nuevos para la misma persona).
TIEMPO_MAX_RECUPERACION_SEG = 10.0

# Tolerancia de distancia entre la posicion proyectada (ultima posicion +
# velocidad estimada x tiempo transcurrido) y la posicion del candidato
# nuevo. Se expresa como multiplo del tamaño de la propia caja (mas grande
# la persona en pantalla -> mas margen, por perspectiva) mas un margen
# proporcional a la velocidad y al tiempo perdido (mas tiempo perdido ->
# mas incertidumbre de hacia donde se movio).
FACTOR_TOLERANCIA_TAMANO = 1.5
FACTOR_INCERTIDUMBRE_VELOCIDAD = 0.6

# Margen adicional (en pixeles por frame perdido) para cubrir el caso en que
# la persona siguio caminando mientras estaba oculta, pero su velocidad
# ESTIMADA antes de perderse era baja o cero (por ejemplo, iba caminando
# lento y la proyeccion por velocidad casi no mueve el punto esperado). Sin
# este margen, una ventana de recuperacion larga (TIEMPO_MAX_RECUPERACION_SEG)
# no sirve de mucho si el filtro de distancia sigue siendo estricto. Es una
# estimacion aproximada, no una medicion real de cuanto se movio la persona
# -ajustar segun la resolucion y la distancia de la camara en tus videos-.
MARGEN_BASE_PIXELES_POR_FRAME = 1.5

# Tolerancia de cambio de tamaño de la caja delimitadora (por perspectiva,
# una persona puede verse mas grande o chica al moverse hacia/desde la
# camara). Fuera de este rango, se descarta el candidato.
RATIO_TAMANO_MIN = 0.4
RATIO_TAMANO_MAX = 2.5

# Similitud minima de histograma de color (correlacion, rango ~-1 a 1) para
# aceptar un candidato. Es una heuristica de apariencia barata (no una red
# de reidentificacion real); sensible a cambios de iluminacion y a
# oclusiones parciales por otros objetos distintos del que genero la
# oclusion principal.
UMBRAL_SIMILITUD_HISTOGRAMA = 0.5

# Si el mejor candidato y el segundo mejor quedan muy cerca en similitud de
# apariencia, la coincidencia se considera ambigua y se rechaza: es
# preferible contar un objeto de mas (y dejarlo documentado) que fusionar
# por error a dos personas diferentes.
MARGEN_AMBIGUEDAD_HISTOGRAMA = 0.08


class GestorTrayectorias:
    """Mantiene, para cada categoria, las trayectorias logicas ya
    contabilizadas durante la sesion de un video, y decide si un ID crudo
    nuevo que entrega el tracker corresponde a una trayectoria perdida
    recientemente o si es realmente un objeto nuevo.

    Un 'ID logico' (logical_id) es el identificador propio de SafeWalk que
    se usa para contar una sola vez a cada persona/vehiculo, incluso si el
    tracker le asigna mas de un ID crudo a lo largo del video por culpa de
    una oclusion.
    """

    def __init__(self, fps_efectivo):
        self.fps_efectivo = fps_efectivo
        self.max_frames_recuperacion = max(1, int(TIEMPO_MAX_RECUPERACION_SEG * fps_efectivo))
        self.trayectorias = {}        # logical_id -> datos de la trayectoria
        self.track_id_a_logico = {}   # id crudo del tracker -> logical_id
        self._siguiente_logico = {CLASE_PEATON: 0, CLASE_AUTOMOVIL: 0, CLASE_MOTOCICLETA: 0}
        self._ids_activos_frame_anterior = set()

    def _nuevo_id_logico(self, clase_id):
        n = self._siguiente_logico[clase_id]
        self._siguiente_logico[clase_id] += 1
        return f"{NOMBRES_CATEGORIA[clase_id]}_{n}"

    @staticmethod
    def _extraer_histograma(frame, caja_xyxy):
        x1, y1, x2, y2 = [int(v) for v in caja_xyxy]
        alto, ancho = frame.shape[:2]
        x1, x2 = max(0, x1), min(ancho, x2)
        y1, y2 = max(0, y1), min(alto, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        recorte = frame[y1:y2, x1:x2]
        hsv = cv2.cvtColor(recorte, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        return hist

    def procesar_frame(self, frame, frame_actual, boxes_categoria):
        """boxes_categoria: lista de tuplas (track_id_o_None, clase_id, xyxy).
        Devuelve: dict categoria -> cantidad visible en este frame, y
        actualiza los acumulados internos (self.trayectorias)."""

        ids_vistos_este_frame = set()
        visibles = {'peatones': 0, 'automoviles': 0, 'motocicletas': 0}

        # Deteccion de trayectorias que se acaban de perder (estaban activas
        # el frame anterior y no aparecen en este), solo para el log de
        # depuracion.
        ids_con_deteccion_este_frame = {tid for (tid, _, _) in boxes_categoria if tid is not None}

        for track_id, clase_id, xyxy in boxes_categoria:
            categoria = NOMBRES_CATEGORIA[clase_id]
            visibles[categoria] += 1

            if track_id is None:
                # El tracker todavia no le asigno ID a esta caja; se cuenta
                # como visible pero no se puede evaluar para el acumulado
                # este frame puntual (se resolvera en 1-2 frames, cuando el
                # tracker le asigne un ID).
                continue

            ids_vistos_este_frame.add(track_id)
            x1, y1, x2, y2 = xyxy
            centro = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            tamano = (x2 - x1, y2 - y1)
            histograma = self._extraer_histograma(frame, xyxy)

            if track_id in self.track_id_a_logico:
                # Ya es una trayectoria conocida: se actualiza, NO se vuelve
                # a contar.
                logico = self.track_id_a_logico[track_id]
                info = self.trayectorias[logico]
                dt = max(1, frame_actual - info['ultimo_frame_visto'])
                info['velocidad'] = (
                    (centro[0] - info['centro'][0]) / dt,
                    (centro[1] - info['centro'][1]) / dt,
                )
                info['centro'] = centro
                info['tamano'] = tamano
                if histograma is not None:
                    info['histograma'] = histograma
                info['ultimo_frame_visto'] = frame_actual
                continue

            # ID crudo nuevo para nosotros: intentar recuperar una
            # trayectoria perdida de la misma categoria antes de darlo de
            # alta como objeto nuevo.
            candidatos = []
            for logico, info in self.trayectorias.items():
                if info['categoria'] != categoria:
                    continue
                dt = frame_actual - info['ultimo_frame_visto']
                if dt <= 0 or dt > self.max_frames_recuperacion:
                    continue  # ya esta activa, o la perdida es demasiado vieja

                # 1) Posicion proyectada segun ultima posicion + velocidad.
                proy_x = info['centro'][0] + info['velocidad'][0] * dt
                proy_y = info['centro'][1] + info['velocidad'][1] * dt
                distancia = ((centro[0] - proy_x) ** 2 + (centro[1] - proy_y) ** 2) ** 0.5
                vel_mag = (info['velocidad'][0] ** 2 + info['velocidad'][1] ** 2) ** 0.5
                tolerancia_dist = (
                    max(info['tamano']) * FACTOR_TOLERANCIA_TAMANO
                    + vel_mag * dt * FACTOR_INCERTIDUMBRE_VELOCIDAD
                    + dt * MARGEN_BASE_PIXELES_POR_FRAME
                )
                if distancia > tolerancia_dist:
                    continue

                # 2) Tamaño de caja (perspectiva).
                area_anterior = max(1.0, info['tamano'][0] * info['tamano'][1])
                area_actual = max(1.0, tamano[0] * tamano[1])
                ratio = area_actual / area_anterior
                if not (RATIO_TAMANO_MIN <= ratio <= RATIO_TAMANO_MAX):
                    continue

                # 3) Similitud de apariencia (histograma de color). Si no se
                # pudo calcular para alguno de los dos lados, se descarta el
                # candidato por precaucion -no se asume una coincidencia sin
                # poder evaluarla-.
                if histograma is None or info['histograma'] is None:
                    continue
                similitud = cv2.compareHist(info['histograma'], histograma, cv2.HISTCMP_CORREL)
                if similitud < UMBRAL_SIMILITUD_HISTOGRAMA:
                    continue

                candidatos.append((logico, similitud, distancia, dt))

            logico_asignado = None
            motivo = ''
            if candidatos:
                candidatos.sort(key=lambda c: c[1], reverse=True)
                mejor = candidatos[0]
                if len(candidatos) >= 2 and (mejor[1] - candidatos[1][1]) < MARGEN_AMBIGUEDAD_HISTOGRAMA:
                    motivo = (
                        f"candidato ambiguo (mejor={mejor[1]:.2f}, "
                        f"segundo={candidatos[1][1]:.2f}); se registra como nuevo por precaucion"
                    )
                else:
                    logico_asignado = mejor[0]
                    motivo = (
                        f"reasociado a {logico_asignado} "
                        f"(similitud={mejor[1]:.2f}, distancia={mejor[2]:.1f}px, "
                        f"perdido {mejor[3]} frames)"
                    )
            else:
                motivo = 'sin trayectorias perdidas compatibles en la ventana de recuperacion'

            if logico_asignado is not None:
                self.track_id_a_logico[track_id] = logico_asignado
                info = self.trayectorias[logico_asignado]
                dt = max(1, frame_actual - info['ultimo_frame_visto'])
                info['velocidad'] = (
                    (centro[0] - info['centro'][0]) / dt,
                    (centro[1] - info['centro'][1]) / dt,
                )
                info['centro'] = centro
                info['tamano'] = tamano
                if histograma is not None:
                    info['histograma'] = histograma
                info['ultimo_frame_visto'] = frame_actual
                if DEBUG_TRACKING:
                    print(f"[frame {frame_actual}] track_id {track_id} ({categoria}): {motivo}")
            else:
                logico_nuevo = self._nuevo_id_logico(clase_id)
                self.track_id_a_logico[track_id] = logico_nuevo
                self.trayectorias[logico_nuevo] = {
                    'categoria': categoria,
                    'centro': centro,
                    'velocidad': (0.0, 0.0),
                    'tamano': tamano,
                    'histograma': histograma,
                    'ultimo_frame_visto': frame_actual,
                }
                if DEBUG_TRACKING:
                    print(
                        f"[frame {frame_actual}] track_id {track_id} ({categoria}): "
                        f"nuevo registro {logico_nuevo} -> {motivo}"
                    )

        if DEBUG_TRACKING:
            recien_perdidos = self._ids_activos_frame_anterior - ids_con_deteccion_este_frame
            for tid in recien_perdidos:
                logico = self.track_id_a_logico.get(tid)
                if logico:
                    print(f"[frame {frame_actual}] track_id {tid} ({self.trayectorias[logico]['categoria']}) se perdio de vista")
        self._ids_activos_frame_anterior = ids_con_deteccion_este_frame

        return visibles

    def id_logico(self, track_id):
        """ID logico (el que se cuenta una sola vez) asociado a un ID crudo del
        tracker, o None si todavia no tiene. Usado por datos_deteccion (HU06)."""
        return self.track_id_a_logico.get(track_id) if track_id is not None else None

    def acumulados_por_categoria(self):
        acumulados = {'peatones': set(), 'automoviles': set(), 'motocicletas': set()}
        for logico, info in self.trayectorias.items():
            acumulados[info['categoria']].add(logico)
        return acumulados


def _dibujar_panel(frame, visibles, acumulados):
    """Dibuja, sobre el frame ya anotado por YOLO, el panel de conteo con
    dos secciones: lo que hay en pantalla ahora y el acumulado del video."""
    overlay = frame.copy()
    panel_x, panel_y = 10, 70
    panel_w, panel_h = 290, 250
    cv2.rectangle(overlay, (panel_x, panel_y),
                  (panel_x + panel_w, panel_y + panel_h), (20, 20, 20), -1)
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


def _dibujar_riesgo(frame, detecciones, evaluaciones):
    """HU07: muestra el nivel de riesgo. Resalta con el color del nivel a los
    peatones en riesgo MEDIO/ALTO, los une con el vehiculo que lo causa y pone
    un indicador general arriba al centro. (La alerta visual propia es HU08.)"""
    por_id = {d.id_logico: d for d in detecciones if d.id_logico}
    for id_ped, ev in evaluaciones.items():
        if ev.nivel < MEDIO:
            continue
        color = COLOR_NIVEL[ev.nivel]
        p = por_id.get(id_ped)
        if p is None:
            continue  # nivel mantenido de un peaton que no esta en este fotograma
        cv2.rectangle(frame, (int(p.x1), int(p.y1)), (int(p.x2), int(p.y2)), color, 3)
        cv2.putText(frame, f'RIESGO {ev.nombre}', (int(p.x1), max(15, int(p.y1) - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        v = por_id.get(ev.id_vehiculo)
        if v is not None:
            cv2.line(frame, (int(p.cx), int(p.cy)), (int(v.cx), int(v.cy)), color, 2)

    nivel = AnalizadorRiesgo.nivel_global(evaluaciones)
    color = COLOR_NIVEL[nivel]
    ancho = frame.shape[1]
    x0, y0, w, h = ancho // 2 - 110, 10, 220, 38
    cv2.rectangle(frame, (x0, y0), (x0 + w, y0 + h), color, -1)
    cv2.putText(frame, f'RIESGO: {NOMBRES_NIVEL[nivel]}', (x0 + 14, y0 + 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)


def _guardar_registro(nombre_video, acumulados, duracion_seg):
    """Agrega una fila al CSV de registro (no sobrescribe lo ya guardado).
    CSV elegido porque SafeWalk todavia no tiene base de datos implementada
    (SQLite esta prevista recien para el Sprint 3)."""
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
    objetos (incluyendo una capa de reasociacion tras oclusiones breves), y
    al terminar guarda un resumen en outputs/registro_conteos.csv."""

    modelo = YOLO(ruta_modelo)
    captura = cv2.VideoCapture(ruta_video)

    if not captura.isOpened():
        raise ValueError(f"No se pudo abrir el video: {ruta_video}")

    fps_video = captura.get(cv2.CAP_PROP_FPS) or 0
    total_frames = captura.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duracion_seg = (total_frames / fps_video) if fps_video > 0 and total_frames > 0 else None
    # Si el video no reporta FPS (por ejemplo, una camara en vivo), se asume
    # 30 FPS para las ventanas de tiempo de la reasociacion. Es una
    # aproximacion: documentarla como limitacion si se usa con camara en vivo.
    fps_efectivo = fps_video if fps_video > 0 else 30.0

    gestor = GestorTrayectorias(fps_efectivo)
    registro = RegistroDetecciones()  # uno por video: no mezcla sesiones
    analizador = AnalizadorRiesgo()   # uno por video: historiales de riesgo propios
    frame_actual = 0

    while True:
        ret, frame = captura.read()
        if not ret:
            break
        frame_actual += 1

        tiempo_inicio = time.time()

        resultados = modelo.track(
            frame, persist=True, tracker=RUTA_TRACKER, verbose=False
        )

        boxes_categoria = []
        confianzas = []  # mismo orden que boxes_categoria (HU06)
        if resultados[0].boxes is not None:
            for caja in resultados[0].boxes:
                clase_id = int(caja.cls[0])
                if clase_id not in CLASES_CONTABILIZADAS:
                    continue
                track_id = int(caja.id[0]) if caja.id is not None else None
                xyxy = caja.xyxy[0].tolist()
                boxes_categoria.append((track_id, clase_id, xyxy))
                confianzas.append(float(caja.conf[0]))

        visibles = gestor.procesar_frame(frame, frame_actual, boxes_categoria)
        acumulados = gestor.acumulados_por_categoria()

        # HU06: datos organizados de este fotograma (lista de Deteccion), listos
        # para el analisis de riesgo del siguiente paso del Sprint 2 (HU07).
        detecciones_frame = construir_detecciones(
            gestor, boxes_categoria, confianzas, frame_actual,
            fps_efectivo, NOMBRES_CATEGORIA)

        # HU07: nivel de riesgo de cada peaton en este fotograma.
        evaluaciones = analizador.evaluar(detecciones_frame, frame_actual / fps_efectivo)
        for d in detecciones_frame:
            if d.id_logico in evaluaciones:
                d.riesgo = evaluaciones[d.id_logico].nombre
        registro.agregar(detecciones_frame)

        # Conserva las cajas delimitadoras y etiquetas de deteccion tal como
        # ya las dibujaba el proyecto (incluye las clases no contabilizadas,
        # como bus/truck/bicycle).
        frame_anotado = resultados[0].plot()

        _dibujar_panel(frame_anotado, visibles, acumulados)
        _dibujar_riesgo(frame_anotado, detecciones_frame, evaluaciones)

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

    acumulados = gestor.acumulados_por_categoria()
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
    r = analizador.resumen()
    print('--- Riesgo (peatones, segun el nivel mas alto que alcanzaron) ---')
    print(f"Riesgo ALTO: {r['peatones_riesgo_alto']} | MEDIO: {r['peatones_riesgo_medio']} | BAJO: {r['peatones_riesgo_bajo']}")
    print(f"Fotogramas con riesgo general ALTO: {r['frames_alto']}, MEDIO: {r['frames_medio']}, BAJO: {r['frames_bajo']}")
    print('=====================================\n')

    _guardar_registro(os.path.basename(str(ruta_video)), acumulados, duracion_seg)
    print(f'Registro guardado en {RUTA_REGISTRO_CSV}')

    if EXPORTAR_DETECCIONES_CSV:
        ruta_det = registro.exportar_csv(ruta_video)
        if ruta_det:
            print(f'Detecciones por fotograma ({len(registro)} filas) guardadas en {ruta_det}')
    print()


if __name__ == '__main__':
    procesar_video('data/videos/videoTransito.mp4')