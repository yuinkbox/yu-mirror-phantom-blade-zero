"""Small local Tk controls with a neutral, rounded visual language."""
import tkinter as tk

BG='#09090b'
SURFACE='#101013'
INPUT='#18181b'
BORDER='#27272a'
TEXT='#fafafa'
MUTED='#a1a1aa'
DIM='#71717a'
GREEN='#4ade80'
AMBER='#fbbf24'


def font(size=14,bold=False):
    return ('Microsoft YaHei UI',-size,'bold' if bold else 'normal')


def label(parent,text='',size=14,color=TEXT,bold=False,**kw):
    return tk.Label(parent,text=text,bg=parent.cget('bg'),fg=color,font=font(size,bold),bd=0,**kw)


def rounded(canvas,x,y,w,h,r=9,**kw):
    r=min(r,w/2,h/2)
    return canvas.create_polygon(x+r,y,x+w-r,y,x+w,y,x+w,y+r,x+w,y+h-r,
        x+w,y+h,x+w-r,y+h,x+r,y+h,x,y+h,x,y+h-r,x,y+r,x,y,
        smooth=True,splinesteps=24,**kw)


class Panel(tk.Canvas):
    def __init__(self,parent,padding=18,fill=SURFACE,**kw):
        super().__init__(parent,bg=parent.cget('bg'),highlightthickness=0,bd=0,**kw)
        self.padding=padding;self.fill=fill
        self.body=tk.Frame(self,bg=fill)
        self.window=self.create_window(padding,padding,window=self.body,anchor='nw')
        self.body.bind('<Configure>',self._size)
        self.bind('<Configure>',self._layout)

    def _size(self,_=None):
        desired=self.body.winfo_reqheight()+2*self.padding
        if self.winfo_reqheight()!=desired:self.configure(height=desired)

    def _layout(self,event):
        self.delete('surface')
        rounded(self,1,1,event.width-2,event.height-2,11,fill=self.fill,outline=BORDER,tags='surface')
        self.tag_lower('surface')
        self.itemconfigure(self.window,width=max(1,event.width-2*self.padding))


class Button(tk.Canvas):
    def __init__(self,parent,text,command,width=120,primary=False,height=38,shortcut='',**kw):
        super().__init__(parent,width=width,height=height,bg=parent.cget('bg'),highlightthickness=0,
                         bd=0,takefocus=True,cursor='hand2',**kw)
        self.text=text;self.command=command;self.primary=primary;self.shortcut=shortcut
        self.hover=False;self.focused=False;self.enabled=True
        self.bind('<Configure>',lambda _:self.draw())
        self.bind('<Enter>',lambda _:self._hover(True));self.bind('<Leave>',lambda _:self._hover(False))
        self.bind('<Button-1>',lambda _:self.invoke())
        self.bind('<space>',lambda _:self.invoke());self.bind('<Return>',lambda _:self.invoke())
        self.bind('<FocusIn>',lambda _:self._focus(True));self.bind('<FocusOut>',lambda _:self._focus(False))

    def _hover(self,value):self.hover=value;self.draw()
    def _focus(self,value):self.focused=value;self.draw()
    def invoke(self):
        if self.enabled:self.command()

    def set_enabled(self,enabled):
        self.enabled=bool(enabled)
        self.configure(cursor='hand2' if self.enabled else 'arrow',takefocus=self.enabled)
        self.draw()

    def draw(self):
        self.delete('all');w=max(self.winfo_width(),int(self.cget('width')));h=int(self.cget('height'))
        fill=('#e4e4e7' if self.hover else '#fafafa') if self.primary else ('#202024' if self.hover else INPUT)
        fg=BG if self.primary else TEXT
        if not self.enabled:fill,fg=BORDER,DIM
        rounded(self,1,1,w-2,h-2,8,fill=fill,outline=MUTED if self.focused else fill if self.primary else BORDER)
        if self.shortcut:
            self.create_text(16,h/2,text=self.text,font=font(13,True),fill=fg,anchor='w')
            self.create_text(w-13,h/2,text=self.shortcut,font=('Segoe UI',-11),fill=DIM,anchor='e')
        else:self.create_text(w/2,h/2,text=self.text,font=font(13,True),fill=fg)


class Switch(tk.Canvas):
    def __init__(self,parent,variable):
        super().__init__(parent,width=40,height=24,bg=parent.cget('bg'),highlightthickness=0,
                         bd=0,takefocus=True,cursor='hand2')
        self.variable=variable;self.enabled=True;self.focused=False
        self.trace=variable.trace_add('write',lambda *_:self.draw())
        self.bind('<Button-1>',self.toggle);self.bind('<space>',self.toggle);self.bind('<Return>',self.toggle)
        self.bind('<FocusIn>',lambda _:self._focus(True));self.bind('<FocusOut>',lambda _:self._focus(False))
        self.bind('<Configure>',lambda _:self.draw())
        self.bind('<Destroy>',self._destroy)

    def _destroy(self,event):
        if event.widget is self:self.variable.trace_remove('write',self.trace)

    def _focus(self,value):self.focused=value;self.draw()
    def toggle(self,_=None):
        if self.enabled:self.variable.set(not self.variable.get())
        return 'break'

    def set_enabled(self,enabled):
        self.enabled=bool(enabled);self.configure(cursor='hand2' if enabled else 'arrow',takefocus=bool(enabled));self.draw()

    def draw(self):
        self.delete('all');on=self.variable.get()
        fill=('#fafafa' if on else '#3f3f46') if self.enabled else '#27272a'
        rounded(self,1,2,38,20,10,fill=fill,outline=MUTED if self.focused else fill)
        x=28 if on else 12
        self.create_oval(x-7,5,x+7,19,fill=(BG if on else TEXT) if self.enabled else DIM,outline='')


