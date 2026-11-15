"""Session-only physical follower controls; the serial worker applies changes."""
import time
import tkinter as tk
from tkinter import ttk

from robot_control.arm import BODY
from robot_control.tuning import RANGES,LEAD_RANGE,checked_number,RESPONSE_PROFILES,response_profile,response_profile_index


class TuningPanel:
    """One repeatable tradeoff, with individual settings tucked into Advanced."""
    def __init__(self,parent,worker,on_stop):
        self.worker,self.on_stop,self.closed = worker,on_stop,False
        self.advanced = None
        self.refresh_after = None
        self.window = tk.Toplevel(parent)
        self.window.title('Live tuning - Physical SO-101')
        self.window.geometry('720x430')
        self.window.minsize(680,410)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.bind('<Escape>',lambda event:on_stop())
        body = ttk.Frame(self.window,padding=18)
        body.pack(fill='both',expand=True)
        ttk.Label(body,text='HOW SHOULD THE ARM FEEL?',font=('Segoe UI',16,'bold')).pack(anchor='w')
        ttk.Label(body,text='One choice adjusts speed, smoothing and how strongly the arm catches up.\n'
                           'Switch between the same five settings while following.',
                  wraplength=660,padding=(0,10)).pack(anchor='w')
        ends = ttk.Frame(body)
        ends.pack(fill='x',pady=(10,4))
        ttk.Label(ends,text='MORE CONTROL',font=('Segoe UI',10,'bold')).pack(side='left')
        ttk.Label(ends,text='MORE SPEED',font=('Segoe UI',10,'bold')).pack(side='right')
        choices = ttk.Frame(body)
        choices.pack(fill='x')
        self.level = tk.IntVar(value=-1)
        for index,(name,*_) in enumerate(RESPONSE_PROFILES):
            choices.columnconfigure(index,weight=1,uniform='feel')
            ttk.Radiobutton(choices,text=name,value=index,variable=self.level,style='Toolbutton',
                            command=lambda i=index:self.choose(i)).grid(row=0,column=index,sticky='ew',padx=2,ipady=8)
        nudges = ttk.Frame(body)
        nudges.pack(fill='x',pady=8)
        self.slower = ttk.Button(nudges,text='< More control',command=lambda:self.nudge(-1))
        self.slower.pack(side='left')
        self.faster = ttk.Button(nudges,text='More speed >',command=lambda:self.nudge(1))
        self.faster.pack(side='right')
        self.description = tk.StringVar()
        ttk.Label(body,textvariable=self.description,wraplength=660,padding=(0,8)).pack(anchor='w')
        self.status = tk.StringVar()
        ttk.Label(body,textvariable=self.status,wraplength=660).pack(anchor='w')
        self.error = tk.StringVar()
        ttk.Label(body,textvariable=self.error,foreground='#ffcb70',wraplength=660).pack(anchor='w')
        buttons = ttk.Frame(body)
        buttons.pack(fill='x',pady=(12,8))
        ttk.Button(buttons,text='Advanced...',command=self.open_advanced).pack(side='left',padx=(0,8))
        ttk.Button(buttons,text='Reset to startup',command=lambda:self.apply(self.worker.startup_tuning)).pack(side='left')
        ttk.Button(buttons,text='STOP / HOLD [Esc]',command=on_stop).pack(side='right')
        ttk.Label(body,text='Changes last this session. Tuning does not start or resume the arm.\n'
                           f"Alignment is {worker.config['alignment_deg_s']:g} degrees/s. Software load stops are controlled in the web dashboard.",
                  wraplength=660,padding=(0,8)).pack(anchor='w')
        self.refresh()

    def apply(self,values):
        self.error.set('')
        try:
            if self.advanced is not None and not self.advanced.closed:
                # Replace any not-yet-sent edits as well as visible fields.
                self.advanced.set_values(values)
            else: self.worker.tune(values)
        except ValueError as error: self.error.set(str(error))

    def choose(self,index): self.apply(response_profile(index))

    def nudge(self,step):
        state = self.worker.snapshot()
        index = response_profile_index(state['tuning_pending'] or state['tuning'])
        if index is not None: self.choose(max(0,min(len(RESPONSE_PROFILES)-1,index+step)))

    def open_advanced(self):
        if self.advanced is None or self.advanced.closed:
            self.advanced = AdvancedTuningPanel(self.window,self.worker,self.on_stop)
        self.advanced.window.lift()

    def refresh(self):
        if self.closed: return
        state = self.worker.snapshot()
        # Include an unsent advanced slider edit so a preset never appears to
        # describe a custom value waiting in the UI's throttle.
        pending = self.advanced.pending_values() if self.advanced is not None and not self.advanced.closed else None
        values = pending or state['tuning_pending'] or state['tuning']
        index = response_profile_index(values)
        self.level.set(index if index is not None else -1)
        self.description.set(RESPONSE_PROFILES[index][1] if index is not None else
                             'Custom advanced settings. Choose a feel above to return to the five repeatable steps.')
        self.slower.configure(state='normal' if index is not None and index>0 else 'disabled')
        self.faster.configure(state='normal' if index is not None and index<len(RESPONSE_PROFILES)-1 else 'disabled')
        active = response_profile_index(state['tuning'])
        active_name = RESPONSE_PROFILES[active][0] if active is not None else 'Custom (Advanced)'
        mode = 'ALIGNING' if state['aligning'] else 'LIVE' if state['enabled'] else 'OFF / PAUSED'
        waiting = pending is not None or state['tuning_requested']!=state['tuning_applied']
        prefix = 'Applying changes. Currently:' if waiting else 'Applied:'
        self.status.set(f'{prefix} {active_name}. Arm {mode}.')
        self.refresh_after = self.window.after(100,self.refresh)

    def close(self):
        if self.closed: return
        self.closed = True
        if self.refresh_after is not None: self.window.after_cancel(self.refresh_after)
        if self.advanced is not None: self.advanced.close()
        self.window.destroy()


