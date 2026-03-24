import tkinter as tk
from tkinter import messagebox, filedialog
import struct
import threading
import numpy as np
import pyaudio
from collections import defaultdict

# ==========================================
# ФУНКЦИЯ СИНТЕЗА ГИТАРНЫХ ЗВУКОВ
# ==========================================

def GuitarString(frequency, duration=2., sample_rate=44100, p=0.9, beta=0.1, S=0.5, C=0.1, L=0.1):
    """
    Синтезирует звук гитарной струны заданной частоты.
    Возвращает numpy массив сэмплов в формате int16.
    """
    N = int(sample_rate/frequency)

    noise = np.random.uniform(-1, 1, N)

    # Pick-direction lowpass filter
    buffer = np.zeros_like(noise)
    buffer[0] = (1 - p) * noise[0]
    for i in range(1, N):
        buffer[i] = (1-p)*noise[i] + p*buffer[i-1]
    noise = buffer

    # Pick-position comb filter
    pick = int(beta*N+1/2)
    if pick == 0:
        pick = N
    buffer = np.zeros_like(noise)
    for i in range(N):
        if i-pick < 0:
            buffer[i] = noise[i]
        else:
            buffer[i] = noise[i]-noise[i-pick]
    noise = buffer

    # Создаем массив для сэмплов
    samples = np.zeros(int(sample_rate*duration))
    for i in range(N):
        samples[i] = noise[i]

    def DelayLine(n):
        return samples[n-N] if n-N >= 0 else 0

    def StringDampling_filter(n):
        return 0.996*((1-S)*DelayLine(n)+S*DelayLine(n-1))

    def FirstOrder_stringTuning_allpass_filter(n):
        return C*(StringDampling_filter(n)-samples[n-1])+StringDampling_filter(n-1)

    def Modeling(n):
        return FirstOrder_stringTuning_allpass_filter(n)

    # Моделирование затухания
    for i in range(N, len(samples)):
        samples[i] = Modeling(i)

    # Dynamic-level lowpass filter
    w_tilde = np.pi*frequency/sample_rate
    buffer = np.zeros_like(samples)
    buffer[0] = w_tilde/(1+w_tilde)*samples[0]
    for i in range(1, len(samples)):
        buffer[i] = w_tilde/(1+w_tilde)*(samples[i]+samples[i-1])+(1-w_tilde)/(1+w_tilde)*buffer[i-1]
    samples = (L**(4/3)*samples)+(1.0-L)*buffer

    # Нормируем и преобразуем в int16
    if np.max(np.abs(samples)) > 0:
        samples = samples / np.max(np.abs(samples))
    samples = np.int16(samples * 32767)

    return samples

def get_frequency_from_fret(string_freq, fret):
    """
    Вычисляет частоту ноты на основе частоты открытой струны и номера лада.
    """
    return string_freq * (2 ** (fret / 12.0))

# ==========================================
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ
# ==========================================
p = pyaudio.PyAudio()
tracks = []              # Список дорожек, каждая дорожка - список тактов
track_names = []         # Список имен дорожек

is_playing = False       # Флаг состояния воспроизведения
play_after_ids = []      # Список ID для отмены запланированных задач

BPM = 120
STEP_INTERVAL = int(60000 / BPM / 2) # Интервал между восьмыми нотами в мс

# Частоты открытых струн (стандартный строй EADGBE)
STRING_FREQUENCIES = [329.63, 246.94, 196.00, 146.83, 110.00, 82.41]  # От 1-й к 6-й
STRING_NAMES = ["E4", "B3", "G3", "D3", "A2", "E2"]

# Кэш для синтезированных звуков
sound_cache = {}

# Глобальные переменные для интерфейса
all_tracks_data = []     # Данные виджетов всех дорожек
main_canvas = None
main_scrollbar = None
tracks_container = None
track_buttons_frame = None
bpm_scale = None
horizontal_canvas = None
horizontal_scrollbar = None
bars_container = None

