# Функция определяет минимальный номер голоса, в котором нет активной ноты
import os
import pickle
from pathlib import Path

import pandas as pd
from mido import MidiFile, Message, UnknownMetaMessage

from .gpc_wrapper import GPCWrapper
from .stylizing import PerformerWrapper
from .MidiToMp3Converter import MidiToMp3Converter


def get_files_with_params(from_path: Path, tonality: str, L: int) -> list[Path]:
    """
    Возвращает список файлов из папки `from_path`,
    у которых тональность `tonality` и количество аккордов для кода вершины `L`.
    Используется фильтр по маске f'{tonality}.pcl{L}'

    :param from_path: Путь к папке, из файлов которой будет поиск
    :param tonality: параметр тональности
    :param L: параметр L (количество аккордов для кода вершины)
    :return: Список файлов с искомыми параметрами
    """
    assert from_path.exists() and from_path.is_dir()
    file_mask = f'{tonality}.pcl{L}'
    return [from_path / Path(file_name) for file_name in os.listdir(from_path) if file_name.endswith(file_mask)]


def combine_pickle_files(files: list[Path], output_filepath: Path | None = None) -> pd.DataFrame:
    """ Функция объединяет несколько pcl файлов в один (полезно, когда в библиотеке много малых pcl файлов) """
    df = pd.DataFrame(columns=['from', 'to', 'from_crc32', 'to_crc32', 'atribute'])  # Initialize an empty DataFrame to store the merged data

    for filepath in files:
        try:
            with open(filepath, 'rb') as f:
                content = pickle.load(f)
                if isinstance(content, pd.DataFrame):
                    df = pd.concat([df, content], ignore_index=True)
        except Exception as e:
            print(f"Pcl file {filepath} error: {e}")

    if output_filepath:
        with open(output_filepath, 'wb') as out:
            pickle.dump(df, out, protocol=pickle.HIGHEST_PROTOCOL)

    return df


def apply_instruments_table(instruments_table, input_mid):
    # Распределяем по инструменам стилизованный миди
    result_mid = MidiFile()
    result_mid.ticks_per_beat = 120
    result_mid.type = 1
    print("Instruments:")
    instrument_count, nu = instruments_table.shape
    for i in range(instrument_count):
        print("\t", instruments_table.item((i, 0)))
        result_mid.add_track(instruments_table.item((i, 0)))
        # Set the instrument
        result_mid.tracks[i].append(Message('control_change', control=0, value=0x00, channel=i, time=0))
        result_mid.tracks[i].append(Message('control_change', control=32, value=0x00, channel=i, time=0))
        result_mid.tracks[i].append(Message('program_change', program=int(instruments_table.item((i, 1))), channel=i, time=0))  # MIDI file contain instruments from #0

    # используем время для отсчета событий
    last_event_time = [0] * instrument_count  # начальное время инструмента = 0
    time = 0  # Время нарастающим итогом для отсчета интервалов

    # Делим единый трек на инструменты
    for track in input_mid.tracks:
        for msg in track:
            time += msg.time
            for i in range(instrument_count):
                if msg.type in ("note_on", "note_off"):
                    if int(instruments_table.item((i, 2))) <= msg.note <= int(instruments_table.item((i, 3))):
                        result_mid.tracks[i].append(Message(msg.type, channel=i, note=msg.note, velocity=msg.velocity, time=time - last_event_time[i]))
                        last_event_time[i] = time

    for i in range(instrument_count):
        result_mid.tracks[i].append(UnknownMetaMessage(type_byte=123, time=0))  # MetaMessage('end_of_track')

    return result_mid