class AdvancedTuningPanel:
    def __init__(self,parent,worker,on_stop):
        self.worker,self.closed = worker,False
        self.loading = True
        self.dirty = {}
        self.boxes = {}
        self.last_local = 0.
        self.send_after = self.refresh_after = None
        self.window = tk.Toplevel(parent)
        self.window.title('Advanced tuning - Physical SO-101')
        self.window.geometry('720x640')
        self.window.minsize(650,610)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.bind('<Escape>',lambda event:on_stop())
        body = ttk.Frame(self.window,padding=18)
        body.pack(fill='both',expand=True)
        ttk.Label(body,text='ADVANCED RESPONSE TUNING',font=('Segoe UI',16,'bold')).pack(anchor='w')
        ttk.Label(body,text='Adjust while following. Changes last this session; closing this window keeps them.',
                  wraplength=670,padding=(0,8)).pack(anchor='w')
        controls = ttk.Frame(body)
        controls.pack(fill='x')
        controls.columnconfigure(1,weight=1)
        self.fields = {}
        rows = [('speed_deg_s','Arm speed (degrees/s)',RANGES['speed_deg_s'],5),
                ('response_ms','Smoothing (ms)',RANGES['response_ms'],5),
                ('control_hz','Control rate (Hz)',RANGES['control_hz'],5)]
        rows += [('lead:'+name,name.replace('_',' ').title()+' lead (degrees)',LEAD_RANGE,.5) for name in BODY]
        state = worker.snapshot()
        values = state['tuning_pending'] or state['tuning']
        for row,(key,label,limits,step) in enumerate(rows):
            value = values['joint_lead_deg'][key[5:]] if key.startswith('lead:') else values[key]
            slider,entry = tk.DoubleVar(value=value),tk.StringVar(value=f'{value:g}')
            self.fields[key] = (slider,entry,limits,step)
            ttk.Label(controls,text=label).grid(row=row,column=0,sticky='w',pady=10,padx=(0,12))
            ttk.Scale(controls,from_=limits[0],to=limits[1],variable=slider,
                      command=lambda raw,k=key:self.slide(k,raw)).grid(row=row,column=1,sticky='ew',padx=(0,12))
            box = ttk.Spinbox(controls,from_=limits[0],to=limits[1],increment=step,width=7,textvariable=entry,
                              command=lambda k=key:self.commit(k))
            box.grid(row=row,column=2)
            self.boxes[key] = box
            box.bind('<Return>',lambda event,k=key:self.commit(k))
            box.bind('<FocusOut>',lambda event,k=key:self.commit(k))
        buttons = ttk.Frame(body)
        buttons.pack(fill='x',pady=(12,8))
        ttk.Button(buttons,text='Close advanced',command=self.close).pack(side='left',padx=(0,8))
        ttk.Button(buttons,text='Reset to startup',command=self.reset).pack(side='left')
        ttk.Button(buttons,text='STOP / HOLD [Esc]',command=on_stop).pack(side='right')
        self.status = tk.StringVar()
        ttk.Label(body,textvariable=self.status,wraplength=670,padding=(0,5)).pack(anchor='w')
        self.error = tk.StringVar()
        ttk.Label(body,textvariable=self.error,foreground='#ffcb70',wraplength=670).pack(anchor='w')
        ttk.Label(body,text='Less smoothing responds sooner but can add jitter. Lead is how far a command may be ahead '
                           'of the measured joint; increase it gradually if LEAD is limiting motion.\n'
                           'Control rate is a target; compare it with the actual Hz in the physical panel.\n'
                           f"Alignment stays at {worker.config['alignment_deg_s']:g} degrees/s. Tuning never enables or resumes movement.",
                  wraplength=670,justify='left',padding=(0,10)).pack(anchor='w')
        self.loading = False
        self.refresh()

    def slide(self,key,raw):
        if self.loading or self.closed: return
        slider,entry,limits,step = self.fields[key]
        value = max(limits[0],min(limits[1],round(float(raw)/step)*step))
        entry.set(f'{value:g}')
        self.queue(key,value)

    def commit(self,key):
        if self.loading or self.closed: return
        slider,entry,limits,_ = self.fields[key]
        try: value = checked_number(float(entry.get()),limits,key.replace('lead:','') )
        except ValueError as error:
            self.error.set(str(error))
            return
        if value!=slider.get():
            slider.set(value)
            self.queue(key,value)
        entry.set(f'{value:g}')

    def queue(self,key,value):
        self.error.set('')
        self.last_local = time.monotonic()
        self.dirty[key] = value
        # Throttle, not debounce: dragging keeps applying the latest value.
        if self.send_after is None: self.send_after = self.window.after(75,self.flush)

    def flush(self):
        self.send_after = None
        if self.closed or not self.dirty: return
        changes = {k:v for k,v in self.dirty.items() if not k.startswith('lead:')}
        leads = {k[5:]:v for k,v in self.dirty.items() if k.startswith('lead:')}
        if leads: changes['joint_lead_deg'] = leads
        self.dirty.clear()
        try: self.worker.tune(changes)
        except ValueError as error: self.error.set(str(error))

    def set_values(self,values):
        self.dirty.clear()
        self.loading = True
        for key,(slider,entry,_,_) in self.fields.items():
            value = values.get('joint_lead_deg',{}).get(key[5:]) if key.startswith('lead:') else values.get(key)
            if value is not None:
                slider.set(value)
                entry.set(f'{value:g}')
                self.dirty[key] = value
        self.loading = False
        self.error.set('')
        if self.send_after is not None:
            self.window.after_cancel(self.send_after)
            self.send_after = None
        self.flush()

    def pending_values(self):
        if not self.dirty: return None
        from robot_control.tuning import apply_values
        state = self.worker.snapshot()
        values = state['tuning_pending'] or state['tuning']
        changes = {k:v for k,v in self.dirty.items() if not k.startswith('lead:')}
        leads = {k[5:]:v for k,v in self.dirty.items() if k.startswith('lead:')}
        if leads: changes['joint_lead_deg'] = leads
        return apply_values(values,changes)

    def reset(self): self.set_values(self.worker.startup_tuning)

    def sync_external(self,state):
        """Show changes made elsewhere (web console, basic panel) without fighting a local edit."""
        if self.dirty or self.send_after is not None or time.monotonic()-self.last_local < .5: return
        current = state['tuning_pending'] or state['tuning']
        try: focused = self.window.focus_get()
        except (KeyError,tk.TclError): focused = None
        self.loading = True
        try:
            for key,(slider,entry,_,_) in self.fields.items():
                value = current['joint_lead_deg'].get(key[5:]) if key.startswith('lead:') else current.get(key)
                if value is None or self.boxes.get(key) is focused or abs(slider.get()-value) < 1e-9: continue
                slider.set(value)
                entry.set(f'{value:g}')
        finally: self.loading = False

    def refresh(self):
        if self.closed: return
        state = self.worker.snapshot()
        self.sync_external(state)
        values = state['tuning']
        pending = bool(self.dirty) or state['tuning_requested']!=state['tuning_applied']
        prefix = 'Queued; waiting for control cycle. Active:' if pending else 'Applied:'
        mode = 'ALIGNING' if state['aligning'] else 'LIVE' if state['enabled'] else 'OFF / PAUSED'
        self.status.set(f"{prefix} {values['speed_deg_s']:g} degrees/s, {values['response_ms']:g} ms, "
                        f"{values['control_hz']:g} Hz target. Arm {mode}.")
        self.refresh_after = self.window.after(100,self.refresh)

    def close(self):
        if self.closed: return
        # Complete a last slider change; this never enables motion.
        if self.send_after is not None: self.window.after_cancel(self.send_after)
        self.flush()
        self.closed = True
        if self.refresh_after is not None: self.window.after_cancel(self.refresh_after)
        self.window.destroy()
