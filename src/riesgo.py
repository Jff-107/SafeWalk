"""HU07 - Clasificacion del nivel de riesgo (Bajo / Medio / Alto).

Criterio (proximidad peaton-vehiculo, segun CF-03 del documento del proyecto),
evaluado por cada peaton contra cada vehiculo (auto o moto) del fotograma:

  1. DISTANCIA en el suelo: separacion horizontal entre cajas + diferencia de
     altura de los "pies" (borde inferior de cada caja, que aproxima la
     profundidad), dividida entre la altura del peaton. Asi la medida no
     depende de la resolucion ni de cuan lejos esta la persona de la camara.
  2. MOVIMIENTO del vehiculo: un auto estacionado o detenido NO genera riesgo.
  3. ACERCAMIENTO: la distancia peaton-vehiculo viene disminuyendo.

     ALTO  : vehiculo en movimiento, muy cerca y acercandose.
     MEDIO : vehiculo en movimiento y (muy cerca, o cerca y acercandose).
     BAJO  : cualquier otro caso.

LIMITACIONES (documentar en el informe): trabaja sobre la imagen 2D, no sobre
distancias reales en metros; depende del angulo de la camara; los umbrales de
abajo son un punto de partida y hay que calibrarlos con videos reales.
"""
import math
from collections import deque

BAJO, MEDIO, ALTO = 0, 1, 2
NOMBRES_NIVEL = {BAJO: 'BAJO', MEDIO: 'MEDIO', ALTO: 'ALTO'}

# --- Umbrales (ajustables) -------------------------------------------------
DIST_ALTO = 0.6               # "muy cerca": <= 0.6 alturas de peaton
DIST_MEDIO = 1.8              # "cerca":     <= 1.8 alturas de peaton
VEL_VEHICULO_MIN = 0.15       # anchos de vehiculo por segundo para considerarlo en movimiento
ACERCAMIENTO_MIN = 0.3        # alturas de peaton por segundo para considerarlo "acercandose"
DIST_SEGUIMIENTO_MAX = 6.0    # solo se sigue el historial de pares a menos de esto
VENTANA_MUESTRAS = 6          # muestras para suavizar velocidades (~0.2 s a 30 FPS)
MIN_SPAN_SEG = 0.1            # tiempo minimo entre muestras para estimar velocidad
MANTENER_SEG = 0.7            # el nivel mas alto se mantiene en pantalla este tiempo
OLVIDAR_SEG = 2.0             # se descarta el historial de objetos no vistos en este tiempo


class EvaluacionRiesgo:
    def __init__(self, nivel, id_vehiculo=None, distancia=None,
                 acercamiento=0.0, motivo=''):
        self.nivel = nivel
        self.id_vehiculo = id_vehiculo
        self.distancia = distancia
        self.acercamiento = acercamiento
        self.motivo = motivo

    @property
    def nombre(self):
        return NOMBRES_NIVEL[self.nivel]


def _distancia_suelo(ped, veh):
    gap_x = max(0.0, max(ped.x1, veh.x1) - min(ped.x2, veh.x2))
    dy = abs(ped.y2 - veh.y2)
    return math.hypot(gap_x, dy) / max(ped.alto, 1.0)


