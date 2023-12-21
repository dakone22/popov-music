import sys
from collections import defaultdict
from pathlib import Path

from mido import MidiFile, Message, UnknownMetaMessage


class PerformerWrapper:
    def __init__(self, style_performer_path: Path, midi_style: Path):
        assert style_performer_path.exists()

        self.style_performer_path = style_performer_path

        sys.path.insert(0, f"{style_performer_path}/src")  # TODO: некостыльный динамичный import
        from performance.performer import Performer

        self.performer_cls = Performer
        self.midi_style = midi_style

    def stylize(self, mono_mid, voice_count):
        # Запускаем перенос стиля
        mono_mid_styled = []  # массив одноголосных миди со стилем
        for i in range(voice_count):
            mono_mid_styled.append(MidiFile())
            mono_mid_styled[i].ticks_per_beat = 120
            mono_mid_styled[i].type = 1
            mono_mid_styled[i].add_track('Acoustic Guitar')

        p = self.performer_cls()  # класс-фасад
        p.compile(f"{self.style_performer_path}/config/config_0025", 'config.json')  # загрузить конфигурацию
        style = MidiFile(self.midi_style)
        for i in range(voice_count):
            if len(mono_mid[i].tracks[0]) > 128:
                print(f"Стилизация голоса {i + 1} из {voice_count}")
                mono_mid_styled[i] = p.style(mono_mid[i], style, A=30, B=1, stride=1, dt_max=0.0, verbose=1, timelimit=1200)  # стилизация
            else:
                print(f"Голос {i} не поддается стилизации")
                mono_mid_styled[i] = mono_mid[i]

        # Собираем все midi вместе в многоголосный трек
        return merge_tracks(mono_mid_styled, voice_count)


def merge_tracks(mid_array, voice_count):
    """ Функция для объединения треков в едином масштабе времени """
    # Объединение треков нескольких объектов MidFile в один трек
    # Запишем время события из каждого трека в dictionary, каждый элемент которого - массив сообщений в данный момент времени
    merged_track = defaultdict(list)
    # Для каждого миди
    for voice in range(voice_count):
        mid = mid_array[voice]
        # Для каждого трека
        for track in mid.tracks:
            time = 0  # Время нарастающим итогом т.е. рассматриваем разницу между нотами, а не время с самого начала
            # Для каждого сообщения в треке
            for message in track:
                # Если сообщение о нажатии или отпускании ноты
                if message.type in ("note_on", "note_off"):
                    message_type = message.type
                    # Если time_on и velocity=0, то это событие равнозначно note_off
                    if message.type == "note_on" and message.velocity == 0:
                        message_type = "note_off"

                    # Рассчитаем текущий момент времени для нового сообщения
                    time += message.time

                    if message.channel != 10: # Дизейблим перкуссию (10 канал по стандарту МИДИ используется для перкуссии.)
                        merged_track[time].append({'type': message_type, 'note': message.note, 'velocity': message.velocity})

    # Создаем новую переменную для обозначения миди-файла
    merged_mid = MidiFile()
    # Вычисляем масштаб для пересчета темпа
    ticks_per_beat_factor = set_time_factor(mid.ticks_per_beat)
    # if debug: print(f'Исходная композиция. Количество тиков в четверти: {mid.ticks_per_beat}')
    # Темп всегда 120 ударов на целую ноту
    merged_mid.ticks_per_beat = 120
    # Создаем первый и единственный трек
    merged_mid.add_track('Acoustic Grand Piano')
    time = 0  # Начальная метка времени
    # Для каждого момента времени в словаре (сортируем его по времени), записываем событие в midi.
    for moment in sorted(merged_track.keys()):
        # if debug: print(moment, ':', merged_track[moment])
        # Рассчитаем промежуток времени от предыдущего события
        delta_t = int(moment - time)
        # Перебираем все сообщения в текущем моменте времени
        for event in merged_track[moment]:
            # Формируем сообщение в формате midi
            merged_mid.tracks[0].append(Message(event.get('type'), note=event.get('note'), velocity=event.get('velocity'), time=retime(delta_t, ticks_per_beat_factor)))
            # Все остальные события в этот момент времени имеют delta_t=0
            delta_t = 0
        # Переключаем момент времени на новую отметку
        time = moment

    merged_mid.tracks[0].append(UnknownMetaMessage(type_byte=123, time=0))  # MetaMessage('end_of_track')

    return merged_mid


def retime(time, ticks_per_beat_factor):
    """ Функция для пересчета времени в соответствии с масштабом трека """
    return int(time / ticks_per_beat_factor)


def set_time_factor(tempo):
    """ Функция для задания нового масштаба времени по информации из трека """
    # global debug
    ticks_per_beat_factor = tempo / 120.0
    # if debug: print("ticks_per_beat_factor=", ticks_per_beat_factor)
    return ticks_per_beat_factor