# ==========================================
# ЗВУКОВЫЕ ФУНКЦИИ
# ==========================================

def get_synthesized_sound(string_index, fret):
    """
    Синтезирует звук для указанной струны и лада.
    Использует кэш для оптимизации.
    """
    cache_key = (string_index, fret)

    if cache_key in sound_cache:
        return sound_cache[cache_key]

    # Получаем частоту открытой струны
    base_freq = STRING_FREQUENCIES[string_index]

    # Для открытой струны (fret=0) используем базовую частоту
    # Для зажатых ладов вычисляем частоту
    if fret == 0:
        frequency = base_freq
    else:
        frequency = get_frequency_from_fret(base_freq, fret)

    # Синтезируем звук
    audio_data = GuitarString(frequency, duration=0.8, sample_rate=44100)

    # Сохраняем в кэш
    sound_cache[cache_key] = audio_data

    return audio_data

def mix_audio_data(audio_list, sample_rate=44100):
    """
    Смешивает несколько аудиофайлов в один.
    """
    if not audio_list:
        return None

    # Определяем максимальную длину
    max_length = max(len(audio) for audio in audio_list)

    # Создаем массив для смешивания
    mixed = np.zeros(max_length, dtype=np.float32)

    for audio in audio_list:
        # Преобразуем в float32 для смешивания
        audio_float = audio.astype(np.float32) / 32767.0
        mixed[:len(audio_float)] += audio_float

    # Нормируем, чтобы избежать клиппинга
    max_val = np.max(np.abs(mixed))
    if max_val > 0:
        mixed = mixed / max_val * 0.8  # 80% громкости для запаса

    # Преобразуем обратно в int16
    return np.int16(mixed * 32767)

def play_chord_thread(audio_data, sample_rate=44100):
    """
    Воспроизводит смешанный звук аккорда в отдельном потоке.
    """
    def play():
        stream = None
        try:
            stream = p.open(format=pyaudio.paInt16,
                          channels=1,
                          rate=sample_rate,
                          output=True)
            audio_bytes = audio_data.tobytes()
            stream.write(audio_bytes)
        except Exception as e:
            print(f"Ошибка воспроизведения: {e}")
        finally:
            if stream:
                try:
                    if stream.is_active():
                        stream.stop_stream()
                    stream.close()
                except:
                    pass

    thread = threading.Thread(target=play, daemon=True)
    thread.start()

def clear_cache():
    """Очищает кэш синтезированных звуков"""
    global sound_cache
    sound_cache.clear()

# ==========================================
# ЛОГИКА ОБРАБОТКИ ТЕМПА
# ==========================================

def update_bpm(val):
    """Обновляет темп и пересчитывает задержку между шагами."""
    global BPM, STEP_INTERVAL
    BPM = int(val)
    STEP_INTERVAL = int(60000 / BPM / 2)

def validate_fret(new_value):
    """Проверка ввода: разрешены только цифры от 0 до 21."""
    if new_value == "": return True
    if new_value.isdigit():
        if 0 <= int(new_value) <= 21:
            return True
    return False

# ==========================================
# ЭКСПОРТ/ИМПОРТ GP5
# ==========================================

def write_string(file, text):
    """Вспомогательная функция для записи строки в бинарный файл"""
    encoded = text.encode('utf-8')
    file.write(struct.pack('<i', len(encoded)))
    file.write(encoded)

def read_string(file):
    """Вспомогательная функция для чтения строки из бинарного файла"""
    length = struct.unpack('<i', file.read(4))[0]
    if length > 0:
        return file.read(length).decode('utf-8', errors='ignore')
    return ""

