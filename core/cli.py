# core/cli.py

import os
import sys
import textwrap
import shlex
import re
import multiprocessing
from multiprocessing import Pool
import traceback
from collections import defaultdict
from mutagen.flac import FLAC, FLACNoHeaderError

from .parser import CueParser
from .processor import AudioProcessor, SUPPORTED_AUDIO_EXTENSIONS, init_worker

class TermuxColors:
    HEADER = '\033[95m'; OKBLUE = '\033[94m'; OKCYAN = '\033[96m'; OKGREEN = '\033[92m'; WARNING = '\033[93m'; FAIL = '\033[91m'; ENDC = '\033[0m'; BOLD = '\033[1m'; UNDERLINE = '\033[4m'

# [Debug] 改进 worker 的错误返回信息
def process_album_worker(args):
    file_path, use_track_concurrency = args
    task_name = os.path.basename(file_path)
    log_prefix = f"[P{os.getpid()}:{task_name}]"
    print(f"{log_prefix} 开始处理...")
    try:
        processor = AudioProcessor()
        album_data, tracks_data = {}, []
        cue_content_lines = []

        # 阶段 1: 解析
        if file_path.lower().endswith('.flac'):
            try:
                audio = FLAC(file_path)
                if audio.cuesheet: cue_content_lines = audio.cuesheet.text
                elif 'cuesheet' in audio: cue_content_lines = audio['cuesheet'][0].splitlines()
                if cue_content_lines:
                    parser = CueParser(content_lines=cue_content_lines)
                    album_data, tracks_data = parser.parse()
                    album_data['file'] = os.path.basename(file_path)
                else:
                    return (False, task_name, "解析失败：FLAC文件不包含任何形式的内嵌CUE表。")
            except FLACNoHeaderError:
                return (False, task_name, "解析失败：文件不是有效的FLAC。")
        else:
            parser = CueParser(file_path=file_path)
            album_data, tracks_data = parser.parse()
        if not tracks_data: return (False, task_name, f"解析失败：未能从 '{task_name}' 解析出任何音轨。")
        
        # 阶段 2: 音频文件匹配
        source_dir = os.path.dirname(file_path); audio_file_path = ""
        expected_filename = album_data.get('file', '')
        if expected_filename and os.path.isfile(os.path.join(source_dir, expected_filename)):
            audio_file_path = os.path.join(source_dir, expected_filename)
        else:
            cue_basename = os.path.splitext(task_name)[0]
            candidates = [f for f in os.listdir(source_dir) if os.path.splitext(f)[0] == cue_basename and os.path.splitext(f)[1].lower() in SUPPORTED_AUDIO_EXTENSIONS]
            if len(candidates) == 1: audio_file_path = os.path.join(source_dir, candidates[0])
            else: return (False, task_name, f"音频匹配失败：未能找到唯一的匹配音频文件 (找到 {len(candidates)} 个)。")
        album_data['file'] = os.path.basename(audio_file_path)
        
        # 阶段 3: 执行处理
        success, error_message = processor.process_album(album_data, tracks_data, source_dir, use_track_concurrency)
        
        if success:
            print(f"{log_prefix} {TermuxColors.OKGREEN}处理成功！{TermuxColors.ENDC}"); return (True, task_name, None)
        else:
            return (False, task_name, f"{log_prefix} 分割处理失败:\n{error_message}")
    except Exception:
        return (False, task_name, f"发生未知严重错误:\n{traceback.format_exc()}")

