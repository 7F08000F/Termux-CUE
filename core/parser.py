# core/parser.py

import re
import os
import subprocess
import shutil

class CueParser:
    """
    一个CUE解析器，支持从外部.cue文件或内嵌的CUE数据进行解析。
    能正确处理专辑/音轨RG标签和各种格式的FILE指令。
    """
    def __init__(self, file_path=None, content_lines=None):
        if file_path and not content_lines:
            if not os.path.exists(file_path): raise FileNotFoundError(f"错误：文件未找到 -> {file_path}")
            self.file_path = file_path
            self.cue_content_lines = self._load_from_file()
        elif content_lines:
            self.file_path = "embedded.cue"
            self.cue_content_lines = content_lines
        else:
            raise ValueError("CueParser 必须接收 file_path 或 content_lines 参数之一。")
        self.album_data = {}
        self.tracks_data = []

    def _load_from_file(self):
        for enc in ['utf-8', 'gbk', 'shift_jis', 'latin-1']:
            try:
                with open(self.file_path, 'r', encoding=enc) as f: return f.readlines()
            except UnicodeDecodeError: continue
        with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f: return f.readlines()

    def _parse_time(self, t):
        try: m, s, f = map(int, t.split(':')); return m * 60 + s + f / 75.0
        except (ValueError, IndexError): return 0.0

    def parse(self):
        if not self.cue_content_lines: return {}, []
        current_track_info = None
        for line in self.cue_content_lines:
            line = line.strip();
            if not line: continue
            match = re.match(r'^\s*(\w+)\s*(.*)', line, re.IGNORECASE)
            if not match: continue
            command, args = match.groups(); command = command.upper()
            if command == 'TRACK':
                if current_track_info: self.tracks_data.append(current_track_info)
                track_num_str = re.match(r'(\d+)', args)
                if track_num_str: current_track_info = {'number': int(track_num_str.group(1)), 'performer': self.album_data.get('performer'), 'title': f"音轨 {int(track_num_str.group(1))}"}
            elif command == 'FILE':
                if current_track_info is None:
                    quoted_match = re.search(r'"([^"]+)"', args)
                    if quoted_match:
                        self.album_data['file'] = quoted_match.group(1)
                    # [Debug] 增加对空参数的检查，防止IndexError
                    elif args.split():
                        self.album_data['file'] = args.split()[0]
            elif command == 'TITLE':
                stripped_args = args.strip('"')
                if current_track_info: current_track_info['title'] = stripped_args
                else: self.album_data['title'] = stripped_args
            elif command == 'PERFORMER':
                stripped_args = args.strip('"')
                if current_track_info: current_track_info['performer'] = stripped_args
                else: self.album_data['performer'] = stripped_args
            elif command == 'INDEX':
                if current_track_info and args.startswith('01') and len(args.split()) > 1:
                    current_track_info['start_time'] = self._parse_time(args.split()[1])
            elif command == 'REM':
                rem_parts = re.split(r'\s+', args, 1)
                if len(rem_parts) == 2:
                    rem_key, rem_value = rem_parts[0].upper(), rem_parts[1]
                    if rem_key.startswith('REPLAYGAIN_'):
                        if current_track_info and 'TRACK' in rem_key: current_track_info[rem_key.lower()] = rem_value
                        elif not current_track_info and 'ALBUM' in rem_key: self.album_data[rem_key.lower()] = rem_value
        if current_track_info: self.tracks_data.append(current_track_info)
        if not self.tracks_data: return self.album_data, self.tracks_data
        for i in range(len(self.tracks_data)):
            if i + 1 < len(self.tracks_data): self.tracks_data[i]['end_time'] = self.tracks_data[i+1]['start_time']
            else: self.tracks_data[i]['end_time'] = None
        return self.album_data, self.tracks_data