def export_to_gp5():
    """Экспорт табулатуры в формат GP5"""
    if not tracks:
        messagebox.showwarning("Нет данных", "Нет дорожек для экспорта!")
        return

    filename = filedialog.asksaveasfilename(
        defaultextension=".gp5",
        filetypes=[("Guitar Pro 5 files", "*.gp5"), ("All files", "*.*")],
        title="Сохранить табулатуру как"
    )

    if not filename:
        return

    try:
        with open(filename, 'wb') as f:
            f.write(b'Guitar Pro 5 file')
            f.write(struct.pack('<i', 0))

            write_string(f, f"Exported Tablature - {len(tracks)} tracks")
            write_string(f, "GuitarTabs")
            write_string(f, "")
            write_string(f, "")
            write_string(f, "")
            write_string(f, "")
            write_string(f, "")

            f.write(struct.pack('<i', 0))
            f.write(struct.pack('<i', 0))
            f.write(struct.pack('<i', 0))

            f.write(struct.pack('<i', BPM))

            max_bars = max([len(track) for track in tracks]) if tracks else 0
            f.write(struct.pack('<i', max_bars))
            f.write(struct.pack('<i', 6))

            string_notes = [40, 45, 50, 55, 59, 64]
            for note in string_notes:
                f.write(struct.pack('<i', note))

            for track_idx, track in enumerate(tracks):
                write_string(f, track_names[track_idx])
                write_string(f, "Guitar")

                for bar_idx in range(max_bars):
                    bar_data = track[bar_idx] if bar_idx < len(track) else [["" for _ in range(8)] for _ in range(6)]

                    f.write(struct.pack('<i', 1))
                    f.write(struct.pack('<i', 4))
                    f.write(struct.pack('<i', 4))

                    for string_idx in range(6):
                        for note_idx in range(8):
                            fret_text = bar_data[string_idx][note_idx] if string_idx < len(bar_data) and note_idx < len(bar_data[string_idx]) else ""
                            if fret_text and fret_text.isdigit():
                                fret = int(fret_text)
                                if fret >= 0:  # 0 - открытая струна
                                    f.write(struct.pack('<i', 1))
                                    f.write(struct.pack('<i', note_idx))
                                    f.write(struct.pack('<i', string_idx + 1))
                                    f.write(struct.pack('<i', fret))
                                    f.write(struct.pack('<i', 4))
                                    f.write(struct.pack('<i', 0))

        messagebox.showinfo("Успех", f"Табулатура успешно экспортирована!")

    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось экспортировать файл: {str(e)}")

def import_from_gp5():
    """Импорт табулатуры из формата GP5"""
    filename = filedialog.askopenfilename(
        defaultextension=".gp5",
        filetypes=[("Guitar Pro 5 files", "*.gp5"), ("All files", "*.*")],
        title="Открыть табулатуру"
    )

    if not filename:
        return

    try:
        with open(filename, 'rb') as f:
            header = f.read(17)
            if header != b'Guitar Pro 5 file':
                messagebox.showerror("Ошибка", "Неверный формат файла GP5")
                return

            f.read(4)
            title = read_string(f)
            artist = read_string(f)
            album = read_string(f)
            author = read_string(f)
            copyright_info = read_string(f)
            tab_author = read_string(f)
            instructions = read_string(f)

            f.read(4)
            f.read(4)
            f.read(4)

            bpm = struct.unpack('<i', f.read(4))[0]
            update_bpm(bpm)
            if bpm_scale:
                bpm_scale.set(bpm)

            num_bars = struct.unpack('<i', f.read(4))[0]
            num_strings = struct.unpack('<i', f.read(4))[0]

            for i in range(num_strings):
                struct.unpack('<i', f.read(4))[0]

            tracks.clear()
            track_names.clear()

            track_data = []
            for bar_idx in range(num_bars):
                bar_data = [["" for _ in range(8)] for _ in range(6)]

                f.read(4)
                f.read(4)
                f.read(4)

                for string_idx in range(6):
                    for note_idx in range(8):
                        try:
                            has_note = struct.unpack('<i', f.read(4))[0]
                            if has_note == 1:
                                position = struct.unpack('<i', f.read(4))[0]
                                string_num = struct.unpack('<i', f.read(4))[0]
                                fret = struct.unpack('<i', f.read(4))[0]
                                f.read(4)
                                f.read(4)
                                if 0 <= position < 8 and 1 <= string_num <= 6:
                                    bar_data[string_num - 1][position] = str(fret)
                        except:
                            break

                track_data.append(bar_data)

            tracks.append(track_data)
            track_names.append(f"Track 1")

            update_all_tracks_display()
            update_track_buttons()

            messagebox.showinfo("Успех", f"Табулатура успешно импортирована!\n\n"
                               f"Количество тактов: {num_bars}\n"
                               f"BPM: {bpm}")

    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось импортировать файл: {str(e)}")

