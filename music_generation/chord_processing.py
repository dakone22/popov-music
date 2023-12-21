import itertools
import pathlib
import sys
from collections import defaultdict
from typing import NamedTuple

import pandas as pd
from mido import MidiFile, Message, MidiTrack
from music21.chord import Chord


# определяем ноту по номеру (см. midi формат)
def decode_note(i: int) -> str:
    # Массив имен нот (в некоторых странах B - си-бемоль, H - си (используем B нотацию))
    note_names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    # Определение октавы
    octave = i // 12 - 1  # Октавы нумеруются у музыкантов с 1 :-). Но мы то инженеры.
    # Определяем ноту
    note = i % 12  # нота в конкретной октаве (остаток от деления)
    # Формирование результата в виде строки названия ноты из нашего массива note_names и ее октава
    return note_names[note] + str(octave)


# определим элемент массива аккордов(L) в вершине
def init_chord_sequence(terminator: int, L: int) -> list[tuple[int]]:
    # начальный аккорд - пустой кортеж нот
    # Массив аккордов для графа де Брюйна
    return [(terminator, )] * L  # кортеж начальных вершин


# Функция формирования окна аккордов:
#  сдвигаем массив аккордов влево и задвигаем новый список нот (аккорд) в правую позицию
def shift(new_chord, chord_sequence):
    # Удаляем первый аккорд
    chord_sequence.pop(0)
    # Вставляем новый аккорд в конец массива
    chord_sequence.append(new_chord)
    return chord_sequence


# Найдем максимальное сочетание нот аккорда, являющееся созвучным (консонантный аккорд)
def consonant(chord):
    src_chord = Chord(chord)
    rem = src_chord.removeRedundantPitchNames(inPlace=True)
    max_chord_len = min([len(chord), 5])
    # Если аккорд созвучный, то возвращаем без изменений
    if src_chord.isConsonant():
        return chord

    # Иначе пробуем удалить ноты (вплоть до одной ноты в аккорде), чтобы получить максимальный созвучный акорд
    best_chord_length = 1
    best_chord = Chord([chord[0]])
    best_chord_notes = [chord[0]]
    for note_count in range(1, len(chord) + 1):
        # Получим все сочетания из note_count нот
        chord_list = itertools.combinations(chord, max_chord_len + 1)
        # Для каждого сочетания проверим созвучность
        for i in list(chord_list):
            cur_chord = Chord(i)
            if cur_chord.isConsonant() and len(i) > best_chord_length:
                best_chord_notes = i
                best_chord_length = len(i)
    return best_chord_notes


class NoteEvent(NamedTuple):
    type: str
    note: int
    velocity: float


