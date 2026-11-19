-- Abrir sobre la base real o la base de demostración. Solo consultas de lectura.
PRAGMA integrity_check;
PRAGMA foreign_key_check;

-- Cada ejecución con sus cantidades de registros y cruces acumulados.
SELECT * FROM resumen_sesiones ORDER BY id DESC;

-- Alertas con la fuente y zona de origen.
SELECT a.id, a.fecha_hora, a.nivel, a.descripcion, s.nombre, s.fuente, s.zona
FROM alertas a LEFT JOIN sesiones s ON s.id = a.sesion_id
ORDER BY a.id DESC LIMIT 100;

-- Detecciones promedio por muestra y hora. No son personas únicas.
SELECT s.zona, strftime('%Y-%m-%d %H:00', m.fecha_hora) AS hora,
       COUNT(*) AS muestras, ROUND(AVG(m.peatones_frame), 2) AS peatones_promedio,
       ROUND(AVG(m.vehiculos_frame), 2) AS vehiculos_promedio,
       ROUND(AVG(m.fps), 2) AS fps_promedio
FROM metricas m LEFT JOIN sesiones s ON s.id=m.sesion_id
GROUP BY s.zona, hora ORDER BY hora;

-- Frecuencia de alertas por zona y nivel.
SELECT COALESCE(s.zona, 'Sin especificar') AS zona, a.nivel, COUNT(*) AS alertas
FROM alertas a LEFT JOIN sesiones s ON s.id=a.sesion_id
GROUP BY zona, a.nivel ORDER BY alertas DESC;