class AnalizadorRiesgo:
    """Una instancia por video (como el gestor de trayectorias)."""

    def __init__(self):
        self._hist_obj = {}      # id_logico -> deque[(t, cx, cy)]
        self._hist_par = {}      # (id_ped, id_veh) -> deque[(t, distancia)]
        self._mantenido = {}     # id_ped -> (EvaluacionRiesgo, hasta_t)
        self.max_nivel_por_ped = {}
        self.frames_por_nivel = {BAJO: 0, MEDIO: 0, ALTO: 0}

    # -- utilidades de historial -------------------------------------------
    def _velocidad_objeto(self, id_obj):
        h = self._hist_obj.get(id_obj)
        if not h or len(h) < 3:
            return 0.0
        (t0, x0, y0), (t1, x1, y1) = h[0], h[-1]
        if t1 - t0 < MIN_SPAN_SEG:
            return 0.0
        return math.hypot(x1 - x0, y1 - y0) / (t1 - t0)    # px/s

    def _acercamiento(self, clave):
        h = self._hist_par.get(clave)
        if not h or len(h) < 3:
            return 0.0
        (t0, d0), (t1, d1) = h[0], h[-1]
        if t1 - t0 < MIN_SPAN_SEG:
            return 0.0
        return (d0 - d1) / (t1 - t0)                      # alturas/s (+ = se acercan)

    def _podar(self, t):
        for d in (self._hist_obj, self._hist_par):
            for k in [k for k, h in d.items() if t - h[-1][0] > OLVIDAR_SEG]:
                del d[k]

    # -- evaluacion de un fotograma ------------------------------------------
    def evaluar(self, detecciones, tiempo):
        """detecciones: lista de datos_deteccion.Deteccion del fotograma.
        Devuelve {id_logico_peaton: EvaluacionRiesgo} solo para peatones con ID."""
        peatones = [d for d in detecciones if d.categoria == 'peatones' and d.id_logico]
        vehiculos = [d for d in detecciones
                     if d.categoria in ('automoviles', 'motocicletas') and d.id_logico]

        for d in peatones + vehiculos:
            h = self._hist_obj.setdefault(d.id_logico, deque(maxlen=VENTANA_MUESTRAS))
            h.append((tiempo, d.cx, d.cy))

        resultado = {}
        for p in peatones:
            mejor = EvaluacionRiesgo(BAJO, motivo='sin vehiculos en movimiento cerca')
            for v in vehiculos:
                dist = _distancia_suelo(p, v)
                clave = (p.id_logico, v.id_logico)
                if dist <= DIST_SEGUIMIENTO_MAX:
                    self._hist_par.setdefault(
                        clave, deque(maxlen=VENTANA_MUESTRAS)).append((tiempo, dist))
                acerc = self._acercamiento(clave)
                vel_rel = self._velocidad_objeto(v.id_logico) / max(v.ancho, 1.0)
                en_mov = vel_rel >= VEL_VEHICULO_MIN
                acercando = acerc >= ACERCAMIENTO_MIN

                nivel, motivo = BAJO, ''
                if en_mov:
                    if dist <= DIST_ALTO and acercando:
                        nivel, motivo = ALTO, 'vehiculo en movimiento muy cerca y acercandose'
                    elif dist <= DIST_ALTO:
                        nivel, motivo = MEDIO, 'vehiculo en movimiento muy cerca'
                    elif dist <= DIST_MEDIO and acercando:
                        nivel, motivo = MEDIO, 'vehiculo en movimiento cerca y acercandose'
                if nivel > mejor.nivel or (nivel == mejor.nivel and nivel > BAJO
                                           and dist < (mejor.distancia or 1e9)):
                    mejor = EvaluacionRiesgo(nivel, v.id_logico, dist, acerc, motivo)

            # Mantener el nivel mas alto un instante para que se alcance a ver.
            previo = self._mantenido.get(p.id_logico)
            if previo and previo[0].nivel > mejor.nivel and tiempo < previo[1]:
                mejor = previo[0]
            elif mejor.nivel > BAJO:
                self._mantenido[p.id_logico] = (mejor, tiempo + MANTENER_SEG)

            resultado[p.id_logico] = mejor
            self.max_nivel_por_ped[p.id_logico] = max(
                self.max_nivel_por_ped.get(p.id_logico, BAJO), mejor.nivel)

        self.frames_por_nivel[self.nivel_global(resultado)] += 1
        self._podar(tiempo)
        return resultado

    @staticmethod
    def nivel_global(evaluaciones):
        return max((e.nivel for e in evaluaciones.values()), default=BAJO)

    def resumen(self):
        """Resumen de la sesion: peatones segun el nivel MAS ALTO alcanzado."""
        niveles = list(self.max_nivel_por_ped.values())
        return {
            'peatones_riesgo_alto': sum(1 for n in niveles if n == ALTO),
            'peatones_riesgo_medio': sum(1 for n in niveles if n == MEDIO),
            'peatones_riesgo_bajo': sum(1 for n in niveles if n == BAJO),
            'frames_alto': self.frames_por_nivel[ALTO],
            'frames_medio': self.frames_por_nivel[MEDIO],
            'frames_bajo': self.frames_por_nivel[BAJO],
        }