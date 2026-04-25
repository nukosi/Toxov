import time
import datetime
import subprocess
import sys
import os
from database import init_db, load_config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BLOCKER_FILE = os.path.join(BASE_DIR, "blocker.py")


def should_be_blocked(config):
    now = datetime.datetime.now().time()
    sh, sm = map(int, config["block_start"].split(":"))
    eh, em = map(int, config["block_end"].split(":"))
    return datetime.time(sh, sm) <= now < datetime.time(eh, em)


def run_blocker(command):
    subprocess.run([sys.executable, BLOCKER_FILE, command])


current_state = None

init_db()
print("nukosisnsblocker スケジューラー起動")
print("終了するには Ctrl+C")

while True:
    try:
        config = load_config()
        should_block = should_be_blocked(config)

        if should_block != current_state:
            now_str = datetime.datetime.now().strftime("%H:%M")
            if should_block:
                print(f"[{now_str}] ブロック開始 ({config['block_start']} ～ {config['block_end']})")
                run_blocker("block")
            else:
                print(f"[{now_str}] ブロック解除")
                run_blocker("unblock")
            current_state = should_block

    except Exception as e:
        print(f"エラー: {e}")

    time.sleep(60)
