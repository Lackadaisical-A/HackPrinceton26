"""Latest-frame renderer transport. Tk never sends or receives on a process pipe."""
from collections import deque
import multiprocessing
import threading
import time


class RenderClient:
    def __init__(self, worker, timeout=3., startup_timeout=15., max_restarts=3):
        self.worker,self.timeout,self.startup_timeout,self.max_restarts = worker,timeout,startup_timeout,max_restarts
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.restart = threading.Event()
        self.latest = None
        self.version = 0
        self.frame = None
        self.frame_number = 0
        self.frame_times = deque(maxlen=30)
        self.state,self.error,self.restarts = "starting","",0
        self.thread = threading.Thread(target=self.run,daemon=True,name="URDF renderer transport")
        self.thread.start()

    def submit(self, request):
        with self.lock:
            self.latest = request
            self.version += 1

    def snapshot(self):
        with self.lock:
            times = self.frame_times
            fps = (len(times)-1)/(times[-1]-times[0]) if len(times)>1 and times[-1]>times[0] else 0.
            return dict(frame=self.frame,number=self.frame_number,state=self.state,error=self.error,
                        restarts=self.restarts,fps=fps,seen=times[-1] if times else None)

    def note(self, state, error=""):
        with self.lock: self.state,self.error = state,error

    def receive(self, connection, process, seconds):
        deadline = time.monotonic()+seconds
        while not self.stop.is_set() and not self.restart.is_set():
            if connection.poll(.02): return connection.recv()
            if not process.is_alive(): raise RuntimeError("render process exited")
            if time.monotonic() >= deadline: raise TimeoutError("renderer stalled")
        raise InterruptedError()

    def run(self):
        failures = 0
        while not self.stop.is_set():
            if failures > self.max_restarts:
                self.note("failed","Repeated renderer failure; click Restart renderer")
                while not self.stop.wait(.05) and not self.restart.is_set(): pass
                failures = 0
            if self.stop.is_set(): break
            self.restart.clear()
            process = connection = child = None
            try:
                self.note("starting")
                context = multiprocessing.get_context("spawn")
                connection,child = context.Pipe()
                process = context.Process(target=self.worker,args=(child,),daemon=True,name="SO101 preview renderer")
                process.start()
                child.close()
                child = None
                ready = self.receive(connection,process,self.startup_timeout)
                if ready[0] != "ready": raise RuntimeError(str(ready[1:]))
                self.note("ready")
                sent_version = -1
                while not self.stop.is_set() and not self.restart.is_set():
                    with self.lock:
                        request,version = self.latest,self.version
                    if request is None or version == sent_version:
                        self.stop.wait(.005)
                        continue
                    # Only this I/O thread may block. Intermediate poses are
                    # overwritten in latest while a frame is being rendered.
                    connection.send(request)
                    result = self.receive(connection,process,self.timeout)
                    if result[0] != "frame": raise RuntimeError(str(result[1:]))
                    with self.lock:
                        self.frame = result
                        self.frame_number += 1
                        self.frame_times.append(time.monotonic())
                        self.state,self.error = "ready",""
                    failures = 0
                    sent_version = version
            except InterruptedError:
                pass
            except (OSError,EOFError,RuntimeError,TimeoutError) as error:
                failures += 1
                with self.lock:
                    self.restarts += 1
                    self.state,self.error = "restarting",str(error) or "render connection closed"
            finally:
                if child is not None: child.close()
                if connection is not None: connection.close()
                if process is not None:
                    if process.is_alive(): process.terminate()
                    process.join(timeout=1)
            if not self.stop.is_set(): self.stop.wait(.15)
        self.note("closed")

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1.5)
