"""Operator controls; every servo operation runs in WearableRobot's worker."""
import tkinter as tk
from tkinter import ttk
import time

from robot_control.arm import JOINTS


class PhysicalPanel:
    def __init__(self,parent,worker,on_stop):
        self.worker,self.closed = worker,False
        self.window = tk.Toplevel(parent)
        self.window.title(f"Physical SO-101 — {worker.port}")
        self.window.geometry("800x500")
        self.window.configure(bg="#0b1220")
        self.window.protocol("WM_DELETE_WINDOW",self.close)
        self.window.bind("<Escape>",lambda event:on_stop())
        body = ttk.Frame(self.window,padding=18)
        body.pack(fill="both",expand=True)
        ttk.Label(body,text=f"PHYSICAL SO-101 / {worker.port}",font=("Segoe UI",16,"bold")).pack(anchor="w")
        ttk.Label(body,text="Connection is read-only until you enable control.",padding=(0,8)).pack(anchor="w")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x",pady=5)
        self.arm_button = ttk.Button(buttons,text="Enable arm · anchor here",command=lambda:worker.request("enable_arm"))
        self.arm_button.pack(side="left",padx=(0,6))
        self.claw_button = ttk.Button(buttons,text="Enable claw",command=lambda:worker.request("enable_claw"))
        self.claw_button.pack(side="left",padx=(0,6))
        ttk.Button(buttons,text="STOP / HOLD  [Esc]",command=on_stop).pack(side="left")
        self.status = tk.StringVar(value="Connecting…")
        ttk.Label(body,textvariable=self.status,wraplength=750,foreground="#ffcb70",padding=(0,10)).pack(fill="x")
        self.state = tk.StringVar()
        ttk.Label(body,textvariable=self.state).pack(anchor="w")
        self.table = ttk.Treeview(body,columns=("position","target","load","torque"),show="tree headings",height=6)
        self.table.heading("#0",text="Joint")
        self.table.column("#0",width=180)
        for key,label in (("position","Position (raw)"),("target","Target (raw)"),("load","Load (counts)"),("torque","Torque")):
            self.table.heading(key,text=label)
            self.table.column(key,width=120,anchor="center")
        for name in JOINTS: self.table.insert("","end",iid=name,text=name.replace("_"," "))
        self.table.pack(fill="x",pady=10)
        ttk.Label(body,text="1. Recenter/calibrate in Wearable Lab. Verify the URDF follows you.\n"
                           "2. Clear the arm's workspace; hold your arm still, then Enable arm.\n"
                           "3. Make one small movement first. Enable claw separately with the stick centered.\n"
                           "Stops hold position with torque on. Resets require manual re-enable.\n"
                           "Keep the power switch reachable: USB loss prevents a new hold command.",
                  wraplength=750,justify="left").pack(anchor="w")
        self.refresh()

    def refresh(self):
        if self.closed: return
        state = self.worker.snapshot()
        fresh = state["seen"] is not None and time.monotonic()-state["seen"]<.5
        self.status.set(state["message"])
        self.state.set(f"Arm {'ENABLED' if state['enabled'] else 'OFF'} · Claw {'ENABLED' if state['claw_enabled'] else 'OFF'} · "
                       f"Telemetry {'LIVE' if fresh else 'waiting'} · {self.worker.config['speed_deg_s']:g}°/s target rate")
        ready = fresh and state["connected"] and state["can_enable"]
        self.arm_button.configure(state="normal" if ready and not state["enabled"] else "disabled")
        self.claw_button.configure(state="normal" if ready and not state["claw_enabled"] else "disabled")
        for name in JOINTS:
            self.table.item(name,values=(state["positions"].get(name,"—"),
                            round(state["targets"][name]) if name in state["targets"] else "—",
                            state["loads"].get(name,"—"),"ON" if state["torque"].get(name) else "off"))
        self.window.after(100,self.refresh)

    def close(self):
        if self.closed: return
        self.closed = True
        self.worker.pause("physical controls closed")
        self.window.destroy()
