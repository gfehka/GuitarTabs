import tkinter as tk
from tkinter import messagebox
import pyaudio
import wave
import numpy as np

# ==========================================
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ
# ==========================================
p = pyaudio.PyAudio()
active_streams = []      # Список активных потоков звука
bars = []                # Хранилище объектов Entry для каждой тактовой сетки


is_playing = False       # Флаг состояния воспроизведения
current_highlighted_entries = [] # Список подсвеченных ячеек (визуальный курсор)

BPM = 120
STEP_INTERVAL = int(60000 / BPM / 2) # Интервал между восьмыми нотами в мс

after_id = None  # ID для управления отменой root.after

# Файлы звуков открытых струн (1 - тонкая Mi, 6 - толстая Mi)
STRING_FILES = [f"open_s{i}.wav" for i in range(1, 7)]

# ==========================================
# ЛОГИКА ОБРАБОТКИ ЗВУКА И ТЕМПА
# ==========================================

def update_bpm(val):    # Обновляет темп и пересчитывает задержку между шагами.
    global BPM, STEP_INTERVAL
    BPM = int(val)
    STEP_INTERVAL = int(60000 / BPM / 2)

def validate_fret(new_value):   # Проверка ввода: разрешены только цифры от 0 до 21 (лады гитары).
    if new_value == "": return True
    if new_value.isdigit():
        if 0 <= int(new_value) <= 21:
            return True
    return False

def get_shifted_data(filename, fret):    # Загружает WAV и меняет его частоту дискретизации.
                                         # fret = 0 (оригинал), fret = 1 (на полтона выше) и т.д.
    try:
        wf = wave.open(filename, 'rb')
        n_frames = wf.getnframes()
        data = wf.readframes(n_frames)
        audio_data = np.frombuffer(data, dtype=np.int16)

        # Формула изменения высоты тона через изменение скорости воспроизведения
        shift_factor = 2 ** (fret / 12.0)
        new_rate = int(wf.getframerate() * shift_factor)
        wf.close()
        return audio_data, new_rate
    except Exception as e:
        print(f"Ошибка загрузки звука: {e}")
        return None, None

# ==========================================
# УПРАВЛЕНИЕ ВОСПРОИЗВЕДЕНИЕМ
# ==========================================

def clear_all_entries():    #Очищает все ячейки во всех тактах.
    if not bars: return
    if messagebox.askyesno("Очистка", "Очистить все введённые лады?"):
        for bar in bars:
            for row in bar:
                for entry in row:
                    entry.delete(0, tk.END)

def clear_highlight():  # Сброс подсветки ячеек.
    global current_highlighted_entries
    for en in current_highlighted_entries:
        try: en.config(bg="white")
        except: pass
    current_highlighted_entries.clear()

def stop_audio():       # Полная остановка звука и сброс состояния плеера.
    global is_playing, after_id
    is_playing = False

    # Отменяем запланированный следующий шаг, если он есть
    if after_id:
        root.after_cancel(after_id)
        after_id = None

    clear_highlight()
    for s in active_streams:
        try:
            s.stop_stream()
            s.close()
        except: pass
    active_streams.clear()

def play_step(bar_idx, col_idx):
    global is_playing, current_highlighted_entries, after_id

    # Жесткая проверка: если нажали Stop, выходим из рекурсии немедленно
    if not is_playing:
        return

    if col_idx >= 8:
        play_step(bar_idx + 1, 0)
        return

    if bar_idx >= len(bars):
        stop_audio()
        return

    clear_highlight()
    current_bar = bars[bar_idx]
    streams_to_start = []

    for row in range(6):
        en = current_bar[row][col_idx]
        en.config(bg="#FFF59D")
        current_highlighted_entries.append(en)

        fret_raw = en.get()
        if fret_raw.isdigit():
            fret = int(fret_raw)
            audio_data, rate = get_shifted_data(STRING_FILES[row], fret)
            if audio_data is not None:
                def make_callback(data_bytes):
                    ptr = [0]
                    def callback(in_data, frame_count, time_info, status):
                        chunk = data_bytes[ptr[0] : ptr[0] + frame_count * 2]
                        ptr[0] += len(chunk)
                        if len(chunk) == 0: return (None, pyaudio.paComplete)
                        return (chunk, pyaudio.paContinue)
                    return callback

                stream = p.open(format=pyaudio.paInt16, channels=1, rate=rate,
                                output=True, stream_callback=make_callback(audio_data.tobytes()))
                streams_to_start.append(stream)
                active_streams.append(stream)

    for s in streams_to_start:
        s.start_stream()

    active_streams[:] = [s for s in active_streams if s.is_active()]

    # Планируем следующий шаг и сохраняем ЕГО ID
    if is_playing:  # Проверяем, что всё еще играем
        after_id = root.after(STEP_INTERVAL, lambda: play_step(bar_idx, col_idx + 1))

# ==========================================
# ИНТЕРФЕЙС И ГРАФИКА
# ==========================================

