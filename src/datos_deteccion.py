"""HU06 - Procesar y organizar los datos generados por las detecciones.

Convierte lo que entrega YOLO + el tracker en una estructura ordenada por
fotograma (una `Deteccion` por objeto contabilizable), pensada para que el
modulo de riesgo (HU07) la consuma sin tener que volver a leer los
resultados crudos de Ultralytics. Tambien permite exportarla a CSV como
evidencia del sprint.

Este modulo NO depende de main.py (evita importaciones circulares): recibe
del llamador todo lo que necesita.
"""
import csv
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Deteccion:
    frame: int
    tiempo_seg: float
    id_logico: Optional[str]      # None si el tracker aun no asigno ID a la caja
    categoria: str                # 'peatones' | 'automoviles' | 'motocicletas'
    confianza: float
    x1: float
    y1: float
    x2: float
    y2: float
    cx: float
    cy: float
    vx_px_s: float                # velocidad estimada del centro (px/segundo)
    vy_px_s: float
    riesgo: str = ''              # BAJO/MEDIO/ALTO (solo peatones; lo llena main.py via riesgo.py, HU07)

    @property
    def ancho(self):
        return self.x2 - self.x1

    @property
    def alto(self):
        return self.y2 - self.y1


def construir_detecciones(gestor, boxes_categoria, confianzas, frame_actual,
                          fps, nombres_categoria):
    """Arma la lista de `Deteccion` de un fotograma.

    boxes_categoria: lista de (track_id_o_None, clase_id, xyxy), la misma que
        recibe GestorTrayectorias.procesar_frame().
    confianzas: lista de floats, en el MISMO orden que boxes_categoria.
    Debe llamarse DESPUES de gestor.procesar_frame(), porque ahi se resuelve
    el ID logico (incluida la reasociacion tras oclusiones) y se actualiza la
    velocidad de cada trayectoria.
    """
    detecciones = []
    for (track_id, clase_id, xyxy), conf in zip(boxes_categoria, confianzas):
        x1, y1, x2, y2 = xyxy
        id_logico = gestor.id_logico(track_id)
        info = gestor.trayectorias.get(id_logico) if id_logico else None
        if info is not None:
            vx, vy = info['velocidad'][0] * fps, info['velocidad'][1] * fps
        else:
            vx, vy = 0.0, 0.0
        detecciones.append(Deteccion(
            frame=frame_actual,
            tiempo_seg=round(frame_actual / fps, 3),
            id_logico=id_logico,
            categoria=nombres_categoria[clase_id],
            confianza=round(float(conf), 3),
            x1=round(x1, 1), y1=round(y1, 1), x2=round(x2, 1), y2=round(y2, 1),
            cx=round((x1 + x2) / 2.0, 1), cy=round((y1 + y2) / 2.0, 1),
            vx_px_s=round(vx, 1), vy_px_s=round(vy, 1),
        ))
    return detecciones


class RegistroDetecciones:
    """Acumula las detecciones de todos los fotogramas de UNA sesion de video
    (se crea uno nuevo por video, igual que los contadores)."""

    def __init__(self):
        self.detecciones = []

    def agregar(self, detecciones_frame):
        self.detecciones.extend(detecciones_frame)

    def __len__(self):
        return len(self.detecciones)

    def exportar_csv(self, nombre_video, carpeta='outputs'):
        """Guarda un CSV nuevo por video procesado (nombre con fecha/hora para
        no sobrescribir resultados anteriores). Devuelve la ruta, o None si
        no hubo detecciones que guardar."""
        if not self.detecciones:
            return None
        os.makedirs(carpeta, exist_ok=True)
        base = os.path.splitext(os.path.basename(str(nombre_video)))[0] or 'video'
        sello = datetime.now().strftime('%Y%m%d_%H%M%S')
        ruta = os.path.join(carpeta, f'detecciones_{base}_{sello}.csv')
        campos = list(asdict(self.detecciones[0]).keys())
        with open(ruta, mode='w', newline='', encoding='utf-8') as f:
            escritor = csv.DictWriter(f, fieldnames=campos)
            escritor.writeheader()
            for d in self.detecciones:
                escritor.writerow(asdict(d))
        return ruta