# ==========================================
# УПРАВЛЕНИЕ ДОРОЖКАМИ
# ==========================================

def save_current_data():
    """Сохраняет текущие данные из Entry в модель tracks"""
    global all_tracks_data, tracks

    if not all_tracks_data:
        return

    for track_idx, track_data in enumerate(all_tracks_data):
        if track_idx >= len(tracks):
            continue

        track = tracks[track_idx]
        for bar_idx, bar_data in enumerate(track_data['bars_data']):
            if bar_idx >= len(track):
                continue

            for string_idx, entries in enumerate(bar_data):
                if string_idx >= 6:
                    continue

                for note_idx, entry in enumerate(entries):
                    if note_idx >= 8:
                        continue

                    val = entry.get()
                    if val != track[bar_idx][string_idx][note_idx]:
                        track[bar_idx][string_idx][note_idx] = val

def create_new_track():
    """Создает новую дорожку той же длины, что и остальные"""
    global tracks, track_names

    # Сохраняем текущие данные
    save_current_data()

    if tracks:
        num_bars = len(tracks[0])
    else:
        num_bars = 1

    new_track = []
    for _ in range(num_bars):
        new_track.append([["" for _ in range(8)] for _ in range(6)])

    tracks.append(new_track)
    track_names.append(f"Track {len(tracks)}")

    print(f"Создана дорожка {track_names[-1]} с {num_bars} тактами")

    update_all_tracks_display()
    update_track_buttons()

def delete_current_track():
    """Удаляет последнюю дорожку"""
    global tracks, track_names

    if len(tracks) <= 1:
        messagebox.showwarning("Предупреждение", "Нельзя удалить последнюю дорожку!")
        return

    if messagebox.askyesno("Удаление дорожки", f"Удалить {track_names[-1]}?"):
        # Сохраняем текущие данные перед удалением
        save_current_data()

        del tracks[-1]
        del track_names[-1]

        update_track_buttons()
        update_all_tracks_display()

def update_track_buttons():
    """Обновляет кнопки выбора дорожек"""
    global track_buttons_frame

    if not track_buttons_frame:
        return

    for widget in track_buttons_frame.winfo_children():
        widget.destroy()

    for i, name in enumerate(track_names):
        btn_style = {"width": 10, "height": 1, "font": ("Arial", 9)}
        btn = tk.Button(track_buttons_frame, text=name,
                      command=lambda idx=i: None,
                      bg="#e0e0e0", **btn_style)
        btn.pack(side=tk.LEFT, padx=2)

def update_all_tracks_display():
    """Обновляет отображение всех дорожек"""
    global horizontal_canvas, bars_container, all_tracks_data, main_canvas

    if not horizontal_canvas or not bars_container:
        return

    for widget in bars_container.winfo_children():
        widget.destroy()

    all_tracks_data = []

    print(f"Перестроение интерфейса. Всего дорожек: {len(tracks)}")

    for track_idx, track in enumerate(tracks):
        print(f"  Дорожка {track_idx}: {len(track)} тактов")
        create_track_widget(track_idx, track)

    horizontal_canvas.update_idletasks()
    horizontal_canvas.configure(scrollregion=horizontal_canvas.bbox("all"))

    if main_canvas:
        main_canvas.update_idletasks()
        main_canvas.configure(scrollregion=main_canvas.bbox("all"))

