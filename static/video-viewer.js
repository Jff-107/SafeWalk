/* Una imagen completa por petición; no mantiene un MJPEG cargando indefinidamente. */
(() => {
  const image = document.getElementById('processed-video');
  if (!image) return;
  let version = -1, currentUrl = null, stopped = false;
  async function next() {
    if (stopped) return;
    const started = performance.now();
    let delay = 33;
    try {
      const response = await fetch(`/api/video/frame?after=${version}`, {
        cache: 'no-store', signal: AbortSignal.timeout(4000)
      });
      if (response.status === 401) throw Error('Vuelve a abrir el enlace del panel de la laptop.');
      if (response.status === 204) {
        delay = 100;
      } else {
        if (!response.ok) throw Error('No se pudo obtener la imagen del servidor.');
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        await new Promise((resolve, reject) => {
          image.onload = resolve;
          image.onerror = () => { URL.revokeObjectURL(url); reject(Error('Imagen incompleta. Reintentando…')); };
          image.src = url;
        });
        if (currentUrl) URL.revokeObjectURL(currentUrl);
        currentUrl = url;
        if (image.naturalWidth && image.naturalHeight) {
          const screen = image.closest?.('.video-screen');
          if (screen) {
            screen.style.aspectRatio = `${image.naturalWidth} / ${image.naturalHeight}`;
            screen.classList.toggle('portrait-video', image.naturalHeight > image.naturalWidth);
          }
        }
        version = Number(response.headers.get('X-Frame-Version'));
        document.getElementById('video-placeholder').hidden = true;
      }
    } catch (error) {
      document.getElementById('analysis-detail').textContent = error.message;
      delay = 500;
    }
    if (!stopped) setTimeout(next, Math.max(0, delay - (performance.now() - started)));
  }
  window.addEventListener('pagehide', () => {
    stopped = true;
    if (currentUrl) URL.revokeObjectURL(currentUrl);
  });
  next();
})();
