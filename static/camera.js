const cameraVideo = document.getElementById('camera-preview');
const cameraMessage = document.getElementById('camera-message');
const cameraStart = document.getElementById('start-camera');
const cameraStop = document.getElementById('stop-camera');
const cameraCanvas = document.createElement('canvas');
let cameraStream = null, cameraTransport = null;
let cameraActive = false, cameraOpening = false;
let cameraGeneration = 0, cameraTimer = null;
let cameraFramesCaptured = 0, cameraWaitingSince = 0;
let cameraLastFeedback = 0, cameraFrameHandle = null;
let cameraPresentedFrames = 0, cameraLastCapturedFrame = -1;
let cameraManualRotation = 0;
const cameraRotate = document.getElementById('rotate-camera');
const cameraIsMobile = (navigator.maxTouchPoints || 0) > 0;

function currentCameraOrientation() {
  const orientation = globalThis.screen?.orientation;
  const angle = orientation?.angle ?? window.orientation ?? 0;
  const landscape = !cameraIsMobile ? null : orientation?.type
    ? orientation.type.startsWith('landscape')
    : typeof window.matchMedia === 'function' ? window.matchMedia('(orientation: landscape)').matches : Math.abs(angle) === 90;
  return {angle, landscape};
}
cameraRotate?.addEventListener('click', () => {
  cameraManualRotation = (cameraManualRotation + 90) % 360;
  cameraMessage.textContent = 'Imagen girada 90°. El cambio aparecerá en la laptop con el próximo fotograma.';
});
const cameraErrors = {
  NotAllowedError: 'Permite el acceso a la cámara en el navegador y vuelve a intentarlo.',
  NotFoundError: 'No se encontró una cámara disponible.',
  NotReadableError: 'Otra aplicación está usando la cámara. Ciérrala y vuelve a intentarlo.'
};

async function closeCamera() {
  cameraActive = false;
  cameraGeneration++;
  clearTimeout(cameraTimer);
  if (cameraFrameHandle !== null) cameraVideo.cancelVideoFrameCallback?.(cameraFrameHandle);
  cameraFrameHandle = null;
  cameraTransport?.stop();
  cameraTransport = null;
  cameraStream?.getTracks().forEach(track => track.stop());
  cameraStream = null;
  cameraVideo.srcObject = null;
  cameraStart.disabled = false;
  cameraStop.disabled = true;
  await fetch('/api/camera/stop', {method: 'POST', signal: AbortSignal.timeout(3000)}).catch(() => {});
}

async function captureCameraFrame(generation) {
  if (!cameraActive || generation !== cameraGeneration) return;
  const started = performance.now();
  const sender = cameraTransport;
  try {
    const hasNewFrame = cameraPresentedFrames === 0 || cameraPresentedFrames !== cameraLastCapturedFrame;
    if (sender.available && hasNewFrame && cameraVideo.readyState >= 2 && cameraVideo.videoWidth && cameraVideo.videoHeight) {
      cameraLastCapturedFrame = cameraPresentedFrames;
      const profile = sender.profile;
      const geometry = cameraGeometry({width: cameraVideo.videoWidth, height: cameraVideo.videoHeight,
        edge: profile.edge, ...currentCameraOrientation(), manual: cameraManualRotation});
      const {width, height} = geometry;
      if (cameraCanvas.width !== width || cameraCanvas.height !== height) {
        cameraCanvas.width = width; cameraCanvas.height = height;
      }
      drawCameraFrame(cameraCanvas.getContext('2d'), cameraVideo, geometry);
      const blob = await new Promise(resolve => {
        const timer = setTimeout(() => resolve(null), 1500);
        cameraCanvas.toBlob(result => { clearTimeout(timer); resolve(result); }, 'image/jpeg', profile.quality);
      });
      if (!cameraActive || generation !== cameraGeneration) return;
      if (blob) {
        cameraFramesCaptured++;
        sender.send(blob);
      } else {
        cameraMessage.textContent = 'La cámara no generó una imagen JPEG. Detén y vuelve a conectar.';
      }
    } else if (!cameraFramesCaptured && performance.now() - cameraWaitingSince > 3000) {
      cameraMessage.textContent = 'Esperando imagen de la cámara. Mantén esta página visible; si no aparece la vista previa, detén y vuelve a permitir la cámara.';
    }
  } catch (error) {
    cameraMessage.textContent = 'No se pudo capturar la imagen. Detén y vuelve a conectar la cámara.';
  }
  if (cameraActive && generation === cameraGeneration) {
    cameraTimer = setTimeout(() => captureCameraFrame(generation), Math.max(0, 1000 / sender.profile.fps - (performance.now() - started)));
  }
}