def create_track_widget(track_idx, track):
    """Создает виджет для отдельной дорожки"""
    global bars_container, all_tracks_data

    vcmd = (root.register(validate_fret), '%P')

    track_frame = tk.Frame(bars_container, relief=tk.RIDGE, bd=2, padx=5, pady=5)
    track_frame.pack(fill=tk.X, pady=5)

    header_frame = tk.Frame(track_frame)
    header_frame.pack(fill=tk.X, pady=2)

    tk.Label(header_frame, text=f"{track_names[track_idx]}",
            font=("Arial", 10, "bold"), fg="blue").pack(side=tk.LEFT, padx=5)

    bars_frame = tk.Frame(track_frame)
    bars_frame.pack(fill=tk.X, pady=2)

    bars_data = []
    for bar_idx, bar in enumerate(track):
        bar_frame = tk.Frame(bars_frame)
        bar_frame.pack(side=tk.LEFT, padx=0)
        bar_entries = [[] for _ in range(6)]

        for r in range(6):
            tk.Label(bar_frame, text=" | ", fg="gray", font=("Arial", 10, "bold")).grid(
                row=r, column=0, padx=2)

            if bar_idx == 0:
                tk.Label(bar_frame, text=f"S{r+1}", fg="blue",
                        width=8, anchor="e").grid(row=r, column=1)

            for c in range(8):
                en = tk.Entry(bar_frame, width=3, justify='center', bg="white",
                              validate="key", validatecommand=vcmd)
                en.grid(row=r, column=2 + (c * 2), padx=1, pady=2)

                if bar and r < len(bar) and c < len(bar[r]):
                    en.insert(0, bar[r][c])

                bar_entries[r].append(en)

                if c < 7:
                    tk.Label(bar_frame, text="---", fg="gray").grid(
                        row=r, column=3 + (c * 2))

        bars_data.append(bar_entries)

    all_tracks_data.append({
        'track_frame': track_frame,
        'bars_data': bars_data
    })

def add_bar_to_all_tracks():
    """Добавляет новый такт во все дорожки (без всплывающего окна)"""
    global tracks

    if not tracks:
        messagebox.showwarning("Предупреждение", "Нет дорожек для добавления такта!")
        return

    print(f"\n=== ДОБАВЛЕНИЕ ТАКТА ===")

    # Сохраняем текущие данные перед добавлением такта
    save_current_data()

    for track_idx in range(len(tracks)):
        tracks[track_idx].append([["" for _ in range(8)] for _ in range(6)])

    update_all_tracks_display()

    if horizontal_canvas:
        horizontal_canvas.xview_moveto(1.0)

    new_bar_count = len(tracks[0]) if tracks else 0
    print(f"Добавлен такт #{new_bar_count} во все {len(tracks)} дорожек")

def clear_current_track():
    """Очищает все ячейки во всех дорожках"""
    global all_tracks_data

    if not all_tracks_data:
        return

    if messagebox.askyesno("Очистка", "Очистить все введённые лады?"):
        for track_data in all_tracks_data:
            for bar in track_data['bars_data']:
                for row in range(6):
                    for entry in bar[row]:
                        entry.delete(0, tk.END)

        # Также очищаем данные в модели
        for track_idx in range(len(tracks)):
            for bar_idx in range(len(tracks[track_idx])):
                for row in range(6):
                    for col in range(8):
                        tracks[track_idx][bar_idx][row][col] = ""

def clear_all_tracks():
    """Очищает все дорожки"""
    if messagebox.askyesno("Очистка", "Очистить все данные во всех дорожках?"):
        # Сохраняем текущие данные перед очисткой
        save_current_data()

        for track_idx in range(len(tracks)):
            for bar_idx in range(len(tracks[track_idx])):
                for row in range(6):
                    for col in range(8):
                        tracks[track_idx][bar_idx][row][col] = ""
        update_all_tracks_display()

