"""Consultas con parámetros y reportes pandas; SQLite conserva los originales."""
from contextlib import closing
from datetime import datetime, timedelta
import io
import sqlite3
import zipfile
import pandas as pd


def selection(args):
    kind = args.get('tipo', 'metricas')
    if kind not in {'metricas', 'alertas'}:
        raise ValueError('Tipo de registro inválido.')
    clauses, values = [], []
    session = args.get('sesion', '')
    if session == 'legacy':
        clauses.append('r.sesion_id IS NULL')
    elif session:
        try:
            session_id = int(session)
            if session_id <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Sesión inválida.')
        clauses.append('r.sesion_id=?')
        values.append(session_id)
    dates = {}
    for key in ('desde', 'hasta'):
        if args.get(key):
            try:
                dates[key] = datetime.strptime(args[key], '%Y-%m-%d')
            except (ValueError, TypeError):
                raise ValueError('Las fechas deben tener el formato AAAA-MM-DD.')
    if dates.get('desde') and dates.get('hasta') and dates['desde'] > dates['hasta']:
        raise ValueError('La fecha inicial no puede ser posterior a la final.')
    if 'desde' in dates:
        clauses.append('r.fecha_hora>=?')
        values.append(dates['desde'].isoformat())
    if 'hasta' in dates:
        clauses.append('r.fecha_hora<?')
        values.append((dates['hasta'] + timedelta(days=1)).isoformat())
    risk = args.get('nivel', '')
    if risk and risk not in {'ALTO', 'MEDIO', 'BAJO'}:
        raise ValueError('Nivel de riesgo inválido.')
    if risk:
        if kind == 'alertas':
            clauses.append('r.nivel=?')
            values.append(risk)
        else:
            clauses.append({'ALTO': 'r.riesgo_alto_frame>0', 'MEDIO': 'r.riesgo_medio_frame>0',
                            'BAJO': 'r.riesgo_alto_frame=0 AND r.riesgo_medio_frame=0'}[risk])
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    source = f' FROM {kind} r LEFT JOIN sesiones s ON s.id=r.sesion_id'
    columns = "r.*, COALESCE(s.nombre,'Registro anterior sin sesión') AS analisis, COALESCE(s.fuente,'sin sesión') AS fuente, COALESCE(s.zona,'Sin especificar') AS zona"
    return kind, source, where, values, columns


def records(db_path, args):
    kind, source, where, values, columns = selection(args)
    try:
        page = max(1, int(args.get('pagina', 1)))
    except (ValueError, TypeError):
        raise ValueError('Página inválida.')
    size = 25
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute('SELECT COUNT(*)' + source + where, values).fetchone()[0]
        pages = max(1, (total + size - 1) // size)
        page = min(page, pages)
        rows = conn.execute('SELECT ' + columns + source + where + ' ORDER BY r.id DESC LIMIT ? OFFSET ?',
                            [*values, size, (page-1)*size]).fetchall()
        if kind == 'metricas':
            summary = dict(conn.execute('''SELECT MAX(r.peatones_frame) max_peatones,
                MAX(r.vehiculos_frame) max_vehiculos, ROUND(AVG(r.fps),2) fps_promedio,
                SUM(CASE WHEN r.riesgo_alto_frame>0 THEN 1 ELSE 0 END) muestras_riesgo_alto'''
                + source + where, values).fetchone())
        else:
            summary = dict(conn.execute("SELECT SUM(CASE WHEN r.nivel='ALTO' THEN 1 ELSE 0 END) alertas_altas" + source + where, values).fetchone())
    return {'tipo': kind, 'registros': [dict(row) for row in rows], 'total': total,
            'pagina': page, 'paginas': pages, 'resumen': summary}


def report_frames(db_path, args):
    kind, source, where, values, columns = selection(args)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute('BEGIN')
        frame = pd.read_sql_query('SELECT ' + columns + source + where + ' ORDER BY r.id', conn, params=values)
    if frame.empty:
        return frame, pd.DataFrame(columns=['sesion_id', 'registros']), pd.DataFrame(columns=['minuto', 'registros'])
    prepared = frame.copy()
    prepared['sesion_id'] = prepared['sesion_id'].fillna(-1).astype(int)
    group = prepared.groupby(['sesion_id', 'analisis', 'zona'], dropna=False)
    if kind == 'metricas':
        summary = group.agg(registros=('id','count'), max_peatones=('peatones_frame','max'),
            max_vehiculos=('vehiculos_frame','max'), fps_promedio=('fps','mean'),
            cruces_peatonales_acumulados=('peatones_cruzaron','max'),
            cruces_vehiculares_acumulados=('vehiculos_cruzaron','max')).reset_index().round(2)
        prepared['minuto'] = pd.to_datetime(prepared['fecha_hora'], errors='coerce').dt.floor('min')
        trend = prepared.dropna(subset=['minuto']).groupby('minuto').agg(
            registros=('id','count'), peatones_promedio=('peatones_frame','mean'),
            vehiculos_promedio=('vehiculos_frame','mean'), riesgo_alto_promedio=('riesgo_alto_frame','mean')).reset_index()
        numeric = trend.select_dtypes(include='number').columns
        trend[numeric] = trend[numeric].round(2)
    else:
        summary = group.size().reset_index(name='registros')
        prepared['minuto'] = pd.to_datetime(prepared['fecha_hora'], errors='coerce').dt.floor('min')
        trend = prepared.dropna(subset=['minuto']).groupby(['minuto', 'nivel']).size().reset_index(name='registros')
    return frame, summary, trend


def csv_bytes(frame):
    # Neutraliza fórmulas en textos externos al abrir el CSV en una hoja de cálculo.
    clean = frame.copy()
    for col in clean.select_dtypes(include=['object', 'string']).columns:
        clean[col] = clean[col].map(lambda value: "'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')) else value)
    return clean.to_csv(index=False).encode('utf-8-sig')


def report_zip(db_path, args):
    frame, summary, trend = report_frames(db_path, args)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in [('registros.csv', frame), ('resumen_por_sesion.csv', summary), ('tendencias_por_minuto.csv', trend)]:
            archive.writestr(name, csv_bytes(data))
        archive.writestr('LEEME.txt', 'SafeWalk: reporte generado con pandas desde SQLite.\n'
            'Los archivos respetan los filtros de consulta. Los promedios son detecciones por muestra, no personas únicas.\n'
            'Los cruces son acumulados: el máximo dentro de un filtro no equivale a cruces ocurridos en ese intervalo.\n'
            'sesion_id -1 representa registros anteriores sin sesión. Riesgo por proximidad visual, sin validación de seguridad vial.\n'
            'Filtros: ' + repr(dict(args)))
    output.seek(0)
    return output
