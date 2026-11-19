(() => {
  const form = document.getElementById('records-filter');
  if (!form) return;
  let page = 1, pages = 1, active = new URLSearchParams();
  const text = (id, value) => { document.getElementById(id).textContent = value; };
  const columns = {
    metricas: [['id','ID'], ['fecha_hora','Fecha y hora'], ['analisis','Análisis'], ['zona','Zona'], ['peatones_frame','Peatones'], ['vehiculos_frame','Vehículos'], ['riesgo_alto_frame','Riesgo alto'], ['fps','FPS']],
    alertas: [['id','ID'], ['fecha_hora','Fecha y hora'], ['analisis','Análisis'], ['zona','Zona'], ['nivel','Nivel'], ['descripcion','Descripción']]
  };
  async function load() {
    const query = new URLSearchParams(active); query.set('pagina', page);
    text('records-message', 'Consultando…');
    try {
      const response = await fetch('/api/registros?' + query, {cache:'no-store'});
      const data = await response.json();
      if (!response.ok) throw Error(data.message);
      page = data.pagina; pages = data.paginas;
      const head = document.getElementById('records-head'); head.replaceChildren();
      const tr = document.createElement('tr');
      for (const [, label] of columns[data.tipo]) { const th = document.createElement('th'); th.textContent = label; tr.appendChild(th); }
      head.appendChild(tr);
      const body = document.getElementById('records-body'); body.replaceChildren();
      for (const row of data.registros) {
        const tr = document.createElement('tr');
        for (const [key] of columns[data.tipo]) { const td = document.createElement('td'); td.textContent = row[key] ?? '—'; tr.appendChild(td); }
        body.appendChild(tr);
      }
      text('records-message', data.total ? `${data.total} registros encontrados. Pulsa Consultar para actualizar durante un análisis.` : 'No hay registros para estos filtros.');
      text('records-page', `Página ${page} de ${pages}`);
      document.getElementById('records-prev').disabled = page <= 1;
      document.getElementById('records-next').disabled = page >= pages;
      const s = data.resumen;
      text('records-summary', data.tipo === 'metricas'
        ? `Máximo por muestra: ${s.max_peatones ?? 0} peatones · ${s.max_vehiculos ?? 0} vehículos · FPS promedio: ${s.fps_promedio ?? 0} · Muestras con riesgo alto: ${s.muestras_riesgo_alto ?? 0}`
        : `Alertas de nivel alto en la selección: ${s.alertas_altas ?? 0}`);
      document.getElementById('records-csv').href = '/reportes/descargar?' + active;
      const zip = new URLSearchParams(active); zip.set('formato','zip');
      document.getElementById('records-zip').href = '/reportes/descargar?' + zip;
    } catch (error) { text('records-message', error.message); }
  }
  form.addEventListener('submit', event => {
    event.preventDefault(); active = new URLSearchParams(new FormData(form)); page = 1; load();
  });
  document.getElementById('records-prev').addEventListener('click', () => { if (page > 1) { page--; load(); } });
  document.getElementById('records-next').addEventListener('click', () => { if (page < pages) { page++; load(); } });
  async function metadata() {
    try {
      const [dbResponse, sessionResponse] = await Promise.all([fetch('/api/base-datos'), fetch('/api/sesiones')]);
      if (!dbResponse.ok || !sessionResponse.ok) throw Error('No se pudo consultar SQLite.');
      const db = await dbResponse.json();
      text('database-status', db.integridad === 'ok' && !db.relaciones_invalidas ? 'SQLite · integridad correcta' : 'Revisar integridad SQLite');
      text('database-counts', `${db.tablas.sesiones} análisis · ${db.tablas.metricas} métricas · ${db.tablas.alertas} alertas almacenadas`);
      const select = document.getElementById('records-session');
      const selected = select.value;
      select.replaceChildren();
      for (const [value, label] of [['','Todos los análisis'], ['legacy','Anteriores sin sesión']]) {
        const option = document.createElement('option'); option.value = value; option.textContent = label; select.appendChild(option);
      }
      for (const row of await sessionResponse.json()) {
        const option = document.createElement('option'); option.value = row.id;
        option.textContent = `#${row.id} · ${row.nombre} · ${row.zona}`; select.appendChild(option);
      }
      select.value = selected;
    } catch (error) { text('database-status', error.message); }
  }
  metadata(); setInterval(metadata, 15000); load();
})();
