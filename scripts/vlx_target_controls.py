"""Text selection of the bee and two visually distinct guitars."""
import re
import threading


ALIASES = {'小蜜蜂': (0,), '蜜蜂': (0,), '蜜蜂玩偶': (0,), '黄色小蜜蜂玩偶': (0,),
           '大吉他': (1,), '大电吉他': (1,), '小吉他': (2,),
           '白色电吉他': (1, 2), '白色吉他': (1, 2), '电吉他': (1, 2), '吉他': (1, 2)}
# Physical size in the image is deliberately not a classification feature.
# The reference's large instrument is a double-cutaway electric guitar; the
# small instrument has a rounded white body, dark neck and round sound hole.
QUERIES = {0: 'bee plush toy', 1: 'white electric guitar', 2: 'white ukulele'}
LABELS = {0: '小蜜蜂', 1: '大吉他', 2: '小吉他'}
HELP = '支持：小蜜蜂、大吉他、小吉他；输入“吉他/电吉他”同时标注大小两把'


class TargetSelection:
    def __init__(self, text=''):
        self.lock = threading.Lock()
        self.revision = 0
        self.targets = ()
        self.select(text)

    def select(self, text):
        tokens = [s for s in re.split(r'[、,，;；\s]+', text.strip()) if s]
        if any(token not in ALIASES for token in tokens):
            self.clear()
            raise ValueError(HELP)
        targets = tuple(sorted({key for token in tokens for key in ALIASES[token]}))
        with self.lock:
            if targets != self.targets:
                self.revision += 1
                self.targets = targets
            return self.revision, self.targets

    def clear(self):
        return self.select('')

    def snapshot(self):
        with self.lock:
            return self.revision, self.targets

    def accepts(self, revision):
        current, targets = self.snapshot()
        return bool(targets) and current == revision


def query_for(targets):
    return '; '.join(QUERIES[key] for key in targets)


class TargetWindow:
    def __init__(self, selection, stop, width=1280, text=''):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.selection = selection
        self.stop = stop
        self.root = tk.Tk()
        self.root.title('VLX 监控识别 — 输入目标后标注')
        self.root.geometry(f'{width}x820')
        self.root.minsize(800, 560)
        bar = ttk.Frame(self.root, padding=10)
        bar.pack(fill='x')
        ttk.Label(bar, text='识别目标：', font=('Microsoft YaHei', 12)).pack(side='left')
        self.text = tk.StringVar(value=text)
        self.entry = ttk.Entry(bar, textvariable=self.text, font=('Microsoft YaHei', 12), width=30)
        self.entry.pack(side='left', fill='x', expand=True, padx=6)
        self.apply_button = ttk.Button(bar, text='开始标注', command=self.submit)
        self.apply_button.pack(side='left', padx=5)
        self.clear_button = ttk.Button(bar, text='清除标注', command=lambda: self.text.set(''))
        self.clear_button.pack(side='left', padx=5)
        self.message = tk.StringVar(value='未输入目标：仅播放视频，不标注。' + HELP)
        ttk.Label(self.root, textvariable=self.message, padding=(12, 2, 12, 8), font=('Microsoft YaHei', 10)).pack(fill='x')
        self.video = tk.Label(self.root, bg='#111827')
        self.video.pack(fill='both', expand=True)
        self.text.trace_add('write', self.edited)
        self.entry.bind('<Return>', lambda _: self.submit())
        self.root.bind('<Escape>', lambda _: self.close())
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.entry.focus_set()
        self.last_image = None
        if text:
            self.submit()

    def edited(self, *_):
        # Editing invalidates in-flight results before the replacement is submitted.
        self.selection.clear()
        self.message.set('输入完成后按回车或点“开始标注”' if self.text.get().strip() else
                         '未输入目标：仅播放视频，不标注。' + HELP)

    def submit(self):
        try:
            _, targets = self.selection.select(self.text.get())
            self.message.set('当前标注：' + '、'.join(LABELS[t] for t in targets) if targets else
                             '未输入目标：仅播放视频，不标注')
        except ValueError as error:
            self.message.set(str(error))

    def pump(self):
        self.root.update_idletasks()
        self.root.update()

    def show(self, frame):
        import cv2
        from PIL import Image, ImageTk
        if frame is self.last_image:
            return
        self.last_image = frame
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        width, height = max(1, self.video.winfo_width()), max(1, self.video.winfo_height())
        scale = min(width / image.width, height / image.height)
        image = image.resize((max(1, int(image.width*scale)), max(1, int(image.height*scale))))
        self.photo = ImageTk.PhotoImage(image)
        self.video.configure(image=self.photo)

    def close(self):
        self.stop.set()
        self.root.withdraw()

    def destroy(self):
        self.root.destroy()
