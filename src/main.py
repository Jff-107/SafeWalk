import cv2
from ultralytics import YOLO  #YOLO
import time   #FPS

# Clases que nos interesan para SafeWalk (según el dataset COCO)
CLASES_RELEVANTES = {
    0: 'person',
    1: 'bicycle',
    2: 'car',
    3: 'motorcycle',
    5: 'bus',
    7: 'truck'
}


def procesar_video(ruta_video, ruta_modelo='data/models/yolov8n.pt'):
    """Lee un video frame por frame, aplica detección YOLO y muestra el resultado."""

    modelo = YOLO(ruta_modelo)
    captura = cv2.VideoCapture(ruta_video)

    if not captura.isOpened():
        raise ValueError(f"No se pudo abrir el video: {ruta_video}")

    while True:
        ret, frame = captura.read()
        tiempo_inicio = time.time()
        if not ret:
            break

        # Ejecutar YOLO sobre el frame actual
        # verbose=False evita que imprima una línea por cada frame en consola
        resultados = modelo(frame, verbose=False)

        # Contadores para este frame
        peatones = 0
        vehiculos = 0

        for resultado in resultados:
            for caja in resultado.boxes:
                clase_id = int(caja.cls[0])
                if clase_id not in CLASES_RELEVANTES:
                    continue  # ignoramos clases que no nos interesan (perro, silla, etc.)

                if clase_id == 0:
                    peatones += 1
                else:
                    vehiculos += 1

        # Dibujar las detecciones sobre el frame
        frame_anotado = resultados[0].plot()

        # Mostrar el conteo en pantalla (texto simple sobre el video)
        texto = f"Peatones: {peatones} | Vehiculos: {vehiculos}"
        cv2.putText(frame_anotado, texto, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        tiempo_fin = time.time()
        fps = 1 / (tiempo_fin - tiempo_inicio)
        cv2.putText(frame_anotado, f"FPS: {fps:.1f}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        
        cv2.imshow('SafeWalk - Deteccion', frame_anotado)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    captura.release()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    procesar_video('data/videos/videoTransito.mp4')