class Cli:
    def __init__(self, script_root):
        self.script_root_dir = script_root; self.processor = AudioProcessor()

    def display_welcome(self):
        print(f"{TermuxColors.BOLD}{TermuxColors.OKGREEN}=================================================={TermuxColors.ENDC}")
        print(f"{TermuxColors.BOLD}{TermuxColors.OKGREEN}     Termux CUE 整轨无损分割工具 v5.1 {TermuxColors.ENDC}")
        print(f"{TermuxColors.BOLD}{TermuxColors.OKGREEN}=================================================={TermuxColors.ENDC}")
        print(f"欢迎使用！ {TermuxColors.OKCYAN}这是一个稳定版本。{TermuxColors.ENDC}")

    def _has_embedded_cue(self, flac_path):
        try:
            audio = FLAC(flac_path)
            return bool(audio.cuesheet or 'cuesheet' in audio)
        except (FLACNoHeaderError, Exception):
            return False

    def _scan_and_get_tasks(self):
        all_tasks = []; print("\n请选择扫描范围:"); print(f"  [1] 脚本目录  [2] 内置存储根目录  [3] 手动输入目录")
        choice = input("请输入选项 [1-3]: ").strip()
        target_dir = ""
        if choice == '1': target_dir = self.script_root_dir
        elif choice == '2': target_dir = "/sdcard/"
        elif choice == '3': target_dir = input("请输入要扫描的目录: ").strip()
        if not os.path.isdir(target_dir): print(f"{TermuxColors.FAIL}目录无效。{TermuxColors.ENDC}"); return []
        print(f"正在扫描 '{target_dir}'..."); files_by_dir = defaultdict(lambda: {'cues': [], 'flacs': []})
        for root, _, files in os.walk(target_dir):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext == '.cue': files_by_dir[root]['cues'].append(file)
                elif ext == '.flac': files_by_dir[root]['flacs'].append(file)
        final_tasks = []
        for directory, items in files_by_dir.items():
            cue_basenames = {os.path.splitext(f)[0] for f in items['cues']}
            for cue_file in items['cues']: final_tasks.append(os.path.join(directory, cue_file))
            for flac_file in items['flacs']:
                flac_basename = os.path.splitext(flac_file)[0]
                if flac_basename in cue_basenames: continue
                if self._has_embedded_cue(os.path.join(directory, flac_file)):
                    final_tasks.append(os.path.join(directory, flac_file))
        final_tasks.sort(); return final_tasks

    def confirm_action(self, prompt_text="是否继续?"):
        while True:
            reply = input(f"{TermuxColors.WARNING}{prompt_text} (Y/n): {TermuxColors.ENDC}").lower().strip()
            if reply in ['y', 'yes', '']: return True
            if reply in ['n', 'no']: return False
            print(f"{TermuxColors.FAIL}无效输入。{TermuxColors.ENDC}")

    def run(self):
        self.display_welcome()
        if not os.path.exists(os.path.expanduser('~/storage')):
            print(f"\n{TermuxColors.WARNING}警告：未检测到 '~/storage' 目录。{TermuxColors.ENDC}")
            print(f"如果遇到文件访问错误，请先运行 {TermuxColors.BOLD}termux-setup-storage{TermuxColors.ENDC} 命令授权。\n")
        try: self.processor.check_dependencies()
        except EnvironmentError as e: print(f"{TermuxColors.FAIL}{e}{TermuxColors.ENDC}"); sys.exit(1)
        while True:
            tasks = self._scan_and_get_tasks()
            if not tasks:
                if not self.confirm_action("未扫描到任何任务。是否重试?"): break
                else: continue
            
            # [Debug] 将列表推导式改为标准的 for 循环，提高可读性
            print(f"\n扫描到 {len(tasks)} 个有效专辑任务。")
            for i, task in enumerate(tasks):
                tag = f" {TermuxColors.WARNING}[FLAC 内嵌]{TermuxColors.ENDC}" if task.lower().endswith('.flac') else ""
                print(f"  [{i+1:2d}] {os.path.basename(task)}{tag}")

            if not self.confirm_action(f"是否全自动处理以上所有 {len(tasks)} 个任务?"):
                if not self.confirm_action("已取消。是否重新扫描?"): break
                else: continue
            results = []
            is_concurrent = len(tasks) > 1
            if is_concurrent:
                print(f"\n检测到多个专辑，将启用【专辑并发】模式。"); worker_args = [(task_path, False) for task_path in tasks]; cores = min(multiprocessing.cpu_count(), len(tasks), 4)
                print(f"开始处理 {len(tasks)} 个任务，使用 {cores} 个核心。")
                with Pool(processes=cores, initializer=init_worker) as pool: results = pool.map(process_album_worker, worker_args)
            else:
                print(f"\n将以【顺序】模式处理专辑。");
                for task_path in tasks:
                    results.append(process_album_worker((task_path, True)))
            if is_concurrent: print("...并发任务完成，正在重置终端..."); os.system("reset")
            success_count = 0; error_logs = []
            for res in results:
                if res[0]: success_count += 1
                else: error_logs.append(f"--- Error on album {res[1]} ---\n{res[2]}\n")
            if error_logs:
                with open("cue_splitter_error.log", "a", encoding='utf-8') as f:
                    f.write("\n" + "="*10 + f" Batch Log at {__import__('datetime').datetime.now()} " + "="*10 + "\n")
                    for log in error_logs: f.write(log)
            print("\n" + "="*20 + " 批量处理结果汇总 " + "="*20); print(f"{TermuxColors.OKGREEN}成功: {success_count} 个专辑{TermuxColors.ENDC}")
            failure_count = len(tasks) - success_count
            if failure_count > 0: print(f"{TermuxColors.FAIL}失败: {failure_count} 个专辑 (详情已写入 cue_splitter_error.log){TermuxColors.ENDC}")
            if not self.confirm_action("\n当前批次已完成。是否继续处理其他任务?"): break
        print("\n感谢使用！程序已退出。")