class ChordProcessor:
    MS_PER_BEAT_DEFAULT = 500_000 # начальное значение по стандарту, далее уточняется в треке через сообщение set_tempo

    def __init__(self, L: int, terminator: int = 0, banned_instruments=("Bass", "Drum"), debug=False):
        assert L > 2

        self.L = L
        self.terminator = terminator
        self.banned_instruments = banned_instruments
        self.debug = debug

    def _merge_track(self, mid: MidiFile) -> dict[int, list[NoteEvent]]:
        """
        Объединение треков - делаем из mid.type 1 (sync tracks) в mid.type 0 (single track)

        :param mid: Загруженный Midi-файл
        :param banned_instruments: Инструменты, которые нужно проигнорировать
        :return:
        """
        ticks_per_beat = mid.ticks_per_beat

        # Запишем время события из каждого трека в dictionary, каждый элемент которого - массив сообщений в данный момент времени
        merged_track = defaultdict(list)
        # Для каждого трека
        for track in mid.tracks:
            if any([(banned_instrument in track.name) for banned_instrument in self.banned_instruments]):
                continue

            # Начальный масштаб времени, отнесенный к 120 ударам в минуту
            ms_per_beat = self.MS_PER_BEAT_DEFAULT  # начальное значение по стандарту, далее уточняется в треке через сообщение set_tempo
            ms_per_tick = ms_per_beat / ticks_per_beat

            time = 0  # Время нарастающим итогом т.е. рассматриваем разницу между нотами, а не время с самого начала
            # Для каждого сообщения в треке
            for message in track:
                # Eсли сообщение о смене темпа
                if message.type == "set_tempo":
                    ms_per_beat = message.tempo
                    ms_per_tick = ms_per_beat / ticks_per_beat
                    # time += int(120 * message.time * ms_per_tick / 500_000)  # TODO: кажется, этого тут не должно быть, поэтому закомментим
                    if self.debug: print(f"Смена темпа на: {int(500_000 / ms_per_tick)}")

                # Если сообщение о нажатии или отпускании ноты
                elif message.type in ("note_on", "note_off"):
                    # Расчитаем текущий момент времени для темпа 120 ударов в минуту для нового сообщения (время в сообщениях измеряется в ticks)
                    time += int(120 * message.time * ms_per_tick / 500_000)  # TODO: непонятно, почему делим на какие-то 500_000?
                    
                    # Дизейблим перкуссию (10 канал по стандарту МИДИ используется для перкуссии.)
                    if message.channel == 10: continue

                    message_type = message.type
                    # Если time_on и velocity=0, то это событие равнозначно note_off
                    if message.type == "note_on" and message.velocity == 0:
                        message_type = "note_off"

                    # Добавим текущее событие к массиву в виде словаря {type, note, velocity}
                    note = NoteEvent(type=message_type, note=message.note, velocity=message.velocity)
                    merged_track[time].append(note)

        return merged_track

    def _track_append(self, track: MidiTrack, merged_track: dict[int, list[NoteEvent]]) -> None:
        previous_time = 0  # Начальная метка времени
        # Для каждого момента времени в словаре (сортируем его по времени), записываем событие в midi.
        for time in sorted(merged_track.keys()):
            if self.debug: print(f"{time}: {merged_track[time]}")

            # Рассчитаем промежуток времени от предыдущего события
            delta_t = int(time - previous_time)

            # Перебираем все сообщения в текущем моменте времени
            for event in merged_track[time]:
                # Формируем сообщение в формате midi
                track_message = Message(type=event.type, note=event.note, velocity=event.velocity, time=delta_t)
                track.append(track_message)

                # Все остальные события в этот момент времени имеют delta_t=0
                delta_t = 0

            # Переключаем момент времени на новую отметку
            previous_time = time

    def _process_midi_file(self, mid: MidiFile, df: pd.DataFrame) -> pd.DataFrame:
        # Показать последовательность аккордов и записать ее в csv
        for track in mid.tracks:  # он должен быть один, так как он смерджен!!!
            time = 0  # время трека, потому что time=time+message.time, а время между нотами = message.time

            chord_sequence = init_chord_sequence(self.terminator, self.L)
            chord_id = chord_sequence[self.L - 1][0]  # формируем аккорд в цифровом значении
            prev_chord_id = chord_sequence[self.L - 2][0]  # начальный аккорд начинается с нуля - это нужно для графа. берем переменную "предыдущий акк"
            chord = ()  # список всех нажатых нот в данный момент. т.е наш аккорд
            # prev_chord = () # список всех нажатых нот для предыдущего аккорда

            for message_index, message in enumerate(track):
                if message.type in ("note_on", "note_off"):
                    # Напечатаем аккорд, так как он сейчас изменится
                    if (message.time != 0 and len(chord) > 0) or message_index == len(track):
                        # len(chord)>0 нужно для фильтрации аккордов и пауз. Мы устанавливаем, что акк не может быть пустым.

                        consonant_chord = chord  # TODO: не используется функция consonant
                        # consonant_chord = consonant(chord) # Применим алгоритм выделения аккорда наибольшим количеством нот

                        # Отсортируем по ноте, а потом по октаве нынешний и предыдущий аккорд
                        sorted_chord = sorted(consonant_chord)
                        sorted_prev_chord = sorted(chord_sequence[self.L - 1])  # Аккорд, который был предыдущим, сохранен в конце массива

                        # Аккорд chord, завершающий свое время жизни, еще не сохранен в массиве и хранится теперь в sorted_chord
                        # Предыдущий аккорд берем из chord_sequence[L-1]
                        # Сравним завершающийся аккорд с предыдущим, чтобы понять, что есть изменения
                        chords_equal = sorted_chord == sorted_prev_chord
                        # Начнем формирование пакета для записи кода аккорда (целочисленного)
                        chord_id = 0xE  # 0xE - обозначение начальный символ числа. Обнуляем предыдущий аккорд, чтобы формировать новый аккорд с нуля

                        # Создадим строку для записи аккорда
                        chord_string = ""

                        # формируем id на основе предыдущего акк + барьер (барьер - это разделение аккордов между предыдущим и текущим)
                        for i in range(1, self.L):  # 1..L-1  prev_prev_chord in chord_sequence
                            for n in chord_sequence[i]:  # Ex:(0x70,0x71,0x72,0x73),0x74 Список нажатых клавиш
                                chord_string += " " + decode_note(int(n))
                                chord_id = chord_id * 0x100 + int(n)
                            chord_id = chord_id * 0x100 + 0x0f  # 0xf - обозначение нашего барьера. Это 255 в десятичном виде
                            chord_string = chord_string + " -->"
                        for n in sorted_chord:  # Аккорд, завершивший звучание - список нажатых клавиш
                            chord_string = chord_string + " " + decode_note(int(n))
                            chord_id = chord_id * 0x100 + int(n)
                        if not chords_equal:
                            if self.debug:
                                print(f"Время: {time}; "
                                      f"Аккорд: {chord_string}; "
                                      f"Код пред. вершины: {hex(prev_chord_id)}; "
                                      f"Код вершины: {chord_id}; "
                                      f"Номера клавиш: {sorted_prev_chord}-->{sorted_chord}")
                            df.loc[len(df)] = [str(prev_chord_id), str(chord_id), "", "", int(message.time)]  # добавление в словарь df pandas атрибуты id предыдущего аккорда, id нового аккорда и время между аккордами
                            # Сохраним предыдущий аккорд, чтобы pandas df сформировалась правильно
                            prev_chord_id = chord_id
                            # предыдущий акк становится пред-предыдущим, а текущий = предыдущий (т.е. сдвигаем время)
                            chord_sequence = shift(chord, chord_sequence)
                    time += message.time  # обновляем общее время трека и переходим к новому событию
                    # Добавим запись в таблицу о переходе между аккордами
                if message.type == "note_on" and message.note not in chord:
                    # если ноты нет в акк, но событие "нажата", то ноту добавляем в акк
                    chord += (message.note,)
                elif message.type == "note_off" and message.note in chord:
                    # если нота "отпущена", то удаляем из нынешнего акк.
                    # Определим индекс элемента в списке нот аккорда
                    note_index = chord.index(message.note)  # %12
                    # Удалим ноту из аккорда, если событие - "отпущена"
                    chord = chord[: note_index] + chord[note_index + 1:]

        df.loc[len(df)] = [str(chord_id), str(self.terminator), "", "", int(0)]
        return df

    def merge_tracks(self, df: pd.DataFrame, midi_source_path: pathlib.Path, result_path: pathlib.Path) -> pd.DataFrame:
        """
        Объединение треков и формирование последовательности аккордов. Результат записываем в pandas DataFrame
        :param df: фрейм в формате {"from", "to", "crc32", "atr"}, в который записывается граф де Брюйна
        :param midi_source_path: исходный файл (удалить)
        :param result_path: сохраненный миди с аккордами для контроля результатов
        :rtype: pd.DataFrame
        """
        # создаем переменную mid - наш миди-файл типа 1 (когда треки НЕ смерджены в одну дорожку)
        mid = MidiFile(midi_source_path, clip=True)
        if mid.type > 1:
            sys.exit("MIDI file should have Type 1 (all trackes should start sunchronously)")
            # TODO: если будут midi.type 2, то можно будет попробовать что-то с этим сделать через midi-библиотеку
            # print(f"MIDI has type {mid.type}!")
            # return

        merged_track = self._merge_track(mid)

        # Создаем новую переменную для обозначения миди-файла
        merged_mid = MidiFile()
        # Темп всегда 120 ударов на четверть (quarter note)
        merged_mid.ticks_per_beat = 120
        # Создаем первый и единственный трек
        merged_mid.add_track('Acoustic Grand Piano')
        # Добавляем объединённый трек
        self._track_append(merged_mid.tracks[0], merged_track)
        # Сохраним объединенный midi в переменную merged_mid
        merged_mid.save(result_path)

        return self._process_midi_file(mid, df)