# ==========================================
# ЛОГИКА ВОСПРОИЗВЕДЕНИЯ ВСЕХ ДОРОЖЕК ОДНОВРЕМЕННО
# ==========================================

def clear_all_highlights():
    """Очищает подсветку всех ячеек"""
    for track_data in all_tracks_data:
        try:
            for bar in track_data['bars_data']:
                for row in range(6):
                    for entry in bar[row]:
                        try:
                            entry.config(bg="white")
                        except:
                            pass
        except:
            pass

def play_step_for_all_tracks(bar_idx, col_idx):
    """Воспроизведение одного шага для всех дорожек одновременно"""
    global is_playing, play_after_ids

    if not is_playing:
        return

    if not all_tracks_data:
        stop_all_tracks()
        return

    if col_idx >= 8:
        play_step_for_all_tracks(bar_idx + 1, 0)
        return

    # Проверяем, есть ли такт в какой-либо дорожке
    max_bars = max([len(track_data['bars_data']) for track_data in all_tracks_data]) if all_tracks_data else 0
    if bar_idx >= max_bars:
        stop_all_tracks()
        return

    # Снимаем подсветку со всех ячеек
    clear_all_highlights()

    # Собираем все звуки для текущего шага
    chord_audio_list = []

    # Для каждой дорожки собираем ноты
    for track_idx, track_data in enumerate(all_tracks_data):
        if bar_idx < len(track_data['bars_data']):
            try:
                bars_data = track_data['bars_data']
                for row in range(6):
                    if col_idx < len(bars_data[bar_idx][row]):
                        entry = bars_data[bar_idx][row][col_idx]
                        entry.config(bg="#FFF59D")

                        # Получаем значение лада
                        fret_raw = entry.get()
                        if fret_raw and fret_raw.isdigit():
                            fret = int(fret_raw)
                            if 0 <= fret <= 21:
                                audio_data = get_synthesized_sound(row, fret)
                                chord_audio_list.append(audio_data)
            except Exception as e:
                print(f"Ошибка в дорожке {track_idx}: {e}")

    # Воспроизводим все ноты одновременно (как аккорд)
    if chord_audio_list:
        mixed_audio = mix_audio_data(chord_audio_list)
        if mixed_audio is not None:
            play_chord_thread(mixed_audio)

    # Планируем следующий шаг
    if is_playing:
        after_id = root.after(STEP_INTERVAL, lambda: play_step_for_all_tracks(bar_idx, col_idx + 1))
        play_after_ids.append(after_id)

def start_all_tracks():
    """Запускает воспроизведение всех дорожек одновременно"""
    global is_playing, play_after_ids

    print(f"\n=== ЗАПУСК ВОСПРОИЗВЕДЕНИЯ ВСЕХ ДОРОЖЕК ===")
    print(f"Всего дорожек: {len(tracks)}")

    if not tracks:
        messagebox.showwarning("Предупреждение", "Нет дорожек для воспроизведения!")
        return

    if not all_tracks_data:
        messagebox.showwarning("Предупреждение", "Нет данных для воспроизведения!")
        return

    # Сохраняем данные перед воспроизведением
    save_current_data()

    # Проверяем, есть ли такты
    max_bars = max([len(track_data['bars_data']) for track_data in all_tracks_data]) if all_tracks_data else 0
    if max_bars == 0:
        messagebox.showwarning("Предупреждение", "Нет тактов для воспроизведения!")
        return

    print(f"Количество тактов: {max_bars}")
    print("=== НАЧАЛО ВОСПРОИЗВЕДЕНИЯ ===\n")

    stop_all_tracks()
    is_playing = True
    clear_all_highlights()
    play_after_ids = []

    play_step_for_all_tracks(0, 0)

def stop_all_tracks():
    """Останавливает воспроизведение всех дорожек"""
    global is_playing, play_after_ids

    print("Остановка воспроизведения")
    is_playing = False

    for after_id in play_after_ids:
        try:
            root.after_cancel(after_id)
        except:
            pass
    play_after_ids = []

    clear_all_highlights()

