#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Termux CUE 整轨无损分割工具 - 主程序入口
"""

import sys
import os
import traceback
import multiprocessing

from core.cli import Cli

def main():
    if sys.version_info < (3, 6):
        print("错误：此脚本需要 Python 3.6 或更高版本。")
        sys.exit(1)

    try:
        script_root_dir = os.path.dirname(os.path.abspath(__file__))
        app = Cli(script_root_dir)
        app.run()
        
    except KeyboardInterrupt:
        print("\n\n操作被用户中断。程序已退出。")
        sys.exit(0)
    except Exception as e:
        print(f"\n发生了一个未预料到的顶层错误: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
