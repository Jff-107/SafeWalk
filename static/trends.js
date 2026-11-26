(() => {
  const canvas = document.getElementById('trend-chart');
  const status = document.getElementById('trend-status');
  const description = document.getElementById('trend-description');
  let samples = [];
  const series = [['peatones_frame', '#315ee8'], ['vehiculos_frame', '#078ca7'], ['riesgo_alto_frame', '#c73b48']];

  function draw() {
    const width = canvas.parentElement.clientWidth;
    if (!width) return;
    const height = 280;
    const scale = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d');
    ctx.scale(scale, scale);
    const left = 42, right = width - 18, top = 20, bottom = height - 38;
    const maximum = Math.max(4, ...samples.flatMap(row => series.map(([key]) => row[key] || 0)));
    const limit = Math.ceil(maximum / 4) * 4;
    ctx.font = '11px Segoe UI, sans-serif';
    for (let tick = 0; tick <= 4; tick++) {
      const y = bottom - (bottom - top) * tick / 4;
      ctx.strokeStyle = '#e7ecf2';
      ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
      ctx.fillStyle = '#6b7788'; ctx.textAlign = 'right'; ctx.fillText(String(limit * tick / 4), left - 8, y + 4);
    }
    if (!samples.length) {
      ctx.fillStyle = '#6b7788'; ctx.textAlign = 'center';
      ctx.fillText('Esperando muestras del análisis…', width / 2, height / 2);
      return;
    }
    const times = samples.map(row => new Date(row.fecha_hora).getTime());
    const first = times[0];
    const span = Math.max(1000, times[times.length - 1] - first);
    const x = index => left + (right - left) * (times[index] - first) / span;
    for (const [key, color] of series) {
      ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 2;
      ctx.beginPath();
      samples.forEach((row, index) => {
        const y = bottom - (bottom - top) * row[key] / limit;
        if (index) ctx.lineTo(x(index), y); else ctx.moveTo(x(index), y);
      });
      ctx.stroke();
      if (samples.length < 40) samples.forEach((row, index) => {
        ctx.beginPath(); ctx.arc(x(index), bottom - (bottom - top) * row[key] / limit, 3, 0, 2 * Math.PI); ctx.fill();
      });
    }
    ctx.fillStyle = '#6b7788';
    const indices = [...new Set([0, Math.floor((samples.length - 1) / 2), samples.length - 1])];
    for (const index of indices) {
      ctx.textAlign = index === 0 ? 'left' : index === samples.length - 1 ? 'right' : 'center';
      ctx.fillText(samples[index].fecha_hora.slice(11, 19), x(index), height - 12);
    }
  }

  async function poll() {
    // Pide las muestras de la sesión visible cada segundo y redibuja el gráfico.
    try {
      const source = document.body.dataset.analysisSource || 'video';
      const response = await fetch(`/api/tendencias?fuente=${encodeURIComponent(source)}`,
        {cache: 'no-store', signal: AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error('Sin conexión');
      const data = await response.json();
      samples = data.muestras;
      draw();
      status.textContent = data.sesion?.estado === 'en curso' ? '● En vivo · cada 1 s' : 'Último análisis';
      description.textContent = `${samples.length} muestras · ${data.sesion?.nombre || 'Sin análisis'} · actualizado ${new Date().toLocaleTimeString()}. Las detecciones no equivalen a personas únicas.`;
    } catch (error) {
      status.textContent = 'Reconectando…';
    } finally {
      setTimeout(poll, 1000);
    }
  }
  new ResizeObserver(draw).observe(canvas.parentElement);
  poll();
})();
