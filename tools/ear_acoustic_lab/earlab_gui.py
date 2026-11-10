#!/usr/bin/env python3
"""One-button Tk desktop frontend for a Mesh2HRTF ear-canal frequency sweep."""
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from run_analysis import PRESETS, REPO_ROOT

ROOT=Path(__file__).resolve().parent

class App:
    def __init__(self, master):
        self.root=master
        master.title('Ear Acoustic Lab — Mesh2HRTF sweep')
        master.geometry('950x730')
        master.minsize(750,600)
        self.running=False
        self.messages=queue.Queue()
        self.results=''
        outer=ttk.Frame(master,padding=14);outer.pack(fill='both',expand=True)
        outer.columnconfigure(1,weight=1)
        r=0
        self.repo=tk.StringVar(value=str(REPO_ROOT))
        self.stl=self.path(outer,r,'Ear STL model','~/model.stl');r+=1
        self.unit=tk.StringVar(value='mm')
        ttk.Label(outer,text='STL coordinates').grid(row=r,column=0,sticky='w',pady=3)
        ttk.Combobox(outer,textvariable=self.unit,values=['mm','m'],state='readonly',width=10).grid(row=r,column=1,sticky='w');r+=1
        self.mic=self.field(outer,r,'Mic XYZ (default: STL origin)','0 0 0');r+=1
        self.mode=tk.StringVar(value='Near 12 cm')
        ttk.Label(outer,text='Sound source').grid(row=r,column=0,sticky='w',pady=3)
        ttk.Combobox(outer,textvariable=self.mode,values=['Near 12 cm','Far 1 m','Custom XYZ'],state='readonly').grid(row=r,column=1,sticky='w');r+=1
        self.axis=tk.StringVar(value='+Y')
        ttk.Label(outer,text='From microphone toward sound').grid(row=r,column=0,sticky='w',pady=3)
        ttk.Combobox(outer,textvariable=self.axis,values=['+Y','-Y','-X','+X','-Z','+Z'],state='readonly',width=12).grid(row=r,column=1,sticky='w');r+=1
        self.source=self.field(outer,r,'Custom source XYZ (if selected)','0 120 0');r+=1
        self.preset=tk.StringVar(value='Quick ~1kHz')
        ttk.Label(outer,text='Frequency test battery').grid(row=r,column=0,sticky='w',pady=3)
        ttk.Combobox(outer,textvariable=self.preset,values=list(PRESETS),state='readonly',width=25).grid(row=r,column=1,sticky='w');r+=1
        self.music=self.path(outer,r,'Optional WAV / MP3 music','');r+=1
        self.results_root=self.path(outer,r,'Output folder','~/EarAcousticLab/results',folder=True);r+=1
        self.jobs=self.field(outer,r,'Parallel CPU processes','4');r+=1
        note=('Blender exporter runs automatically — no add-on installation. '
              'Export the STL with microphone position at (0,0,0), or enter XYZ here. '
              'A real open canal/tube must exist in the STL surface. '
              '1 m source distance costs approximately the same as 12 cm in BEM. '
              'The simulator uses CPU NumCalc; your RTX 3090 is not used.')
        ttk.Label(outer,text=note,foreground='#555555',wraplength=790).grid(row=r,column=0,columnspan=3,sticky='w',pady=8);r+=1
        buttons=ttk.Frame(outer);buttons.grid(row=r,column=0,columnspan=3,sticky='ew',pady=9)
        self.run_button=ttk.Button(buttons,text='▶ RUN ACOUSTIC SWEEP',command=self.start)
        self.run_button.pack(side='left',padx=(0,14))
        ttk.Button(buttons,text='Preview STL + mic',command=self.preview).pack(side='left',padx=5)
        ttk.Button(buttons,text='Open report',command=self.open_graphs).pack(side='left',padx=5)
        ttk.Button(buttons,text='Reanalyze saved',command=self.reanalyze_last).pack(side='left',padx=5)
        ttk.Button(buttons,text='Play music audition',command=self.open_music).pack(side='left',padx=5)
        ttk.Button(buttons,text='Results folder',command=self.open_folder).pack(side='left',padx=5)
        r+=1
        self.status=tk.StringVar(value='Ready: ~/model.stl, microphone (0,0,0). Click RUN ACOUSTIC SWEEP.')
        ttk.Label(outer,textvariable=self.status).grid(row=r,column=0,columnspan=3,sticky='w',pady=4);r+=1
        self.log=tk.Text(outer,height=15,wrap='word')
        self.log.grid(row=r,column=0,columnspan=3,sticky='nsew')
        outer.rowconfigure(r,weight=1)
        master.after(100,self.poll)

    def field(self,parent,row,label,default):
        v=tk.StringVar(value=default)
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',pady=3)
        ttk.Entry(parent,textvariable=v).grid(row=row,column=1,sticky='ew',pady=3,padx=4)
        return v

    def path(self,parent,row,label,default,folder=False):
        v=self.field(parent,row,label,default)
        def browse():
            x=filedialog.askdirectory(initialdir=os.path.expanduser(v.get() or '~')) if folder else filedialog.askopenfilename(initialdir=str(Path('~').expanduser()))
            if x:v.set(x)
        ttk.Button(parent,text='Browse',command=browse).grid(row=row,column=2,sticky='e',padx=(5,0))
        return v

    def start(self):
        if self.running:return
        try:
            mic=[float(i) for i in self.mic.get().replace(',',' ').split()]
            source=[float(i) for i in self.source.get().replace(',',' ').split()]
            if len(mic)!=3 or len(source)!=3:raise ValueError('XYZ must contain three numbers')
            jobs=int(self.jobs.get())
            if jobs<1 or jobs>32:raise ValueError('CPU processes must be 1..32')
        except ValueError as e:
            messagebox.showerror('Check coordinates',str(e));return
        cmd=[sys.executable,'-u',str(ROOT/'run_analysis.py'),
             '--mesh2hrtf',self.repo.get(),'--stl',self.stl.get(),
             '--units',self.unit.get(),'--mic',*map(str,mic),
             '--source-mode',self.mode.get(),'--axis',self.axis.get(),
             '--source',*map(str,source),'--preset',self.preset.get(),
             '--results',self.results_root.get(),'--jobs',str(jobs)]
        if self.music.get():cmd+=['--music',self.music.get()]
        self.log.delete('1.0','end');self.results=''
        self.running=True;self.run_button.configure(state='disabled')
        self.status.set('Running actual Mesh2HRTF numerical simulation...')
        threading.Thread(target=self.worker,args=(cmd,),daemon=True).start()

    def reanalyze_last(self):
        """Generate more plots and spatial maps without re-running NumCalc."""
        if self.running:
            return
        root = Path(self.results_root.get()).expanduser().resolve()
        candidates = [p for p in root.glob('ear_*')
                      if (p/'Output2HRTF'/'HRTF_MicProbe.sofa').is_file()]
        if not candidates:
            messagebox.showinfo(
                'No completed simulation',
                'No completed NumCalc run found in the results folder. Run a sweep first.')
            return
        project = max(candidates, key=lambda p: p.stat().st_mtime)
        self.log.delete('1.0', 'end')
        self.log.insert('end', '[REANALYZE] '+str(project)+'\n')
        self.results = ''
        self.running = True
        self.run_button.configure(state='disabled')
        self.status.set('Reading completed NumCalc data and generating diagnostic plots...')
        cmd = [sys.executable, '-u', str(ROOT/'reanalyze.py'), str(project)]
        threading.Thread(target=self.worker, args=(cmd,), daemon=True).start()

    def worker(self,cmd):
        try:
            p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                               text=True,bufsize=1,encoding='utf-8',errors='replace')
            for line in p.stdout:self.messages.put(('line',line))
            code=p.wait()
            self.messages.put(('done',code))
        except Exception as e:self.messages.put(('error',str(e)))

    def poll(self):
        try:
            while True:
                kind,value=self.messages.get_nowait()
                if kind=='line':
                    self.log.insert('end',value);self.log.see('end')
                    if value.startswith('RESULT_DIR='):
                        self.results=value.strip().split('=',1)[1]
                elif kind=='done':
                    self.running=False;self.run_button.configure(state='normal')
                    self.status.set('Completed: graphs ready.' if value==0 else 'Failed. Check log for exact error.')
                elif kind=='error':
                    self.running=False;self.run_button.configure(state='normal')
                    self.status.set('Failed: '+value)
        except queue.Empty:pass
        self.root.after(100,self.poll)

    def preview(self):
        """Non-destructive, rotatable STL preview (not a mesh editor)."""
        try:
            import numpy as np
            import trimesh
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from mpl_toolkits.mplot3d.art3d import Poly3DCollection
            mesh=trimesh.load_mesh(str(Path(self.stl.get()).expanduser()),process=False)
            if isinstance(mesh,trimesh.Scene):mesh=mesh.dump(concatenate=True)
            mic=np.array([float(x) for x in self.mic.get().replace(',',' ').split()])
            if mic.size!=3:raise ValueError('Mic XYZ must contain 3 numbers')
            if self.mode.get()=='Custom XYZ':
                source=np.array([float(x) for x in self.source.get().replace(',',' ').split()])
            else:
                distance=(120 if self.mode.get()=='Near 12 cm' else 1000)
                if self.unit.get()=='m':distance/=1000
                direction={'-Y':(0,-1,0),'+Y':(0,1,0),'-X':(-1,0,0),
                           '+X':(1,0,0),'-Z':(0,0,-1),'+Z':(0,0,1)}[self.axis.get()]
                source=mic+np.array(direction)*distance
            tris=np.asarray(mesh.triangles)
            if len(tris)>5500:tris=tris[np.linspace(0,len(tris)-1,5500).astype(int)]
            win=tk.Toplevel(self.root);win.title('STL geometry and probe coordinates')
            win.geometry('790x650')
            fig=Figure(figsize=(9,7),dpi=100)
            ax=fig.add_subplot(111,projection='3d')
            collection=Poly3DCollection(tris,alpha=.09,linewidths=.2,edgecolors='#62889d',facecolors='#9bc7da')
            ax.add_collection3d(collection)
            ax.scatter(*mic,s=100,c='red',label='Microphone')
            ax.scatter(*source,s=70,c='orange',label='Sound source')
            bounds=np.asarray(mesh.bounds);centre=np.mean(bounds,axis=0)
            span=max(float(np.max(bounds[1]-bounds[0])),.001)
            for i,func in enumerate((ax.set_xlim,ax.set_ylim,ax.set_zlim)):
                func(centre[i]-span*.6,centre[i]+span*.6)
            ax.set_xlabel('X ('+self.unit.get()+')');ax.set_ylabel('Y');ax.set_zlabel('Z')
            ax.legend(loc='upper left');ax.set_box_aspect((1,1,1))
            canvas=FigureCanvasTkAgg(fig,master=win);canvas.draw();canvas.get_tk_widget().pack(fill='both',expand=True)
            ttk.Label(win,text='Rotate with mouse. Change XYZ in main window then reopen preview. Red = mic, orange = sound source.').pack(pady=5)
        except Exception as e:
            messagebox.showerror('STL preview failed',str(e))

    def open_file(self,p):
        if not Path(p).is_file() and not Path(p).is_dir():
            messagebox.showinfo('No result','Run a simulation first, or check the log.');return
        if sys.platform=='darwin':cmd=['open',str(p)]
        elif os.name=='nt':
            os.startfile(str(p));return
        else:
            if str(p).endswith('.wav') and shutil.which('ffplay'):
                cmd=['ffplay','-nodisp','-autoexit','-loglevel','error',str(p)]
            else:cmd=['xdg-open',str(p)]
        subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def open_graphs(self):
        if not self.results:
            messagebox.showinfo('No result', 'Run a sweep or reanalyze a saved run first.')
            return
        folder = Path(self.results)
        self.open_file(folder/'report.html' if (folder/'report.html').is_file()
                       else folder/'response.png')
    def open_music(self):self.open_file(Path(self.results)/'music_audition.wav')
    def open_folder(self):self.open_file(Path(self.results) if self.results else Path(self.results_root.get()).expanduser())


if __name__=='__main__':
    root=tk.Tk()
    try:ttk.Style().theme_use('clam')
    except tk.TclError:pass
    App(root);root.mainloop()