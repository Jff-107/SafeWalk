const {test} = require('node:test');
const assert = require('node:assert/strict');
const {cameraGeometry, drawCameraFrame} = require('../static/camera-geometry.js');

test('video horizontal del navegador se conserva sin un segundo giro', () => {
  const result = cameraGeometry({width: 1280, height: 720, edge: 640, landscape: true, angle: 90});
  assert.equal(result.width, 640);
  assert.equal(result.height, 360);
  assert.equal(result.rotation, 0);
});
test('teléfono horizontal con captura bloqueada en vertical gira 90 grados', () => {
  const result = cameraGeometry({width: 480, height: 640, edge: 640, landscape: true, angle: 90});
  assert.equal(result.width, 640);
  assert.equal(result.height, 480);
  assert.equal(result.rotation, 90);
});
test('girar hacia el lado contrario conserva una salida horizontal', () => {
  const result = cameraGeometry({width: 480, height: 640, edge: 640, landscape: true, angle: 270});
  assert.equal(result.width, 640);
  assert.equal(result.height, 480);
  assert.equal(result.rotation, 270);
});
test('volver a vertical y corregir manualmente no deforma la imagen', () => {
  const vertical = cameraGeometry({width: 480, height: 640, edge: 480, landscape: false});
  assert.equal(vertical.width / vertical.height, 480 / 640);
  const manual = cameraGeometry({width: 640, height: 480, edge: 640, manual: 90});
  assert.equal(manual.width, 480);
  assert.equal(manual.height, 640);
});
test('la rotación se aplica al JPEG dibujado y restaura el contexto', () => {
  const calls = [];
  const context = Object.fromEntries(['save', 'translate', 'rotate', 'drawImage', 'restore'].map(name =>
    [name, (...args) => calls.push([name, ...args])]));
  const geometry = cameraGeometry({width: 480, height: 640, edge: 640, landscape: true, angle: 90});
  drawCameraFrame(context, 'video', geometry);
  assert.deepEqual(calls[1], ['translate', 320, 240]);
  assert.deepEqual(calls[2], ['rotate', Math.PI / 2]);
  assert.deepEqual(calls[3], ['drawImage', 'video', -240, -320, 480, 640]);
  assert.deepEqual(calls.at(-1), ['restore']);
});
