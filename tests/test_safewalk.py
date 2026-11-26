"""Pruebas aisladas: nunca escriben en los registros reales del proyecto."""
from contextlib import closing
import sqlite3
import tempfile
import unittest
import time
import json
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import cv2
import numpy as np
import app as web
import src.main as storage
from src.web_processor import WebVideoProcessor


class SafeWalkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(storage, 'DB_PATH', str(self.root / 'test.db')),
                        patch.object(storage, 'OUTPUT_DIR', str(self.root)),
                        patch.object(web, 'DB_PATH', self.root / 'test.db'),
                        patch.object(web, 'OUTPUTS', self.root)]
        for item in self.patches:
            item.start()
        storage.preparar_base_datos()
        self.client = web.app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_sessions_reports_and_chart(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        for count in [3, 5]:
            session = storage.iniciar_sesion('video', 'prueba.mp4')
            storage.guardar_metrica(2, 1, count, 1, 12, 0, 1, session)
            storage.guardar_alerta('ALTO', 'Prueba', session)
            storage.finalizar_sesion(session, 'finalizado')
        self.assertEqual(web.read_dashboard()['peatones_cruzaron'], 8)
        self.assertEqual(len(self.client.get('/api/sesiones').json), 2)
        self.assertEqual(self.client.get('/api/estadisticas').json['muestras'], 2)
        self.assertTrue(self.client.get('/estadisticas.png').data.startswith(b'\x89PNG'))
        with self.client.get('/descargas/metricas.csv') as response:
            self.assertIn(b'sesion_id', response.data)
        backup = self.root / 'backup.db'
        backup.write_bytes(self.client.get('/descargas/safewalk.db').data)
        with closing(sqlite3.connect(backup)) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sesiones').fetchone()[0], 2)

    def test_video_summary_uses_only_its_analysis_session(self):
        first = storage.iniciar_sesion('video', 'primero.mp4')
        storage.guardar_metrica(4, 2, 1, 0, 8, 1, 0, first)
        storage.finalizar_sesion(first, 'finalizado')
        second = storage.iniciar_sesion('video', 'segundo.mp4')
        storage.guardar_metrica(2, 3, 0, 1, 12, 0, 1, second)
        storage.guardar_metrica(3, 5, 1, 2, 10, 1, 0, second)
        storage.guardar_alerta('ALTO', 'Prueba del segundo video', second)
        storage.finalizar_sesion(second, 'finalizado')
        camera = storage.iniciar_sesion('camera', 'Camara')
        storage.guardar_metrica(9, 9, 4, 4, 6, 0, 0, camera)
        storage.finalizar_sesion(camera, 'finalizado')
        other_processor = WebVideoProcessor()
        with patch.object(web, 'processor', other_processor):
            response = self.client.get('/api/dashboard?fuente=video').json['summary']
            self.assertEqual(response['sesion']['nombre'], 'segundo.mp4')
            self.assertEqual((response['max_peatones'], response['max_vehiculos']), (3, 5))
            self.assertEqual((response['muestras_riesgo_medio'], response['muestras_riesgo_alto']), (1, 1))
            self.assertEqual((response['peatones_cruzaron'], response['vehiculos_cruzaron']), (1, 2))
            self.assertEqual(response['alertas'], 1)
            self.assertEqual(response['fps'], 11)
            historic = self.client.get(f'/api/reportes/sesion/{first}').json['resumen']
            self.assertEqual((historic['max_peatones'], historic['max_vehiculos']), (4, 2))
            self.assertEqual(historic['alertas'], 0)
            trends = self.client.get('/api/tendencias?fuente=video').json
            self.assertEqual(trends['sesion']['id'], second)
            self.assertEqual(len(trends['muestras']), 2)
            self.assertEqual(self.client.get('/api/dashboard?fuente=camera').json['summary']['max_peatones'], 9)
            other_processor.running = True
            other_processor.source = 'video'
            self.assertIsNone(self.client.get('/api/dashboard?fuente=video').json['summary']['sesion'])

    def test_camera_validation_and_latest_frame_queue(self):
        processor = WebVideoProcessor()
        processor.running = True
        processor.source = 'camera'
        with patch.object(web, 'processor', processor):
            self.assertEqual(self.client.post('/api/camera/start').status_code, 409)
            self.assertEqual(self.client.post('/api/camera/frame', data=b'bad').status_code, 400)
            for value in [0, 200]:
                image = np.full((48, 64, 3), value, np.uint8)
                _, jpeg = cv2.imencode('.jpg', image)
                self.assertEqual(self.client.post('/api/camera/frame', data=jpeg.tobytes()).status_code, 200)
            self.assertEqual(processor.camera_frames.qsize(), 1)
            self.assertGreater(processor.camera_frames.get()[0].mean(), 190)
            self.client.post('/api/camera/stop')
            self.assertTrue(processor.stop_requested)

    def test_camera_reordered_frames_and_old_sessions_are_discarded(self):
        processor = WebVideoProcessor()
        processor.running = True
        processor.source = 'camera'
        processor.camera_token = 'current'
        newer = np.full((48, 64, 3), 200, np.uint8)
        older = np.zeros((48, 64, 3), np.uint8)
        self.assertTrue(processor.submit_camera_frame(newer, 2, 'current'))
        self.assertTrue(processor.submit_camera_frame(older, 1, 'current'))
        self.assertEqual(processor.camera_sequence, 2)
        self.assertEqual(processor.camera_frames.get()[0].mean(), 200)
        self.assertFalse(processor.submit_camera_frame(older, 3, 'old-session'))
        self.assertEqual(processor.camera_latest[0].mean(), 200)

    def test_camera_boxes_stay_on_the_frame_that_was_analyzed(self):
        processor = WebVideoProcessor()
        processor.running = True
        processor.source = 'camera'
        raw = np.zeros((120, 160, 3), np.uint8)
        annotated = raw.copy()
        cv2.rectangle(annotated, (20, 20), (70, 90), (0, 255, 0), 4)
        self.assertTrue(processor._publish_live(raw))
        processor._publish_camera_analysis(annotated, 8)
        annotated_version = processor.frame_version
        # La imagen nueva no debe recibir cajas de la imagen analizada antes.
        self.assertFalse(processor._publish_live(np.full_like(raw, 255)))
        self.assertEqual(processor.frame_version, annotated_version)
        displayed = cv2.imdecode(np.frombuffer(processor.frame_jpeg, np.uint8), cv2.IMREAD_COLOR)
        self.assertGreater(int(displayed[20, 40, 1]), 175)
        processor.camera_last_annotated = time.monotonic() - 2
        self.assertTrue(processor._publish_live(np.full_like(raw, 255)))
        preview = cv2.imdecode(np.frombuffer(processor.frame_jpeg, np.uint8), cv2.IMREAD_COLOR)
        self.assertGreater(int(preview[20, 40, 0]), 240)

    def test_camera_preview_does_not_wait_for_model_warmup(self):
        release = threading.Event()
        warming = threading.Event()
        model = MagicMock()
        def warmup(*args, **kwargs):
            warming.set()
            release.wait(5)
        model.predict.side_effect = warmup
        processor = WebVideoProcessor()
        try:
            with patch('src.web_processor.YOLO', return_value=model):
                processor.start(None, 'Prueba cámara')
                self.assertTrue(warming.wait(3))
                processor.submit_camera_frame(np.zeros((480, 640, 3), np.uint8), 1, processor.camera_token)
                with processor.condition:
                    self.assertTrue(processor.condition.wait_for(lambda: processor.frame_version > 0, timeout=1))
                self.assertFalse(release.is_set())
                self.assertEqual(model.track.call_count, 0)
        finally:
            processor.stop()
            release.set()
            processor.thread.join(10)
            processor.camera_display_thread.join(3)

    def test_phone_http_frame_reaches_laptop_viewer_before_yolo(self):
        release = threading.Event()
        model = MagicMock()
        model.predict.side_effect = lambda *a, **k: release.wait(5)
        processor = WebVideoProcessor()
        try:
            with patch.object(web, 'processor', processor), patch('src.web_processor.YOLO', return_value=model):
                started = self.client.post('/api/camera/start')
                self.assertEqual(started.status_code, 200)
                token = started.json['camera_token']
                _, jpeg = cv2.imencode('.jpg', np.full((480, 640, 3), 180, np.uint8))
                sent = self.client.post('/api/camera/frame', data=jpeg.tobytes(), headers={
                    'X-Camera-Session': token, 'X-Frame-Sequence': '1'})
                self.assertEqual(sent.status_code, 200)
                with processor.condition:
                    self.assertTrue(processor.condition.wait_for(lambda: processor.frame_version > 0, timeout=1))
                image = self.client.get('/api/video/frame')
                self.assertEqual(image.status_code, 200)
                self.assertEqual(image.mimetype, 'image/jpeg')
                decoded = cv2.imdecode(np.frombuffer(image.data, np.uint8), cv2.IMREAD_COLOR)
                self.assertIsNotNone(decoded)
                self.assertGreater(decoded[100:300, 100:300].mean(), 150)
                self.assertEqual(image.headers['Cache-Control'], 'no-store')
                _, portrait = cv2.imencode('.jpg', np.full((640, 480, 3), 180, np.uint8))
                sent = self.client.post('/api/camera/frame', data=portrait.tobytes(), headers={
                    'X-Camera-Session': token, 'X-Frame-Sequence': '2'})
                self.assertEqual(sent.status_code, 200)
                turned = self.client.get('/api/video/frame?after=' + image.headers['X-Frame-Version'])
                self.assertEqual(turned.status_code, 200)
                turned_image = cv2.imdecode(np.frombuffer(turned.data, np.uint8), cv2.IMREAD_COLOR)
                self.assertEqual(turned_image.shape[:2], (640, 480))
                processor.stop()
                self.assertEqual(self.client.get('/api/video/frame?after=' + turned.headers['X-Frame-Version']).status_code, 204)
        finally:
            processor.stop()
            release.set()
            processor.thread.join(10)
            processor.camera_display_thread.join(3)

    def test_mobile_pairing_protects_panel_and_generates_qr(self):
        connection = self.root / 'connection.json'
        connection.write_text(json.dumps({'url':'https://prueba.trycloudflare.com'}), encoding='utf-8')
        with patch.dict(web.app.config, MOBILE_ACCESS_KEY='clave-aleatoria-de-prueba', MOBILE_CONFIG=str(connection)):
            self.assertEqual(self.client.get('/api/dashboard').status_code, 401)
            self.assertEqual(self.client.get('/descargas/safewalk.db').status_code, 302)
            self.assertEqual(self.client.post('/api/acceso', json={'clave':'incorrecta'}).status_code, 403)
            self.assertEqual(self.client.post('/api/acceso', json={'clave':'ñ'}).status_code, 403)
            response = self.client.post('/api/acceso', json={'clave':'clave-aleatoria-de-prueba'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('HttpOnly', response.headers['Set-Cookie'])
            self.assertEqual(self.client.get('/api/dashboard').status_code, 200)
            self.assertEqual(self.client.get('/camara-movil').status_code, 200)
            link = self.client.get('/api/mobile/link').json['url']
            self.assertEqual(link, 'https://prueba.trycloudflare.com/vincular#clave-aleatoria-de-prueba')
            qr = self.client.get('/api/mobile/qr')
            self.assertEqual(qr.mimetype, 'image/svg+xml')
            self.assertIn(b'<svg', qr.data)
            stranger = web.app.test_client()
            self.assertEqual(stranger.post('/api/camera/start').status_code, 401)
            self.assertEqual(stranger.get('/api/mobile/qr').status_code, 401)

    def test_live_trends_only_contains_latest_session(self):
        old = storage.iniciar_sesion('video', 'anterior.mp4')
        storage.guardar_metrica(99, 1, 0, 0, 10, 0, 0, old)
        current = storage.iniciar_sesion('video', 'actual.mp4')
        storage.guardar_metrica(2, 3, 0, 0, 10, 0, 0, current)
        response = self.client.get('/api/tendencias')
        self.assertEqual(response.json['sesion']['id'], current)
        self.assertEqual([r['peatones_frame'] for r in response.json['muestras']], [2])
        storage.guardar_metrica(4, 3, 0, 0, 10, 0, 0, current)
        self.assertEqual(len(self.client.get('/api/tendencias').json['muestras']), 2)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_fluid_video_does_not_wait_for_slow_inference(self):
        video = self.root / 'fluid.avi'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 30, (640, 480))
        self.assertTrue(writer.isOpened())
        for _ in range(30):
            writer.write(np.zeros((480, 640, 3), dtype=np.uint8))
        writer.release()
        boxes = MagicMock()
        boxes.id = None
        boxes.__len__.return_value = 0
        result = MagicMock(boxes=boxes)
        model = MagicMock()
        def slow_track(*args, **kwargs):
            time.sleep(.15)  # YOLO simulado a unos 6 FPS.
            return [result]
        model.track.side_effect = slow_track
        processor = WebVideoProcessor()
        with patch('src.web_processor.YOLO', return_value=model):
            started = time.perf_counter()
            processor.start(str(video), video.name)
            processor.thread.join(timeout=10)
            elapsed = time.perf_counter() - started
        self.assertFalse(processor.running)
        self.assertIsNone(processor.error)
        self.assertGreaterEqual(processor.frame_version, 24)
        self.assertLess(model.track.call_count, processor.frame_version / 2)
        self.assertLess(elapsed, 2.5)

    def test_migration_preserves_existing_records(self):
        storage.guardar_metrica(1, 2, 3, 4, 5, 0, 0)
        with closing(sqlite3.connect(storage.DB_PATH)) as conn, conn:
            conn.execute('DROP VIEW resumen_sesiones')
            for table in ('metricas', 'alertas'):
                conn.execute(f'DROP INDEX idx_{table}_sesion')
                conn.execute(f'ALTER TABLE {table} DROP COLUMN sesion_id')
        storage.preparar_base_datos()
        storage.preparar_base_datos()
        with closing(sqlite3.connect(storage.DB_PATH)) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM metricas').fetchone()[0], 1)

    def test_real_yolo_video_pipeline(self):
        video = self.root / 'short.avi'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 10, (640, 480))
        self.assertTrue(writer.isOpened())
        for _ in range(4):
            writer.write(np.zeros((480, 640, 3), dtype=np.uint8))
        writer.release()
        processor = WebVideoProcessor()
        started, _ = processor.start(str(video), video.name)
        self.assertTrue(started)
        processor.thread.join(timeout=45)
        self.assertFalse(processor.running, 'El análisis no finalizó')
        self.assertIsNone(processor.error)
        self.assertIsNotNone(processor.frame_jpeg)
        self.assertEqual(processor.status, 'Análisis finalizado')
        with closing(sqlite3.connect(storage.DB_PATH)) as conn:
            self.assertEqual(conn.execute('SELECT estado FROM sesiones').fetchone()[0], 'finalizado')
            self.assertGreater(conn.execute('SELECT COUNT(*) FROM metricas').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
