/* Como máximo dos imágenes en vuelo, sin cola de imágenes antiguas. */
class CameraTransport {
  constructor({token, onFeedback, onFatal, fetchImpl = (url, options) => globalThis.fetch(url, options), timeoutMs = 6000}) {
    this.token = token;
    this.onFeedback = onFeedback;
    this.onFatal = onFatal;
    this.fetchImpl = fetchImpl;
    this.timeoutMs = timeoutMs;
    this.pending = new Set();
    this.active = true;
    this.sequence = 0;
    this.rtt = 0;
    this.delivered = 0;
  }
  get available() { return this.active && this.pending.size < 2; }
  get profile() {
    if (this.rtt > 500) return {edge: 384, quality: .4, fps: 10};
    if (this.rtt > 200) return {edge: 640, quality: .5, fps: 20};
    return this.delivered ? {edge: 960, quality: .62, fps: 30} : {edge: 640, quality: .5, fps: 15};
  }
  send(blob) {
    if (!this.available) return false;
    const controller = new AbortController();
    this.pending.add(controller);
    const started = performance.now();
    const sequence = ++this.sequence;
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    Promise.resolve().then(() => this.fetchImpl('/api/camera/frame', {
      method: 'POST', body: blob, signal: controller.signal,
      headers: {'X-Camera-Session': this.token, 'X-Frame-Sequence': String(sequence)}
    })).then(async response => {
      if (!this.active) return;
      if (!response.ok) {
        if ([401, 403, 409].includes(response.status)) {
          const body = await response.json();
          this.onFatal(body.message || 'Vuelve a vincular la cámara.');
          this.stop();
          return;
        }
        throw new Error('La red no pudo entregar la imagen.');
      }
      const elapsed = performance.now() - started;
      this.delivered++;
      this.rtt = this.rtt ? .7 * this.rtt + .3 * elapsed : elapsed;
      this.onFeedback({ok: true, rtt: Math.round(this.rtt), edge: this.profile.edge, delivered: this.delivered});
    }).catch(error => {
      if (!this.active) return;
      this.rtt = Math.max(this.rtt, 600);
      this.onFeedback({ok: false, rtt: Math.round(this.rtt), edge: this.profile.edge,
        error: error.name === 'AbortError' ? 'La imagen no recibió respuesta en 6 segundos.' : error.message});
    }).finally(() => {
      clearTimeout(timer);
      this.pending.delete(controller);
    });
    return true;
  }
  stop() {
    this.active = false;
    for (const controller of this.pending) controller.abort();
    this.pending.clear();
  }
}
if (typeof module !== 'undefined') module.exports = {CameraTransport};
