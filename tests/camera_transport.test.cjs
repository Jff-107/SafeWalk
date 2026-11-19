const {test} = require('node:test');
const assert = require('node:assert/strict');
const {CameraTransport} = require('../static/camera-transport.js');
const settle = () => new Promise(resolve => setImmediate(resolve));

test('fetch nativo conserva el contexto del navegador y entrega la imagen', async () => {
  const original = globalThis.fetch;
  let calls = 0;
  const feedback = [];
  globalThis.fetch = function (url, options) {
    assert.equal(this, globalThis, 'fetch requiere el contexto Window/globalThis');
    assert.equal(url, '/api/camera/frame');
    calls++;
    return Promise.resolve({ok: true});
  };
  const sender = new CameraTransport({token: 'session', onFeedback: x => feedback.push(x), onFatal: assert.fail});
  try {
    sender.send('jpeg');
    await settle();
    assert.equal(calls, 1);
    assert.equal(feedback[0].ok, true);
    assert.equal(feedback[0].delivered, 1);
  } finally {
    sender.stop();
    globalThis.fetch = original;
  }
});

test('como máximo dos imágenes; no crea una cola si la red se demora', async () => {
  const requests = [];
  const feedback = [];
  const sender = new CameraTransport({token: 'session', onFeedback: x => feedback.push(x), onFatal: assert.fail,
    fetchImpl: (url, options) => new Promise(resolve => requests.push({options, resolve}))});
  assert.equal(sender.send('first'), true);
  assert.equal(sender.send('second'), true);
  assert.equal(sender.send('old-third'), false);
  await settle();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].options.headers['X-Frame-Sequence'], '2');
  assert.equal(requests[1].options.headers['X-Camera-Session'], 'session');
  requests.forEach(request => request.resolve({ok: true}));
  await settle();
  assert.equal(sender.available, true);
  assert.equal(feedback.length, 2);
  sender.stop();
});

test('un fallo temporal reduce tamaño y permite enviar una imagen nueva', async () => {
  let fail = true;
  const sender = new CameraTransport({token: 'session', onFeedback: () => {}, onFatal: assert.fail,
    fetchImpl: async () => { if (fail) throw Error('sin red'); return {ok: true}; }});
  sender.send('first');
  await settle();
  assert.equal(sender.profile.edge, 384);
  assert.equal(sender.active, true);
  fail = false;
  assert.equal(sender.send('fresh'), true);
  await settle();
  sender.stop();
});

test('detener aborta imágenes pendientes e impide envíos posteriores', async () => {
  let signal;
  const sender = new CameraTransport({token: 'session', onFeedback: assert.fail, onFatal: assert.fail,
    fetchImpl: (url, options) => new Promise((resolve, reject) => {
      signal = options.signal;
      signal.addEventListener('abort', () => reject(Error('cancelada')));
    })});
  sender.send('first');
  await settle();
  sender.stop();
  assert.equal(signal.aborted, true);
  assert.equal(sender.send('late'), false);
  await settle();
});
