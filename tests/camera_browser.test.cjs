const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

test('página móvil captura y entrega JPEG con fetch de navegador y currentTime fijo', async () => {
  const handlers = {};
  const elements = {};
  for (const id of ['camera-preview', 'camera-message', 'start-camera', 'stop-camera', 'camera-facing']) {
    elements[id] = {textContent: '', value: 'environment', addEventListener: (event, fn) => { handlers[`${id}:${event}`] = fn; }};
  }
  Object.assign(elements['camera-preview'], {
    videoWidth: 640, videoHeight: 480, currentTime: 0, readyState: 2,
    play: async () => {}
  });
  const requests = [];
  const sandbox = {
    document: {
      getElementById: id => elements[id],
      createElement: () => ({getContext: () => ({save() {}, translate() {}, rotate() {}, restore() {}, drawImage() {}}), toBlob: callback => callback(new Blob(['jpeg']))})
    },
    navigator: {mediaDevices: {getUserMedia: async () => ({getTracks: () => [{stop() {}}]})}},
    window: {addEventListener() {}},
    recordFetch: async (url, options) => {
      requests.push({url, options});
      return {ok: true, status: 200, json: async () => ({camera_token: 'test-session'})};
    },
    setTimeout, clearTimeout, performance, AbortController, AbortSignal, Blob, console
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(`globalThis.fetch = function(url, options) {
    if (this !== globalThis) throw new TypeError('Illegal invocation');
    return recordFetch(url, options);
  };`, context);
  for (const file of ['camera-transport.js', 'camera-geometry.js', 'camera.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static', file), 'utf8'), context);
  }
  try {
    await handlers['start-camera:click']();
    await new Promise(resolve => setTimeout(resolve, 220));
    const images = requests.filter(request => request.url === '/api/camera/frame');
    assert.ok(images.length >= 2, `Solo se enviaron ${images.length} imágenes`);
    assert.equal(images[0].options.headers['X-Camera-Session'], 'test-session');
    assert.match(elements['camera-message'].textContent, /imágenes entregadas/);
  } finally {
    await handlers['stop-camera:click']();
  }
});

test('visor de laptop presenta JPEG completos y libera imágenes anteriores', async () => {
  const events = {};
  const placeholder = {hidden: false};
  const detail = {textContent: ''};
  const revoked = [];
  let displayed = 0;
  const layout = {style: {}, classList: {toggle(name, value) { this[name] = value; }}};
  const image = {naturalWidth: 640, naturalHeight: 480, closest: () => layout};
  Object.defineProperty(image, 'src', {set(value) {
    displayed++;
    queueMicrotask(() => image.onload());
  }});
  let calls = 0;
  const context = vm.createContext({
    document: {getElementById: id => id === 'processed-video' ? image : id === 'video-placeholder' ? placeholder : detail},
    window: {addEventListener: (event, fn) => { events[event] = fn; }},
    fetch: async () => {
      calls++;
      return calls <= 2 ? {ok: true, status: 200, blob: async () => new Blob(['jpeg']), headers: {get: () => String(calls)}} : {status: 204};
    },
    URL: {createObjectURL: () => `blob:${calls}`, revokeObjectURL: url => revoked.push(url)},
    setTimeout, performance, AbortSignal, console
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/video-viewer.js'), 'utf8'), context);
  try {
    await new Promise(resolve => setTimeout(resolve, 130));
    assert.equal(displayed, 2);
    assert.equal(placeholder.hidden, true);
    assert.equal(layout.style.aspectRatio, '640 / 480');
    assert.equal(layout.classList['portrait-video'], false);
    assert.ok(revoked.includes('blob:1'));
  } finally {
    events.pagehide();
  }
});
