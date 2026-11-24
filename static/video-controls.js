(() => {
  const toggle = document.getElementById('video-toggle');
  if (!toggle) return;
  const back = document.getElementById('video-back');
  const forward = document.getElementById('video-forward');
  const seek = document.getElementById('video-seek');
  const clock = document.getElementById('video-time');
  const message = document.getElementById('video-control-message');
  let state = null;
  let dragging = false;
  let sending = false;
  const format = value => {
    const seconds = Math.floor(Math.max(0, Number(value) || 0));
    return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  };
  function render() {
    const active = state?.running && state?.source === 'video';
    for (const control of [toggle, back, forward, seek]) control.disabled = !active || sending;
    toggle.textContent = state?.video_paused ? '▶ Reproducir' : '⏸ Pausar';
    const duration = state?.video_duration || 0;
    seek.max = duration;
    if (!dragging) seek.value = Math.min(duration, state?.video_position || 0);
    clock.textContent = `${format(dragging ? seek.value : state?.video_position)} / ${format(duration)}`;
  }
  async function command(action, seconds) {
    if (sending || !state?.running || state.source !== 'video') return;
    sending = true;
    render();
    try {
      const response = await fetch('/api/video/control', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action, seconds})
      });
      const data = await response.json();
      if (!response.ok) throw Error(data.message || 'No se pudo mover el video.');
      state = data.analysis;
      message.textContent = '';
    } catch (error) {
      message.textContent = error.message;
    } finally {
      sending = false;
      render();
    }
  }
  toggle.addEventListener('click', () => command(state?.video_paused ? 'play' : 'pause'));
  back.addEventListener('click', () => command('back'));
  forward.addEventListener('click', () => command('forward'));
  seek.addEventListener('input', () => { dragging = true; render(); });
  seek.addEventListener('change', () => { dragging = false; command('seek', Number(seek.value)); });
  window.addEventListener('safewalk:analysis', event => { state = event.detail; render(); });
  render();
})();
