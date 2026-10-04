import cv2

def abrir_video(ruta_video):
    """Abre un archivo de video y devuelve el objeto de captura de OpenCV."""
    captura = cv2.VideoCapture(ruta_video)
    if not captura.isOpened():
        raise ValueError(f"No se pudo abrir el video: {ruta_video}")
    return captura


def reproducir_video_simple(ruta_video):
    """Muestra el video en una ventana, frame por frame, sin procesamiento."""
    captura = abrir_video(ruta_video)

    while True:
        ret, frame = captura.read()
        if not ret:
            # ret es False cuando el video termina o hay un error de lectura
            break

        cv2.imshow('SafeWalk - Video', frame)

        # Espera 1ms entre frames; si se presiona 'q', se cierra
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    captura.release()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    reproducir_video_simple('data/videos/test.mp4')