cameraStart.addEventListener('click', async () => {
  if (cameraOpening || cameraActive) return;
  cameraOpening = true;
  cameraStart.disabled = true;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('La cámara requiere HTTPS o localhost. Abre el enlace del modo celular.');
    cameraStream = await navigator.mediaDevices.getUserMedia({audio: false, video: {
      facingMode: {ideal: document.getElementById('camera-facing').value},
      width: {ideal: 640}, height: {ideal: 480}, frameRate: {ideal: 30, max: 30}
    }});
    cameraVideo.srcObject = cameraStream;
    await cameraVideo.play();
    const response = await fetch('/api/camera/start', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({zona: document.getElementById('camera-zone')?.value || 'Sin especificar'}), signal: AbortSignal.timeout(8000)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.message);
    cameraActive = true;
    cameraStop.disabled = false;
    cameraFramesCaptured = 0;
    cameraLastFeedback = 0;
    cameraPresentedFrames = 0;
    cameraLastCapturedFrame = -1;
    cameraWaitingSince = performance.now();
    const generation = ++cameraGeneration;
    if (cameraVideo.requestVideoFrameCallback) {
      const observeFrame = () => {
        if (!cameraActive || generation !== cameraGeneration) return;
        cameraPresentedFrames++;
        cameraFrameHandle = cameraVideo.requestVideoFrameCallback(observeFrame);
      };
      cameraFrameHandle = cameraVideo.requestVideoFrameCallback(observeFrame);
    }
    cameraTransport = new CameraTransport({token: data.camera_token,
      onFeedback: feedback => {
        if (generation !== cameraGeneration) return;
        const now = performance.now();
        if (feedback.ok && feedback.delivered > 1 && now - cameraLastFeedback < 350) return;
        cameraLastFeedback = now;
        cameraMessage.textContent = feedback.ok
          ? `${feedback.delivered} imágenes entregadas a la laptop · red ${feedback.rtt} ms · hasta ${feedback.edge} px.`
          : `No se entregó la imagen: ${feedback.error || 'fallo de conexión'}. Reintentando con una imagen más pequeña…`;
      },
      onFatal: message => {
        if (generation !== cameraGeneration) return;
        cameraMessage.textContent = message;
        closeCamera();
      }
    });
    cameraMessage.textContent = 'Enviando imágenes. La laptop puede mostrarlas mientras YOLO se prepara…';
    captureCameraFrame(generation);
  } catch (error) {
    if (cameraActive) await closeCamera();
    cameraStream?.getTracks().forEach(track => track.stop());
    cameraStream = null;
    cameraStart.disabled = false;
    cameraMessage.textContent = cameraErrors[error.name] || error.message;
  } finally {
    cameraOpening = false;
  }
});
cameraStop.addEventListener('click', async () => {
  await closeCamera();
  cameraMessage.textContent = 'Cámara apagada.';
});
window.addEventListener('pagehide', () => {
  if (!cameraActive) return;
  cameraTransport?.stop();
  cameraStream?.getTracks().forEach(track => track.stop());
  navigator.sendBeacon('/api/camera/stop');
});