class Meter(tk.Canvas):
    def __init__(self,parent,color=TEXT):
        super().__init__(parent,height=4,bg=parent.cget('bg'),highlightthickness=0,bd=0)
        self.value=0;self.color=color
        self.bind('<Configure>',lambda _:self.draw())

    def set(self,value):self.value=max(0,min(100,value));self.draw()
    def draw(self):
        self.delete('all');w=self.winfo_width()
        rounded(self,0,0,w,4,2,fill=BORDER,outline='')
        if self.value:rounded(self,0,0,max(4,w*self.value/100),4,2,fill=self.color,outline='')


class Tooltip:
    def __init__(self,widget,text):
        self.widget=widget;self.text=text;self.after=None;self.popup=None
        widget.bind('<Enter>',self.schedule,add='+');widget.bind('<Leave>',self.hide,add='+')
        widget.bind('<Button-1>',self.hide,add='+');widget.bind('<Destroy>',self.hide,add='+')

    def schedule(self,_=None):self.after=self.widget.after(600,self.show)
    def show(self):
        self.after=None
        if not self.widget.winfo_exists() or not self.widget.winfo_viewable():return
        self.popup=tk.Toplevel(self.widget);self.popup.overrideredirect(True)
        self.popup.configure(bg=BORDER)
        label(self.popup,self.text,12,padx=10,pady=7,wraplength=330,justify='left').pack(padx=1,pady=1)
        self.popup.geometry(f'+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty()+self.widget.winfo_height()+5}')

    def hide(self,_=None):
        if self.after:
            self.widget.after_cancel(self.after);self.after=None
        if self.popup:
            self.popup.destroy();self.popup=None


class Tabs(tk.Canvas):
    def __init__(self,parent,items,command):
        super().__init__(parent,bg=parent.cget('bg'),width=240,height=40,bd=0,highlightthickness=0,takefocus=True,cursor='hand2')
        self.command=command;self.selected=items[0];self.items=items;self.focused=False
        self.bind('<Button-1>',lambda e:self.select(items[max(0,min(len(items)-1,int(e.x/80)))]))
        self.bind('<Left>',lambda _:self.select(items[(items.index(self.selected)-1)%len(items)]))
        self.bind('<Right>',lambda _:self.select(items[(items.index(self.selected)+1)%len(items)]))
        self.bind('<FocusIn>',lambda _:self._focus(True));self.bind('<FocusOut>',lambda _:self._focus(False))
        self.refresh()

    def _focus(self,value):self.focused=value;self.refresh()
    def select(self,item):self.selected=item;self.refresh();self.command(item)
    def refresh(self):
        self.delete('all')
        rounded(self,0,0,240,40,9,fill=INPUT,outline='')
        for i,item in enumerate(self.items):
            if item==self.selected:rounded(self,i*80+4,4,72,32,6,fill=BORDER,outline=DIM if self.focused else BORDER)
            self.create_text(i*80+40,20,text=item,font=font(13),fill=TEXT if item==self.selected else MUTED)


class Scroller(tk.Frame):
    def __init__(self,parent):
        super().__init__(parent,bg=BG)
        self.bar=tk.Canvas(self,width=5,bg=BG,bd=0,highlightthickness=0)
        self.first=0.;self.last=1.;self.drag_offset=0
        self.canvas=tk.Canvas(self,bg=BG,highlightthickness=0,bd=0)
        self.canvas.configure(yscrollcommand=self._scrollbar)
        self.canvas.pack(fill='both',expand=True)
        self.body=tk.Frame(self.canvas,bg=BG,padx=28,pady=4)
        self.window=self.canvas.create_window(0,0,anchor='nw',window=self.body)
        self.body.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.window,width=e.width))
        self.canvas.bind('<MouseWheel>',self.scroll)
        self.bar.bind('<Configure>',lambda _:self._paint_bar())
        self.bar.bind('<Button-1>',self._grab)
        self.bar.bind('<B1-Motion>',self._drag)

    def _scrollbar(self,first,last):
        self.first,self.last=float(first),float(last)
        overflow=self.last-self.first<.999
        if overflow and not self.bar.winfo_manager():self.bar.pack(side='right',fill='y',before=self.canvas)
        elif not overflow and self.bar.winfo_manager():self.bar.pack_forget()
        self._paint_bar()

    def _paint_bar(self):
        self.bar.delete('all');h=self.bar.winfo_height()
        rounded(self.bar,0,self.first*h,4,max(16,(self.last-self.first)*h),2,fill=BORDER,outline='')

    def _grab(self,event):
        h=max(1,self.bar.winfo_height());self.drag_offset=event.y-self.first*h
        if not self.first*h<=event.y<=self.last*h:
            self.drag_offset=0;self.canvas.yview_moveto(event.y/h)

    def _drag(self,event):self.canvas.yview_moveto((event.y-self.drag_offset)/max(1,self.bar.winfo_height()))

    def scroll(self,event):
        if self.body.winfo_reqheight()>self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(event.delta/120),'units')
        return 'break'