# ==========================================
# ИНТЕРФЕЙС
# ==========================================

def show_help():
    """Вывод справки"""
    help_text = (
        "Инструкция GuitarTabs:\n\n"
        "1. Вводите номер лада (0-21) в ячейки.\n"
        "   • 0 - открытая струна\n"
        "   • 1-21 - зажатые лады\n"
        "2. Каждая строка — струна гитары (E4, B3, G3, D3, A2, E2).\n"
        "3. Все дорожки отображаются друг под другом.\n"
        "4. '+ New Track' - создает новую дорожку той же длины.\n"
        "5. '- Delete Last Track' - удаляет последнюю дорожку.\n"
        "6. '+ Add Bar' - добавляет такт во ВСЕ дорожки.\n"
        "7. 'Clear All' - очищает все дорожки.\n"
    )
    messagebox.showinfo("Справка", help_text)

def open_sequencer():
    """Открывает главное окно секвенсора"""
    global main_canvas, main_scrollbar, tracks_container, track_buttons_frame, bpm_scale
    global horizontal_canvas, horizontal_scrollbar, bars_container

    for widget in root.winfo_children():
        widget.destroy()

    root.geometry("1300x650")

    # Верхняя панель управления
    top_panel = tk.Frame(root)
    top_panel.pack(fill=tk.X, pady=10)

    btn_frame = tk.Frame(top_panel)
    btn_frame.pack(side=tk.LEFT)

    tk.Button(btn_frame, text='▶ Play All', command=start_all_tracks, width=12,
             bg="#e1f5fe").pack(side=tk.LEFT, padx=2)
    tk.Button(btn_frame, text='■ Stop', command=stop_all_tracks, width=10,
             bg="#ffebee").pack(side=tk.LEFT, padx=2)
    tk.Button(btn_frame, text='+ Add Bar', command=add_bar_to_all_tracks, width=10,
             bg="#e8f5e9").pack(side=tk.LEFT, padx=2)
    tk.Button(btn_frame, text='- Clear All', command=clear_current_track, width=10,
             bg="#fafafa").pack(side=tk.LEFT, padx=2)
    tk.Button(btn_frame, text='Clear Cache', command=clear_cache, width=12,
             bg="#fff3e0").pack(side=tk.LEFT, padx=2)

    file_frame = tk.Frame(top_panel)
    file_frame.pack(side=tk.LEFT, padx=20)

    tk.Button(file_frame, text='Import GP5', command=import_from_gp5, width=12,
             bg="#e3f2fd").pack(side=tk.LEFT, padx=2)
    tk.Button(file_frame, text='Export GP5', command=export_to_gp5, width=12,
             bg="#fff3e0").pack(side=tk.LEFT, padx=2)

    bpm_frame = tk.Frame(top_panel)
    bpm_frame.pack(side=tk.LEFT, padx=20)

    tk.Label(bpm_frame, text="BPM:", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
    bpm_scale = tk.Scale(bpm_frame, from_=40, to_=240, orient=tk.HORIZONTAL,
                         command=update_bpm, length=150)
    bpm_scale.set(BPM)
    bpm_scale.pack(side=tk.LEFT)

    tk.Button(top_panel, text='Назад', command=show_main_menu, width=10).pack(side=tk.RIGHT, padx=5)
    tk.Button(top_panel, text='❓', command=show_help, width=3).pack(side=tk.RIGHT, padx=2)

    # Панель дорожек
    track_panel = tk.Frame(root)
    track_panel.pack(fill=tk.X, pady=5, padx=10)

    tk.Label(track_panel, text="Tracks:", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=5)

    track_buttons_frame = tk.Frame(track_panel)
    track_buttons_frame.pack(side=tk.LEFT, padx=5)

    tk.Button(track_panel, text='+ New Track', command=create_new_track, width=12,).pack(side=tk.LEFT, padx=5)
    tk.Button(track_panel, text='- Delete Last Track', command=delete_current_track, width=15,).pack(side=tk.LEFT, padx=5)

    # Горизонтальная прокрутка для тактов
    horizontal_canvas = tk.Canvas(root, height=380, highlightthickness=0)
    horizontal_scrollbar = tk.Scrollbar(root, orient="horizontal", command=horizontal_canvas.xview)
    bars_container = tk.Frame(horizontal_canvas)

    bars_container.bind("<Configure>", lambda e: horizontal_canvas.configure(scrollregion=horizontal_canvas.bbox("all")))
    horizontal_canvas.create_window((0, 0), window=bars_container, anchor="nw")
    horizontal_canvas.configure(xscrollcommand=horizontal_scrollbar.set)

    horizontal_canvas.pack(side="top", fill="both", expand=True, padx=10, pady=5)
    horizontal_scrollbar.pack(side="top", fill="x", padx=10)

    # Вертикальная прокрутка для дорожек
    main_canvas = tk.Canvas(root, height=100, highlightthickness=0)
    main_scrollbar = tk.Scrollbar(root, orient="vertical", command=main_canvas.yview)
    tracks_container = tk.Frame(main_canvas)

    tracks_container.bind("<Configure>", lambda e: main_canvas.configure(scrollregion=main_canvas.bbox("all")))
    main_canvas.create_window((0, 0), window=tracks_container, anchor="nw")
    main_canvas.configure(yscrollcommand=main_scrollbar.set)

    main_canvas.pack(side="left", fill="both", expand=True, padx=10, pady=5)
    main_scrollbar.pack(side="right", fill="y")

    # Связываем прокрутку
    def on_vertical_scroll(*args):
        main_canvas.yview(*args)

    def on_horizontal_scroll(*args):
        horizontal_canvas.xview(*args)

    main_scrollbar.config(command=on_vertical_scroll)
    horizontal_scrollbar.config(command=on_horizontal_scroll)

    # Прокрутка колесиком мыши
    def on_mousewheel(event):
        if event.state == 0:  # Обычная прокрутка
            main_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        elif event.state == 1:  # Shift + колесико для горизонтальной прокрутки
            horizontal_canvas.xview_scroll(int(-1*(event.delta/120)), "units")

    root.bind_all("<MouseWheel>", on_mousewheel)
    root.bind_all("<Shift-MouseWheel>", on_mousewheel)

    create_new_track()

def show_main_menu():
    """Главное меню программы"""
    global tracks, track_names, all_tracks_data

    stop_all_tracks()
    tracks.clear()
    track_names.clear()
    all_tracks_data = []

    for widget in root.winfo_children():
        widget.destroy()

    root.geometry("600x480")

    tk.Label(root, text="GuitarTabs", font=("Arial", 40, "bold")).pack(pady=30)

    btn_style = {"width": 25, "height": 2, "font": ("Arial", 12)}

    tk.Button(root, text="Новый проект", command=open_sequencer, bg="#e8f5e9", **btn_style).pack(pady=5)
    tk.Button(root, text="Открыть табулатуру", command=lambda: [open_sequencer(), import_from_gp5()],
              bg="#e3f2fd", **btn_style).pack(pady=5)
    tk.Button(root, text="Справка", command=show_help, bg="#fff3e0", **btn_style).pack(pady=5)
    tk.Button(root, text="Выход", command=on_closing, bg="#ffebee", **btn_style).pack(pady=5)

def on_closing():
    """Безопасное закрытие программы"""
    stop_all_tracks()
    clear_cache()
    try:
        p.terminate()
    except:
        pass
    root.destroy()

# ==========================================
# ЗАПУСК
# ==========================================
root = tk.Tk()
root.title('GuitarTabs')
root.protocol("WM_DELETE_WINDOW", on_closing)
show_main_menu()
root.mainloop()