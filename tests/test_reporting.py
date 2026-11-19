"""Persistencia, filtros y descargas consistentes con los registros originales."""
from contextlib import closing
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import pandas as pd
import app as web
import src.main as storage


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'records.db'
        self.patches = [patch.object(storage, 'DB_PATH', str(self.path)),
                        patch.object(storage, 'OUTPUT_DIR', self.temp.name), patch.object(web, 'DB_PATH', self.path)]
        for item in self.patches:
            item.start()
        storage.preparar_base_datos()
        self.first = storage.iniciar_sesion('video', 'A.mp4', 'Entrada UTP')
        self.second = storage.iniciar_sesion('camera', 'Celular', 'Zona B')
        with closing(storage.database_connection()) as conn, conn:
            for date, people, high, session in [('2026-11-14T08:00:00',3,0,self.first),
                ('2026-11-14T08:01:00',5,1,self.first), ('2026-11-15T09:00:00',9,1,self.second)]:
                conn.execute('''INSERT INTO metricas(fecha_hora,peatones_frame,vehiculos_frame,peatones_cruzaron,
                    vehiculos_cruzaron,fps,riesgo_medio_frame,riesgo_alto_frame,sesion_id)
                    VALUES (?,?,1,2,1,20,0,?,?)''', (date, people, high, session))
            conn.execute("INSERT INTO alertas(fecha_hora,nivel,descripcion,sesion_id) VALUES ('2026-11-14T08:01:00','ALTO','Prueba',?)", (self.first,))
        self.client = web.app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_filter_csv_and_zip_agree(self):
        query = {'sesion': self.first, 'desde':'2026-11-14', 'hasta':'2026-11-14', 'nivel':'ALTO'}
        result = self.client.get('/api/registros', query_string=query).json
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['registros'][0]['peatones_frame'], 5)
        self.assertEqual(result['registros'][0]['zona'], 'Entrada UTP')
        response = self.client.get('/reportes/descargar', query_string=query)
        frame = pd.read_csv(io.BytesIO(response.data))
        self.assertEqual(frame['id'].tolist(), [result['registros'][0]['id']])
        zipped = self.client.get('/reportes/descargar', query_string={**query, 'formato':'zip'})
        with zipfile.ZipFile(io.BytesIO(zipped.data)) as archive:
            self.assertEqual(len(pd.read_csv(archive.open('registros.csv'))), 1)
            self.assertIn('tendencias_por_minuto.csv', archive.namelist())
            self.assertEqual(pd.read_csv(archive.open('resumen_por_sesion.csv'))['max_peatones'][0], 5)

    def test_empty_invalid_and_alert_filters(self):
        self.assertEqual(self.client.get('/api/registros?desde=2026-11-16').json['total'], 0)
        for query in ['tipo=sesiones', 'sesion=1%20OR%201=1', 'desde=ayer', 'desde=2026-11-15&hasta=2026-11-14', 'nivel=CRITICO']:
            self.assertEqual(self.client.get('/api/registros?' + query).status_code, 400)
        response = self.client.get('/api/registros?tipo=alertas&nivel=ALTO')
        self.assertEqual(response.json['total'], 1)
        self.assertEqual(response.json['resumen']['alertas_altas'], 1)

    def test_database_integrity_and_foreign_keys(self):
        response = self.client.get('/api/base-datos').json
        self.assertEqual(response['integridad'], 'ok')
        self.assertEqual(response['relaciones_invalidas'], 0)
        self.assertEqual(response['tablas']['metricas'], 3)
        with self.assertRaises(sqlite3.IntegrityError):
            storage.guardar_alerta('ALTO', 'Sesión inexistente', 999999)
        with closing(storage.database_connection()) as conn:
            self.assertEqual(conn.execute('SELECT metricas_total FROM resumen_sesiones WHERE id=?', (self.first,)).fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
