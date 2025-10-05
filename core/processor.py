# core/processor.py

import os
import re
import subprocess
import shutil
import traceback
import sys
from multiprocessing import Pool, cpu_count
from mutagen.flac import FLAC; from mutagen.mp3 import MP3; from mutagen.id3 import ID3, TXXX, TIT2, TPE1, TALB, TRCK, TPE2

SUPPORTED_AUDIO_EXTENSIONS = ['.flac', '.wav', '.mp3', '.ape', '.wv', '.tak']

def init_worker():
    """
    初始化子进程，将其标准输入重定向到 /dev/null。
    这是为了防止子进程（尤其是ffmpeg）意外地“挂起”或“劫持”主进程的终端输入，
    从而根除在并发任务后主进程 input() 无响应的问题。
    """
    sys.stdin = open(os.devnull)

class AudioProcessor:
    def __init__(self, output_dir=None):
        self.output_dir = output_dir
    def check_dependencies(self):
        if not shutil.which("ffmpeg"): raise EnvironmentError("错误：未找到 'ffmpeg'。请通过 'pkg install ffmpeg' 安装。")

    def _write_tags(self, file_path, track_info, album_info, total_tracks):
        # ... (此方法与 v3.0 相同)
        file_ext = os.path.splitext(file_path)[1].lower();
        if file_ext == '.wav' or file_ext not in ['.flac', '.mp3']: return
        audio = None
        try:
            if file_ext == '.flac': audio = FLAC(file_path)
            elif file_ext == '.mp3': audio = MP3(file_path, ID3=ID3); audio.add_tags() if audio.tags is None else None
        except Exception as e: raise type(e)(f"mutagen 打开文件失败: {e}")
        if not audio: return
        if file_ext == '.flac':
            audio['title'] = track_info.get('title', ''); audio['artist'] = track_info.get('performer', ''); audio['album'] = album_info.get('title', ''); audio['albumartist'] = album_info.get('performer', ''); audio['tracknumber'] = f"{track_info.get('number')}/{total_tracks}"
            if 'replaygain_album_gain' in album_info: audio['replaygain_album_gain'] = album_info['replaygain_album_gain']
            if 'replaygain_album_peak' in album_info: audio['replaygain_album_peak'] = album_info['replaygain_album_peak']
            if 'replaygain_track_gain' in track_info: audio['replaygain_track_gain'] = track_info['replaygain_track_gain']
            if 'replaygain_track_peak' in track_info: audio['replaygain_track_peak'] = track_info['replaygain_track_peak']
        elif file_ext == '.mp3':
            audio['TIT2'] = TIT2(encoding=3, text=track_info.get('title', '')); audio['TPE1'] = TPE1(encoding=3, text=track_info.get('performer', '')); audio['TALB'] = TALB(encoding=3, text=album_info.get('title', '')); audio['TPE2'] = TPE2(encoding=3, text=album_info.get('performer', '')); audio['TRCK'] = TRCK(encoding=3, text=f"{track_info.get('number')}/{total_tracks}")
            if 'replaygain_album_gain' in album_info: audio.tags.add(TXXX(encoding=3, desc='REPLAYGAIN_ALBUM_GAIN', text=album_info['replaygain_album_gain']))
            if 'replaygain_album_peak' in album_info: audio.tags.add(TXXX(encoding=3, desc='REPLAYGAIN_ALBUM_PEAK', text=album_info['replaygain_album_peak']))
            if 'replaygain_track_gain' in track_info: audio.tags.add(TXXX(encoding=3, desc='REPLAYGAIN_TRACK_GAIN', text=track_info['replaygain_track_gain']))
            if 'replaygain_track_peak' in track_info: audio.tags.add(TXXX(encoding=3, desc='REPLAYGAIN_TRACK_PEAK', text=track_info['replaygain_track_peak']))
        audio.save()

    def _process_single_track(self, args):
        track_info, album_info, input_file, output_dir, total_tracks = args
        log_prefix = f"[音轨 {track_info['number']:02d}]"
        try:
            file_extension = os.path.splitext(input_file)[1].lower(); safe_track_title = re.sub(r'[\\/*?:"<>|]', "_", track_info['title']); output_filename = f"{track_info['number']:02d} - {safe_track_title}{file_extension}"; output_filepath = os.path.join(output_dir, output_filename)
            command = ["ffmpeg", "-i", input_file, "-ss", str(track_info['start_time'])]
            if track_info.get('end_time') is not None: command.extend(["-to", str(track_info['end_time'])])
            command.extend(["-map_metadata", "-1", "-y", output_filepath])
            subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8', errors='ignore', stdin=subprocess.DEVNULL)
            self._write_tags(output_filepath, track_info, album_info, total_tracks)
            return (True, None)
        except subprocess.CalledProcessError as e:
            return (False, f"{log_prefix} 处理失败。FFmpeg 返回错误:\n--- FFmpeg Stderr ---\n{e.stderr.strip()}\n-----------------------")
        except Exception:
            return (False, f"{log_prefix} 处理时发生未知错误:\n{traceback.format_exc()}")

    def process_album(self, album_data, tracks_data, source_dir, use_track_concurrency):
        album_title = re.sub(r'[\\/*?:"<>|]', "_", album_data.get('title', 'Untitled Album')); output_path = self.output_dir if self.output_dir else os.path.join(source_dir, album_title); os.makedirs(output_path, exist_ok=True)
        print(f"-> 输出目录: {output_path}")
        input_audio_file = os.path.join(source_dir, album_data['file'])
        
        # [Debug] 增加对空音轨列表的检查，防止IndexError
        if not tracks_data:
            return (False, "错误：音轨列表为空，无法处理。")
        
        res = subprocess.run(["ffmpeg", "-i", input_audio_file], capture_output=True, text=True, encoding='utf-8', errors='ignore', stdin=subprocess.DEVNULL)
        dur_match = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})", res.stderr)
        if dur_match: h, m, s, hs = map(int, dur_match.groups()); tracks_data[-1]['end_time'] = h * 3600 + m * 60 + s + hs / 100.0
        
        tasks_args = [(track, album_data, input_audio_file, output_path, len(tracks_data)) for track in tracks_data]
        results = []
        if use_track_concurrency and len(tracks_data) > 1:
            cores = min(cpu_count(), len(tracks_data), 4); print(f"-> 启用音轨并发处理，使用 {cores} 个核心...")
            with Pool(processes=cores, initializer=init_worker) as pool: results = pool.map(self._process_single_track, tasks_args)
        else:
            print(f"-> 启用音轨顺序处理 (共 {len(tracks_data)} 个音轨)...")
            for i, args in enumerate(tasks_args):
                print(f"  - 处理音轨 {i+1}/{len(tracks_data)}: {args[0]['title']}...")
                results.append(self._process_single_track(args))
        failed_tracks = [res[1] for res in results if not res[0]]
        if failed_tracks:
            return (False, "\n".join(failed_tracks))
        return (True, None)
