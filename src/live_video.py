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
        self.position = 0.0
        self.frame_index = 0
        self.frame_epoch = 0
        self._epoch = 0
        self._paused = False
        self._seek = None
        self._control = threading.Lock()

    def control(self, paused=None, seek=None):
        with self._control:
            if paused is not None:
                self._paused = paused
            if seek is not None:
                self._seek = max(0.0, seek)
                self._epoch += 1
                while not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        break

    def playback_position(self):
        with self._control:
            return self.position

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
                frame, index, epoch = self.queue.get(timeout=.1)
                with self._control:
                    if epoch != self._epoch:
                        continue
                self.frame_index = index
                self.frame_epoch = epoch
                return True, frame
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
        pause_started = None
        try:
            while not self.closed.is_set() and not self.stopped():
                with self._control:
                    seek = self._seek
                    self._seek = None
                    paused = self._paused
                    if seek is not None:
                        self.capture.set(cv2.CAP_PROP_POS_FRAMES, round(seek * self.fps))
                        index = int(self.capture.get(cv2.CAP_PROP_POS_FRAMES))
                        self.position = index / self.fps
                        while not self.queue.empty():
                            try:
                                self.queue.get_nowait()
                            except queue.Empty:
                                break
                        started = time.perf_counter() - index / self.fps
                        last_publish = index / self.fps - 1 / output_fps
                        pause_started = time.perf_counter() if paused else None
                    epoch = self._epoch
                if paused and seek is None:
                    if pause_started is None:
                        pause_started = time.perf_counter()
                    self.closed.wait(.05)
                    continue
                if not paused and pause_started is not None:
                    started += time.perf_counter() - pause_started
                    pause_started = None
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
                longest = max(frame.shape[:2])
                if longest > 960:
                    scale = 960 / longest
                    frame = cv2.resize(frame, (round(frame.shape[1] * scale),
                                               round(frame.shape[0] * scale)))
                with self._control:
                    if epoch != self._epoch or self._seek is not None:
                        continue
                    epoch = self._epoch
                    self.position = index / self.fps
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    pass
                self.queue.put_nowait((frame, index, epoch))
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