def add_bar():      # Добавляет новый графический блок (такт) в секвенсор.
    bar_frame = tk.Frame(scrollable_frame)
    bar_frame.pack(side=tk.LEFT, padx=0)
    new_bar_entries = [[] for _ in range(6)]
    vcmd = (root.register(validate_fret), '%P')

    for r in range(6):
        # Отрисовка разделителей и названий струн
        tk.Label(bar_frame, text=" | ", fg="gray", font=("Arial", 10, "bold")).grid(row=r, column=0, padx=2)
        if len(bars) == 0:
            tk.Label(bar_frame, text=f"S{r+1}", fg="blue", width=3).grid(row=r, column=1)

        # Создание 8 полей ввода для нот в такте
        for c in range(8):
            en = tk.Entry(bar_frame, width=3, justify='center', bg="white",
                          validate="key", validatecommand=vcmd)
            en.grid(row=r, column=2 + (c * 2), padx=1, pady=2)
            new_bar_entries[r].append(en)
            if c < 7:
                tk.Label(bar_frame, text="---", fg="gray").grid(row=r, column=3 + (c * 2))

    bars.append(new_bar_entries)
    canvas.update_idletasks()
    canvas.configure(scrollregion=canvas.bbox("all"))

def start_sequence():       # Запуск воспроизведения с самого начала.
    global is_playing
    # Перед запуском всегда полностью останавливаем старый процесс
    stop_audio()
    if not bars: return
    is_playing = True
    play_step(0, 0)

def show_help():    # Вывод справки
    help_text = (
        "Инструкция GuitarTabs:\n\n"
        "1. Вводите номер лада (0-21) в ячейки.\n"
        "2. Каждая строка — это струна гитары (S1-S6).\n"
        "3. Кнопка '+ Add Bar' добавляет новый такт.\n"
        "4. Используйте ползунок BPM для изменения скорости.\n"
        "5. Для работы нужны файлы open_s1.wav ... open_s6.wav в папке с программой."
    )
    messagebox.showinfo("Справка", help_text)

def open_sequencer():       # Экран редактора табулатур.
    for widget in root.winfo_children(): widget.destroy()
    global canvas, scrollable_frame
    root.geometry("1100x450")

    # Панель управления (Кнопки и BPM)
    top_panel = tk.Frame(root)
    top_panel.pack(fill=tk.X, pady=10)

    tk.Button(top_panel, text='▶ Play', command=start_sequence, width=10, bg="#e1f5fe").pack(side=tk.LEFT, padx=5)
    tk.Button(top_panel, text='■ Stop', command=stop_audio, width=10, bg="#ffebee").pack(side=tk.LEFT, padx=5)
    tk.Button(top_panel, text='+ Add Bar', command=add_bar, width=10, bg="#e8f5e9").pack(side=tk.LEFT, padx=5)
    tk.Button(top_panel, text='- Clear All', command=clear_all_entries, width=12, bg="#fafafa").pack(side=tk.LEFT, padx=5)

    tk.Label(top_panel, text="BPM:", font=("Arial", 10)).pack(side=tk.LEFT, padx=(20, 5))
    bpm_scale = tk.Scale(top_panel, from_=40, to_=240, orient=tk.HORIZONTAL,
                         command=update_bpm, length=150)
    bpm_scale.set(BPM)
    bpm_scale.pack(side=tk.LEFT)

    tk.Button(top_panel, text='Назад', command=show_main_menu, width=10).pack(side=tk.RIGHT, padx=5)

    # Область прокрутки для тактов
    canvas = tk.Canvas(root, height=250)
    scrollbar = tk.Scrollbar(root, orient="horizontal", command=canvas.xview)
    scrollable_frame = tk.Frame(canvas)

    scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(xscrollcommand=scrollbar.set)

    canvas.pack(side="top", fill="both", expand=True)
    scrollbar.pack(side="bottom", fill="x")
    add_bar()

def show_main_menu():   # Главное меню программы.
    stop_audio()
    bars.clear()
    for widget in root.winfo_children(): widget.destroy()
    root.geometry("600x400")

    tk.Label(root, text="GuitarTabs", font=("Arial", 40, "bold")).pack(pady=30)

    btn_style = {"width": 20, "height": 2, "font": ("Arial", 12), "bg":"#e8f5e9"}
    tk.Button(root, text="Новый проект", command=open_sequencer, **btn_style).pack(pady=10)
    tk.Button(root, text="Справка", command=show_help, **btn_style).pack(pady=10)
    tk.Button(root, text="Выход", command=on_closing, **btn_style).pack(pady=10)

def on_closing():   # Безопасное закрытие программы.
    stop_audio()
    p.terminate()
    root.destroy()

# ==========================================
# ЗАПУСК ИНТЕРФЕЙСА
# ==========================================
root = tk.Tk()
root.title('GuitarTabs')
root.protocol("WM_DELETE_WINDOW", on_closing)
show_main_menu()
root.mainloop()
