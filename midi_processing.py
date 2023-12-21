import binascii
import fnmatch
import os
import pickle
import time
from pathlib import Path
from threading import Condition, Thread

import pandas as pd
from music21.converter import parse as music21_parse

from chord_processing import ChordProcessor


def get_filtered_files(folder_path: Path, pattern: str) -> list[Path]:
    result = []
    for dirpath, dirnames, filenames in os.walk(folder_path):
        result.extend(folder_path / dirpath / Path(filepath) for filepath in fnmatch.filter(filenames, pattern))
    return result


class MidiProcessor:
    def __init__(self, chord_processor: ChordProcessor, vertex_dictionary_file: Path, maximum_threads=20):
        self.chord_processor = chord_processor
        self.maximum_threads = maximum_threads  # количество потоков обработки

        self.vertex_dictionary = {str(binascii.crc32(str(chord_processor.terminator).encode('utf8'))): str(chord_processor.terminator)}

        # многопоточность ускоряет обработку файлов
        self.ack_signal = Condition()
        self.running_threads = 0  # разделяемая переменная для синхронизации потоков

        # Словарь вершин графа в формате: индекс вершины, полученный как crc32 аккордов | код вершины
        self.vertex_dictionary_file = vertex_dictionary_file
        try:
            with open(vertex_dictionary_file, 'rb') as fp:
                self.vertex_dictionary = pickle.load(fp)
        except FileNotFoundError or pickle.PickleError:
            print(f"Create dictionary file {vertex_dictionary_file}")

    def process(self, midi_filepaths: list[Path], result_dir: Path):
        for midi_filepath in midi_filepaths:
            assert midi_filepath.exists()

        assert result_dir.exists() and result_dir.is_dir()

        # Формируем потоки
        threads = []
        for index, midi_filepath in enumerate(midi_filepaths):
            mid2graph_thread = Thread(target=self._mid2graph, args=(midi_filepath, result_dir, index))
            threads.append(mid2graph_thread)

        # Запуск потоков обработки (не более running_thread)
        for mid2graph_thread in threads:
            while self.running_threads >= self.maximum_threads:
                time.sleep(1.0)
            self.ack_signal.acquire()
            self.running_threads += 1
            self.ack_signal.release()
            mid2graph_thread.start()

        # Ожидание завершения всех потоков
        for mid2graph_thread in threads:
            mid2graph_thread.join()

        #Сохранение словаря
        with open(self.vertex_dictionary_file, 'wb') as file:
            pickle.dump(self.vertex_dictionary, file, protocol=pickle.HIGHEST_PROTOCOL)

    def _get_unique_index(self, vertex) -> tuple:
        """
        Формирование словаря уникальных кодов вершин
        """

        crc32 = str(binascii.crc32(vertex.encode('utf8')))
        if crc32 in self.vertex_dictionary.keys() and vertex != self.vertex_dictionary[crc32]:
            return self._get_unique_index(vertex + " ")
        return crc32, vertex

    def _mid2graph(self, mid_filepath: Path, result_dir: Path, file_num: int):
        try:
            df = pd.DataFrame(columns=['from', 'to', 'from_crc32', 'to_crc32', 'atribute'])
            print(f"Converting file #{file_num}: {mid_filepath}")

            # Мерджим трек и разбираем последовательность аккордов в пакеты для графа деБрюйна
            df = self.chord_processor.merge_tracks(df, mid_filepath, result_dir / mid_filepath.name)

            # определяем тональность и добавляем к имени файлв
            score = music21_parse(mid_filepath)
            key = score.analyze('TemperleyKostkaPayne')

            pcl_out_filename = result_dir / (Path(mid_filepath).stem + f"_{key.tonic.name}_{key.mode}.pcl{self.chord_processor.L}")
            # Вставка в общий словарь найденных аккордов
            try:
                self.ack_signal.acquire()
                for index, row in df.iterrows():
                    (from_vertex_crc32, from_vertex) = self._get_unique_index(row['from'])
                    (to_vertex_crc32, to_vertex) = self._get_unique_index(row['to'])
                    df.loc[index, 'from'] = from_vertex
                    df.loc[index, 'to'] = to_vertex
                    df.loc[index, 'from_crc32'] = from_vertex_crc32
                    df.loc[index, 'to_crc32'] = to_vertex_crc32
                    self.vertex_dictionary[to_vertex_crc32] = to_vertex
                # Сохраняем в файл
                print(f"Write to file #{file_num} : {pcl_out_filename}")
                df.to_pickle(pcl_out_filename)
            except Exception as error:
                print(f"Проблемы с формированием dic и pcl файлов для {pcl_out_filename} {type(error).__name__}: {error}")
            finally:
                self.ack_signal.release()
        except Exception as error:
            print(f"Не удалось обработать файл {mid_filepath}", type(error).__name__, "–", error)
        finally:
            self.ack_signal.acquire()
            self.running_threads = self.running_threads - 1
            self.ack_signal.release()