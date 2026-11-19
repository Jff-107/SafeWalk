(async () => {
  try {
    const response = await fetch('/api/mobile/link', {cache: 'no-store'});
    if (!response.ok) return;
    const connection = await response.json();
    if (!connection.url) return;
    const qr = document.getElementById('mobile-qr');
    qr.src = '/api/mobile/qr'; qr.hidden = false;
    const link = document.getElementById('mobile-phone-link');
    link.href = connection.url; link.hidden = false;
    document.getElementById('mobile-connection-status').textContent = 'Escanea este QR con tu celular, permite la cámara y pulsa Conectar y transmitir. Mantén abierto este panel en la laptop.';
  } catch (error) { /* Las instrucciones locales permanecen visibles. */ }
})();
