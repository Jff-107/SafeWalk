from ultralytics import YOLO
import cv2

# Cargar el modelo YOLO preentrenado
modelo = YOLO('data/models/yolov8n.pt')

# Ruta de la imagen de prueba
ruta_imagen = 'data/videos/imagen 1.jpeg'

# Ejecutar la detección
resultados = modelo(ruta_imagen)

# Recorrer los resultados y mostrar qué se detectó en consola
for resultado in resultados:
    for caja in resultado.boxes:
        clase_id = int(caja.cls[0])
        nombre_clase = modelo.names[clase_id]
        confianza = float(caja.conf[0])
        print(f"Detectado: {nombre_clase} (confianza: {confianza:.2f})")

# Dibujar las detecciones sobre la imagen y guardarla
imagen_anotada = resultados[0].plot()
cv2.imwrite('outputs/deteccion_prueba.jpg', imagen_anotada)

print("\nListo. Revisa outputs/deteccion_prueba.jpg")