"""Entrega de datos reproducible. Nunca introduce datos simulados en la base real."""
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import zipfile
import pandas as pd

from src.main import DB_PATH, preparar_base_datos
from src.reporting import report_zip, report_frames, csv_bytes

ROOT = Path(__file__).resolve().parent


def main():
    preparar_base_datos()
    destination = ROOT / 'outputs' / 'entrega_datos'
    destination.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as source:
        schema = '\n\n'.join(row[0] + ';' for row in source.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name"))
        real = destination / 'safewalk_registros_reales.db'
        with closing(sqlite3.connect(real)) as target:
            source.backup(target)
        if source.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Revisa la integridad de la base original antes de entregar.')
    (destination / 'schema.sql').write_text('PRAGMA foreign_keys=ON;\nPRAGMA user_version=2;\n' + schema, encoding='utf-8')
    (destination / 'reportes_reales.zip').write_bytes(report_zip(real, {}).getvalue())
    demo = destination / 'safewalk_DEMOSTRACION.db'
    # Recrea únicamente el archivo de demostración generado por este script.
    with closing(sqlite3.connect(':memory:')) as sample:
        sample.executescript(schema)
        sample.execute('PRAGMA user_version=2')
        sample.execute('PRAGMA foreign_keys=ON')
        start = datetime(2026, 11, 14, 8, 0)
        for session_id, zone, source in [(1, 'SIMULADA - Entrada UTP', 'video'), (2, 'SIMULADA - Cruce principal', 'camera')]:
            beginning = start + timedelta(hours=session_id - 1)
            sample.execute('INSERT INTO sesiones(id,fuente,nombre,inicio,fin,estado,zona) VALUES (?,?,?,?,?,?,?)',
                (session_id, source, f'DEMOSTRACION SIMULADA {session_id}', beginning.isoformat(),
                 (beginning + timedelta(seconds=119)).isoformat(), 'finalizado', zone))
            for second in range(120):
                timestamp = (beginning + timedelta(seconds=second)).isoformat()
                people = 2 + (second // 10 + session_id) % 7
                vehicles = 1 + (second // 8 + session_id) % 5
                high = int(second % 30 < 5)
                medium = int(not high and second % 15 < 5)
                sample.execute('''INSERT INTO metricas(fecha_hora,peatones_frame,vehiculos_frame,
                    peatones_cruzaron,vehiculos_cruzaron,fps,riesgo_medio_frame,riesgo_alto_frame,sesion_id)
                    VALUES (?,?,?,?,?,?,?,?,?)''',
                    (timestamp, people, vehicles, second//12, second//18, 16+second%8, medium, high, session_id))
                if second % 30 == 0:
                    sample.execute('INSERT INTO alertas(fecha_hora,nivel,descripcion,sesion_id) VALUES (?,?,?,?)',
                        (timestamp, 'ALTO', 'EVENTO SIMULADO: ejemplo académico, no observado en una cámara.', session_id))
        sample.execute("CREATE TABLE informacion_demo (advertencia TEXT NOT NULL)")
        sample.execute("INSERT INTO informacion_demo VALUES ('DATOS SINTETICOS. No son resultados reales ni validan la precisión de YOLO.')")
        sample.commit()
        with closing(sqlite3.connect(demo)) as target:
            sample.backup(target)
    (destination / 'reportes_DEMOSTRACION.zip').write_bytes(report_zip(demo, {}).getvalue())
    for path, suffix in [(real, 'reales'), (demo, 'DEMOSTRACION')]:
        frame, _, _ = report_frames(path, {'tipo':'alertas'})
        (destination / f'alertas_{suffix}.csv').write_bytes(csv_bytes(frame))
        with closing(sqlite3.connect(path)) as conn:
            sessions = pd.read_sql_query('SELECT * FROM resumen_sesiones ORDER BY id', conn)
        (destination / f'sesiones_{suffix}.csv').write_bytes(csv_bytes(sessions))
    readme = ('SafeWalk - Entrega de datos\n\n'
        'safewalk_registros_reales.db: copia coherente de SQLite al ejecutar el script.\n'
        'safewalk_DEMOSTRACION.db: 2 sesiones, 240 métricas y 8 alertas SINTETICAS, claramente identificadas.\n'
        'reportes_reales.zip / reportes_DEMOSTRACION.zip: CSV preparados con pandas.\n'
        'alertas_*.csv / sesiones_*.csv: historial y metadatos por ejecución.\n'
        'schema.sql: estructura SQL sin registros, para crear una base vacía.\n\n'
        'La aplicación utiliza outputs/safewalk.db. Esta carpeta contiene copias para revisión y entrega.\n'
        'Puedes abrir los .db con un visor SQLite y consultar sesiones, metricas, alertas y resumen_sesiones.\n'
        'La base real puede contener nombres de archivos y zonas: revisa su contenido antes de compartirla.\n'
        'Los datos de demostración nunca se insertan en la base real.\n')
    (destination / 'LEEME.txt').write_text(readme, encoding='utf-8')
    package = destination / 'SafeWalk_entrega_datos.zip'
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ['safewalk_registros_reales.db', 'safewalk_DEMOSTRACION.db', 'schema.sql',
                     'reportes_reales.zip', 'reportes_DEMOSTRACION.zip', 'LEEME.txt']:
            archive.write(destination / name, name)
        for path in sorted(destination.glob('*.csv')):
            archive.write(path, path.name)
        archive.write(ROOT / 'database' / 'consultas.sql', 'consultas.sql')
        archive.write(ROOT / 'database' / 'README.md', 'guia_exposicion.md')
    print(f'Entrega creada: {package}')


if __name__ == '__main__':
    main()
