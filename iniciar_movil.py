"""Lanzador de demostración: panel local + enlace HTTPS temporal para el celular.

No instala software ni abre un túnel hasta que el usuario ejecuta este archivo.
"""
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parent


def find_cloudflared():
    return shutil.which('cloudflared') or next((str(path) for path in [
        ROOT / 'tools' / 'cloudflared.exe',
        Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'cloudflared' / 'cloudflared.exe',
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Links' / 'cloudflared.exe',
    ] if path.is_file()), None)


def terminate(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    try:
        import qrcode  # noqa: F401
    except ImportError:
        print('Faltan dependencias. Ejecuta: .venv\\Scripts\\python.exe -m pip install -r requirements.txt')
        return 1
    executable = find_cloudflared()
    if not executable:
        print('Falta cloudflared. Instálalo una vez y abre una nueva terminal:')
        print('  winget install --id Cloudflare.cloudflared --exact')
        print('Guía oficial: https://developers.cloudflare.com/tunnel/downloads/')
        return 1
    if '--check' in sys.argv:
        print('Modo celular preparado: generador QR y cloudflared disponibles. No se abrió ningún túnel.')
        return 0
    # Puerto independiente del modo normal, para no reutilizar un servidor sin protección.
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 5050))
        except OSError:
            print('El puerto 5050 está ocupado. Cierra el modo celular anterior y vuelve a iniciar.')
            return 1
    outputs = ROOT / 'outputs'
    outputs.mkdir(exist_ok=True)
    server = tunnel = None
    with tempfile.TemporaryDirectory(prefix='mobile_', dir=outputs) as directory:
        config = Path(directory) / 'connection.json'
        config.write_text('{}', encoding='utf-8')
        key = secrets.token_urlsafe(32)
        env = dict(os.environ, SAFEWALK_HOST='127.0.0.1', SAFEWALK_PORT='5050',
                   SAFEWALK_MOBILE_KEY=key, SAFEWALK_MOBILE_CONFIG=str(config),
                   SAFEWALK_SECRET_KEY=secrets.token_hex(32))
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with (Path(directory) / 'server.log').open('w', encoding='utf-8') as log:
            try:
                print('Iniciando SafeWalk. Mantén esta terminal abierta.', flush=True)
                server = subprocess.Popen([sys.executable, str(ROOT / 'app.py')], cwd=ROOT,
                    env=env, stdout=log, stderr=log, creationflags=flags)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        raise RuntimeError('El servidor no inició. Revisa que las dependencias estén instaladas.')
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:5050/vincular', timeout=1):
                            break
                    except OSError:
                        time.sleep(.5)
                else:
                    raise RuntimeError('El servidor tardó demasiado en iniciar.')
                print('Creando enlace HTTPS temporal mediante Cloudflare…', flush=True)
                tunnel = subprocess.Popen([executable, 'tunnel', '--url', 'http://127.0.0.1:5050', '--no-autoupdate'],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding='utf-8', errors='replace', creationflags=flags)
                lines = queue.Queue(maxsize=200)
                def read_lines():
                    for line in tunnel.stdout:
                        try:
                            lines.put_nowait(line)
                        except queue.Full:
                            pass
                threading.Thread(target=read_lines, daemon=True).start()
                deadline = time.monotonic() + 60
                public_url = None
                while time.monotonic() < deadline and tunnel.poll() is None:
                    try:
                        line = lines.get(timeout=.5)
                    except queue.Empty:
                        continue
                    match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
                    if match:
                        public_url = match.group(0)
                        break
                if not public_url:
                    raise RuntimeError('No se obtuvo el enlace HTTPS. Comprueba Internet y la configuración de cloudflared.')
                pending = config.with_suffix('.tmp')
                pending.write_text(json.dumps({'url': public_url}), encoding='utf-8')
                pending.replace(config)
                print('\n1. Abre este enlace EN LA LAPTOP para ver el panel y el QR:')
                print(f'http://127.0.0.1:5050/vincular?panel=1#{key}')
                print('\n2. Escanea el QR del panel con el celular, o abre este enlace en él:')
                print(f'{public_url}/vincular#{key}')
                print('\n3. En el celular pulsa Conectar y transmitir y permite la cámara.')
                print('Las imágenes pasan por Cloudflare. Comparte el enlace solo con tu equipo.')
                print('Ctrl+C cierra el servidor, el túnel y el acceso de esta sesión.', flush=True)
                while server.poll() is None and tunnel.poll() is None:
                    time.sleep(1)
                raise RuntimeError('El servidor o el túnel se cerró. Ejecuta de nuevo el modo celular.')
            except KeyboardInterrupt:
                print('\nCerrando conexión…')
            except RuntimeError as exc:
                print(f'Error: {exc}')
                return 1
            finally:
                terminate(tunnel)
                terminate(server)
                if tunnel is not None and tunnel.stdout is not None:
                    tunnel.stdout.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
