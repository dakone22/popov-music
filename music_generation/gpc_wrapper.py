import binascii
import sys
from array import array
from pathlib import Path

import pandas as pd
from gpc64io.base import GPC
from mido import MidiFile, Message


class GPCWrapper:
    def __init__(self, sw_kernel_path: Path, handlers_path: Path, terminator: int, debug=False):
        assert sw_kernel_path.exists() and handlers_path.exists()

        self.sw_kernel_path = sw_kernel_path
        self.handlers_path = handlers_path

        self.terminator = terminator
        self.terminator_crc32 = int(binascii.crc32(str(self.terminator).encode('utf8'))) # Код терминальной вершины
        
        self.debug = debug

        self.gpc: GPC | None = None

    def init(self):
        # Получить доступ к свободному gpc
        self.gpc = GPC()
        print(self.gpc.dev_path)

        # Загрузить sw_kernel
        if self.gpc.load_swk(str(self.sw_kernel_path)) != 0:
            print("Error when loading sw_kernel file {SW_KERNEL_PATH}")

        # Загрузить номера и имена handlers из файла
        self.gpc.def_handlers(str(self.handlers_path))
        print(self.gpc.handlers)

        self.vertex_dictionary = {}
        self.edges_count = 0
        self.max_voices = 0

    def insert_graph(self, df: pd.DataFrame):
        # Массив для передачи в gpc
        edge_array = array('Q', [0] * 3 * len(df))
        # Словарь вершин графа в формате: индекс вершины, полученный как crc32 аккордов | код вершины
        # Создадим словарь кодов вершин и запишем индексную часть графа ДеБрюйна в gpc
        for index, row in df.iterrows():
            edge_array[3 * index] = int(row['from_crc32'])
            edge_array[3 * index + 1] = int(row['to_crc32'])
            edge_array[3 * index + 2] = int(row['atribute'])
            self.vertex_dictionary[int(row['from_crc32'])] = int(row['from'])
            self.vertex_dictionary[int(row['to_crc32'])] = int(row['to'])

        # Запускаем обработчик
        self.gpc.start_handler("insert_edges")
        self.gpc.mq_send_uint64(len(df))  # передаем количество ребер
        # Посылаем массив в gpc
        edge_bytearray = bytearray(edge_array)
        write_thread = self.gpc.mq_send_buf(edge_bytearray)

        # Ждем завершения записи
        self.gpc.join(write_thread)

    def _start_random_traversal(self, count, start_vertex):
        """ Функция обхода графа по случайному пути """
        self.gpc.start_handler("get_random_vertices")
        self.gpc.mq_send_uint64(int(count))  # послать количество вершин
        self.gpc.mq_send_uint64(int(start_vertex))  # послать стартовую вершину

    def _get_random_edge(self):
        u, v, time, adj_c = (self.gpc.mq_receive_uint64() for _ in range(4))

        if self.debug:
            print(f"{u}:{v}:{time}:{adj_c}")

        return u, v, time, adj_c

    def _get_free_voice(self, voices):
        i = 0
        while voices[i] != 1:
            i += 1
        if self.max_voices < i:
            self.max_voices = i
        voices[i] = 0
        return i

    def generate_midi(self, chord_count, max_voice_count):
        # Начать обход графа
        self._start_random_traversal(chord_count, self.terminator_crc32)

        # Создаем новые миди
        origin_mid = MidiFile()
        origin_mid.ticks_per_beat = 120
        origin_mid.type = 1
        origin_mid.add_track('Acoustic Guitar')
        mono_mid = []  # массив одноголосных миди
        for i in range(max_voice_count):
            mono_mid.append(MidiFile())
            mono_mid[i].ticks_per_beat = 120
            mono_mid[i].type = 1
            mono_mid[i].add_track('Acoustic Guitar')
        chord = {}  # словарь нот аккорда в формате нота:голос для разделения на много одноголосных партий
        # Массив, сохраняющий время последнего события в голосе
        last_event_time = []
        # Массив с состоянием голосов (1 - свободен, 0 - занят)
        free_voices = []
        for i in range(max_voice_count):
            last_event_time.append(0)  # начальное время голоса = 0
            free_voices.append(1)  # массив для поиска свободного голоса
        from_chord = str(self.terminator)  # Начальная вершина всегда 0
        cur_chord_delay = 0  # Время, для измерения длительности предыдущего аккорда надо сохранить
        prev_chord_delay = 0  # Время, для измерения длительности текущего аккорда
        global_time = 0  # Время нарастающим итогом для отсчета интервалов
        delay_for_origin = 0  # Время для оригинальной композиции
        delay = []
        for i in range(0, max_voice_count):
            delay.append(0)  # задержка для каждого голоса отсчитывается именно от его событий, а не ото всех событий треков

        for chord_number in range(chord_count):  # Возьмем много аккордов
            current_chord = ()  # Код текущего аккорда пока пустой
            prev_chord = ()  # Код предыдущего аккорда пока пустой

            # Получим очередное ребро из gpc
            from_crc32, to_crc32, edge_atr, adjacency_list_count = self._get_random_edge()
            if self.debug:
                print(from_crc32, to_crc32, edge_atr, adjacency_list_count)

            if from_crc32 not in self.vertex_dictionary.keys():
                print("From_chord: В словаре нет информации о вершине c кодом " + str(from_crc32))
                sys.exit()

            if to_crc32 not in self.vertex_dictionary.keys():
                print("From_chord: В словаре нет информации о вершине c кодом " + str(to_crc32))
                sys.exit()

            from_chord = self.vertex_dictionary[from_crc32]
            to_chord = self.vertex_dictionary[to_crc32]

            # разбираем номер вершины на предыдущий и текущий аккорды, шаблон - 0xE0123f4567
            if self.debug:
                print(hex(int(from_chord)))

            i = int(from_chord)
            if i != self.terminator:
                self.edges_count += adjacency_list_count
                while i % 0x100 != 0xF:
                    current_chord = current_chord + (int(i % 0x100),)
                    i //= 0x100
                i //= 0x100
                while i % 0x100 != 0xE and i % 0x100 != 0xF:
                    prev_chord = prev_chord + (int(i % 0x100),)
                    i //= 0x100
                delay_for_origin = 0
                global_time += prev_chord_delay  # текущий момент времени от начала пьесы
                for i in range(0, max_voice_count):
                    delay[i] = global_time - last_event_time[i]  # задержка для каждого голоса отсчитывается именно от его событий, а не ото всех событий треков
                delay_for_origin = prev_chord_delay
                if int(to_chord) == self.terminator or chord_number == chord_count - 1:
                    # прекратить звучание всех аккордов
                    for msg, voice in chord.items():
                        origin_mid.tracks[0].append(Message('note_off', channel=0, note=msg, velocity=72, time=delay_for_origin))
                        # Завершаем всех одновременно, а не в соответствии delay[voice]))
                        mono_mid[voice].tracks[0].append(Message('note_off', channel=0, note=msg, velocity=72, time=delay[voice]))
                        free_voices[chord[msg]] = 1
                        last_event_time[
                            voice] = global_time  # время последнего событие для голоса используется для следующей задержки (см. маны и доки по MIDI :)
                        delay_for_origin = 0
                        delay[voice] = 0
                    chord = {}
                else:
                    for msg in prev_chord:
                        if msg not in current_chord and msg in chord:
                            origin_mid.tracks[0].append(Message('note_off', channel=0, note=msg, velocity=72, time=delay_for_origin))
                            mono_mid[chord[msg]].tracks[0].append(Message('note_off', channel=0, note=msg, velocity=72, time=delay[chord[msg]]))
                            free_voices[chord[msg]] = 1
                            delay[chord[
                                msg]] = 0  # все остальные события в этом треке для перехода между аккордами случаются в то-же время
                            delay_for_origin = 0
                            last_event_time[chord[
                                msg]] = global_time  # время последнего событие для голоса используется для следующей задержки
                    for msg in prev_chord:  # проходим второй раз и удаляем, т.к. может быть две ноты в разных треках
                        if msg not in current_chord and msg in chord:
                            del chord[msg]
                    for msg in current_chord:
                        if msg not in prev_chord:
                            chord[msg] = self._get_free_voice(free_voices)
                            origin_mid.tracks[0].append(Message('note_on', channel=0, note=msg, velocity=72, time=delay_for_origin))
                            mono_mid[chord[msg]].tracks[0].append(Message('note_on', channel=0, note=msg, velocity=72, time=delay[chord[msg]]))
                            last_event_time[chord[msg]] = global_time  # время последнего событие для голоса используется для следующей задержки
                            delay[chord[msg]] = 0

            from_chord = to_chord  # перейдем к следующей вершине
            prev_chord_delay = cur_chord_delay  # текужий аккорд становится предыдущим, сохраним время его звучания
            cur_chord_delay = edge_atr  # сохраним время действия текущего аккорда

        return self.edges_count, origin_mid, mono_mid

    def run(self, df: pd.DataFrame, chord_count, max_voice_count):
        """ init, insert, generate and delete """
        self.init()
        self.insert_graph(df)
        result = self.generate_midi(chord_count, max_voice_count)
        del self.gpc

        return result
