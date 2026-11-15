# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial>=3.5"]
# ///
"""Run with: uv run viewer.py"""
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import serial
from serial.tools import list_ports


def multiply(a, b):
    w, x, y, z = a
    v, i, j, k = b
    return (w*v-x*i-y*j-z*k, w*i+x*v+y*k-z*j,
            w*j-x*k+y*v+z*i, w*k+x*j-y*i+z*v)


def rotate(q, p):
    result = multiply(multiply(q, (0, *p)), (q[0], -q[1], -q[2], -q[3]))
    return result[1:]


class Viewer:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BNO085 • Live 3D orientation")
        self.root.geometry("1000x740")
        self.q = (1, 0, 0, 0)
        self.reference = self.q
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.last_sample = 0
        self.demo = tk.BooleanVar(value=False)
        controls = ttk.Frame(self.root, padding=12)
        controls.pack(fill="x")
        self.port = ttk.Combobox(controls, width=16, state="readonly")
        self.port.pack(side="left", padx=5)
        ttk.Button(controls, text="Refresh ports", command=self.refresh).pack(side="left")
        self.button = ttk.Button(controls, text="Connect", command=self.toggle)
        self.button.pack(side="left", padx=8)
        ttk.Button(controls, text="Zero orientation", command=self.zero).pack(side="left")
        ttk.Checkbutton(controls, text="Demo", variable=self.demo).pack(side="left", padx=16)
        self.status = tk.StringVar(value="Choose the ESP32 port, then Connect. Close Arduino Serial Monitor first.")
        ttk.Label(self.root, textvariable=self.status, padding=8).pack(fill="x")
        self.canvas = tk.Canvas(self.root, bg="#101827", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        ttk.Label(self.root, text="Red = X   Green = Y   Blue = Z  |  Zero sets the current pose as neutral.  |  Orientation only; movement does not change position.", padding=10).pack()
        self.refresh()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.tick()

    def refresh(self):
        self.ports = {p.device: p.description for p in list_ports.comports()}
        self.port["values"] = list(self.ports)
        if self.ports and self.port.get() not in self.ports:
            self.port.current(0)

    def zero(self):
        self.reference = (self.q[0], -self.q[1], -self.q[2], -self.q[3])

    def toggle(self):
        if self.worker and self.worker.is_alive():
            self.stop.set()
            self.button.config(state="disabled")
            return
        if not self.port.get():
            self.status.set("No serial port found. Plug in your ESP32 and refresh.")
            return
        self.demo.set(False)
        self.stop = threading.Event()
        self.last_sample = 0
        self.status.set(f"Opening {self.port.get()} at 115200 baud…")
        self.button.config(text="Disconnect")
        self.worker = threading.Thread(target=self.read, args=(self.port.get(), self.stop), daemon=True)
        self.worker.start()

    def read(self, port, stop):
        try:
            with serial.Serial(port, 115200, timeout=0.2) as connection:
                while not stop.is_set():
                    line = connection.readline().decode("ascii", errors="ignore").strip()
                    try:
                        q = tuple(float(x) for x in line.split(","))
                        if len(q) != 4 or not all(math.isfinite(x) for x in q):
                            continue
                        norm = math.sqrt(sum(x*x for x in q))
                        if norm < 1e-6:
                            continue
                        self.events.put(("sample", tuple(x/norm for x in q)))
                    except ValueError:
                        continue
        except (serial.SerialException, OSError) as error:
            self.events.put(("error", f"{error} — close other serial monitors and reconnect."))
        finally:
            self.events.put(("closed", None))

    def project(self, p):
        # Fixed camera, with world Z up and a perspective projection.
        x, y, z = p
        horizontal = .80*x - .60*y
        depth = .45*x + .60*y - .66*z
        vertical = .396*x + .528*y + .75*z
        scale = min(self.canvas.winfo_width(), self.canvas.winfo_height()) * .70 / (5 + depth)
        return (self.canvas.winfo_width()/2 + horizontal*scale,
                self.canvas.winfo_height()/2 - vertical*scale, depth)

    def draw(self, q):
        c = self.canvas
        c.delete("all")
        for n in range(-4, 5):
            for a, b in [((n, -4, -1), (n, 4, -1)), ((-4, n, -1), (4, n, -1))]:
                c.create_line(*self.project(a)[:2], *self.project(b)[:2], fill="#243247")
        corners = [(x, y, z) for x in (-1.4, 1.4) for y in (-.8, .8) for z in (-.13, .13)]
        points = [self.project(rotate(q, p)) for p in corners]
        faces = [((0, 1, 3, 2), "#21516d"), ((4, 6, 7, 5), "#2589a8"),
                 ((0, 4, 5, 1), "#156276"), ((2, 3, 7, 6), "#287d8c"),
                 ((0, 2, 6, 4), "#126348"), ((1, 5, 7, 3), "#35bd8a")]
        for indices, color in sorted(faces, key=lambda f: sum(points[i][2] for i in f[0]), reverse=True):
            c.create_polygon(*[v for i in indices for v in points[i][:2]], fill=color, outline="#9ee5d1", width=2)
        origin = self.project((0, 0, 0))[:2]
        for axis, color, label in [((2, 0, 0), "#ff6b72", "X"), ((0, 2, 0), "#69e89e", "Y"), ((0, 0, 2), "#71b5ff", "Z")]:
            endpoint = self.project(rotate(q, axis))[:2]
            c.create_line(*origin, *endpoint, fill=color, width=4, arrow="last")
            c.create_text(endpoint[0]+12, endpoint[1]-12, text=label, fill=color, font=("Segoe UI", 14, "bold"))
        compact = c.winfo_width() < 600
        heading = getattr(self, "heading", "LIVE ORIENTATION" if not self.demo.get() else "DEMO • synthetic motion")
        c.create_text(24, 24, anchor="nw", text=heading, fill="#d7e5fa", font=("Segoe UI", 14 if compact else 18, "bold"))
        values = ", ".join(f"{v:+.2f}" if compact else f"{v:+.3f}" for v in self.q)
        c.create_text(24, 58, anchor="nw", text="w,x,y,z: " + values, fill="#a8bad4", font=("Consolas", 10 if compact else 12))

    def tick(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "sample":
                self.q = value
                self.last_sample = time.monotonic()
                self.status.set(f"Receiving orientation from {self.port.get()} • 115200 baud")
            elif kind == "error":
                self.status.set(value)
            elif kind == "closed":
                self.button.config(text="Connect", state="normal")
                if self.stop.is_set():
                    self.status.set("Disconnected")
        if self.demo.get():
            t = time.monotonic() * .5
            self.q = multiply((math.cos(t/2), 0, 0, math.sin(t/2)),
                              (math.cos(.35*math.sin(t)), math.sin(.35*math.sin(t)), 0, 0))
        elif self.last_sample and time.monotonic() - self.last_sample > 2:
            self.status.set("No recent sensor data. Check wiring, sketch, and serial connection.")
        self.draw(multiply(self.reference, self.q))
        self.root.after(16, self.tick)

    def close(self):
        self.stop.set()
        self.root.destroy()


if __name__ == "__main__":
    Viewer().root.mainloop()
