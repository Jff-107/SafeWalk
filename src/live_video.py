"""Reproducción a ritmo de origen, independiente de la inferencia."""
import queue
import threading
import time

import cv2


class LiveVideo:
    def __init__(self, path, publish, stopped):
        self.capture = cv2.VideoCapture(path)
        self.fps = self.capture.get(cv2.CAP_PROP_FPS)
        if not 1 <= self.fps <= 240:
            self.fps = 30
        self.queue = queue.Queue(maxsize=1)
        self.done = threading.Event()
        self.closed = threading.Event()
        self.publish = publish
        self.stopped = stopped
        self.error = None
        self.thread = None
        self.display_fps = 0
        self.published = 0

    def isOpened(self):
        return self.capture.isOpened()

    def get(self, prop):
        return self.capture.get(prop)

    def read(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._play, daemon=True)
            self.thread.start()
        while not self.closed.is_set() and not self.stopped():
            try:
                return True, self.queue.get(timeout=.1)
            except queue.Empty:
                if self.done.is_set():
                    if self.error:
                        raise self.error
                    return False, None
        return False, None

    def _play(self):
        started = time.perf_counter()
        index = 0
        output_fps = min(self.fps, 30)
        last_publish = -1 / output_fps
        last_wall = None
        try:
            while not self.closed.is_set() and not self.stopped():
                ok, frame = self.capture.read()
                if not ok:
                    break
                target = index / self.fps
                index += 1
                delay = started + target - time.perf_counter()
                if self.closed.wait(max(0, delay)):
                    break
                # Si la codificación se atrasa, descarta imágenes; no acumula demora.
                if time.perf_counter() - started - target > max(.1, 2 / output_fps):
                    continue
                if target - last_publish + 1e-6 < 1 / output_fps:
                    continue
                last_publish = target
                if frame.shape[1] > 960:
                    frame = cv2.resize(frame, (960, round(frame.shape[0] * 960 / frame.shape[1])))
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    pass
                self.queue.put_nowait(frame)
                self.publish(frame)
                now = time.perf_counter()
                if last_wall is not None:
                    instantaneous = 1 / max(now - last_wall, .001)
                    self.display_fps = instantaneous if not self.display_fps else .85 * self.display_fps + .15 * instantaneous
                last_wall = now
                self.published += 1
        except Exception as exc:
            self.error = exc
        finally:
            self.capture.release()
            self.done.set()

    def release(self):
        self.closed.set()
        if self.thread is not None:
            self.thread.join()
        else:
            self.capture.release()
