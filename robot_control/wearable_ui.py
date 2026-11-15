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
        self.window.geometry("800x590")
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
        self.replay_button = ttk.Button(buttons,text="Replay last motion",command=lambda:worker.request("replay"))
        self.replay_button.pack(side="left",padx=(0,6))
        ttk.Button(buttons,text="STOP / HOLD  [Esc]",command=on_stop).pack(side="left")
        self.status = tk.StringVar(value="Connecting…")
        ttk.Label(body,textvariable=self.status,wraplength=750,foreground="#ffcb70",padding=(0,10)).pack(fill="x")
        self.state = tk.StringVar()
        ttk.Label(body,textvariable=self.state).pack(anchor="w")
        self.motion = tk.StringVar()
        ttk.Label(body,textvariable=self.motion,foreground="#4de1bd").pack(anchor="w")
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
                           "Record: tap the stick down to start, tap again to stop. Holding it down for 0.8 s stops arm and claw.\n"
                           "Replay moves the arm to where the recording began, then repeats it: clear that whole path first.\n"
                           "Stops hold position with torque on. Resets require manual re-enable.\n"
                           "Keep the power switch reachable: USB loss prevents a new hold command.",
                  wraplength=750,justify="left").pack(anchor="w")
        self.refresh()

    @staticmethod
    def motion_text(state):
        if state["recording"] is not None:
            return f"● RECORDING {state['recording']:.1f} s · tap the stick again to stop"
        if state["replay"] is not None:
            phase,elapsed,total = state["replay"]
            if phase=="approach": return f"▶ REPLAY · moving to the start pose of the {total:.1f} s motion · stick press or Esc stops"
            return f"▶ REPLAY {elapsed:.1f} / {total:.1f} s · stick press or Esc stops"
        if state["motion"] is not None:
            return f"Last motion {state['motion']:.1f} s · Replay last motion plays it · tap the stick to record a new one"
        return "No motion recorded yet · tap the stick down to start recording, tap again to stop"

    def refresh(self):
        if self.closed: return
        state = self.worker.snapshot()
        fresh = state["seen"] is not None and time.monotonic()-state["seen"]<.5
        self.status.set(state["message"])
        # A replay drives powered joints with live control off: "OFF" would read as "not moving".
        mode = lambda on:"REPLAY" if state["replay"] is not None else "ENABLED" if on else "OFF"
        self.state.set(f"Arm {mode(state['enabled'])} · Claw {mode(state['claw_enabled'])} · "
                       f"Telemetry {'LIVE' if fresh else 'waiting'} · {self.worker.config['speed_deg_s']:g}°/s target rate")
        self.motion.set(self.motion_text(state))
        live = fresh and state["connected"]
        ready = live and state["can_enable"] and state["replay"] is None
        self.arm_button.configure(state="normal" if ready and not state["enabled"] else "disabled")
        self.claw_button.configure(state="normal" if ready and not state["claw_enabled"] else "disabled")
        # A replay needs the robot, not the wearable: it follows the recording, not your arm.
        self.replay_button.configure(state="normal" if live and state["motion"] is not None
                                     and state["recording"] is None and state["replay"] is None else "disabled")
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
