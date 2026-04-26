import time
import datetime
import subprocess
import sys
import os
import requests

# ここにRailwayのURLを設定する
CLOUD_URL = "https://web-production-ed8c9.up.railway.app/api/config"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BLOCKER_FILE = os.path.join(BASE_DIR, "blocker.py")

JST = datetime.timezone(datetime.timedelta(hours=9))


def load_config():
    response = requests.get(CLOUD_URL, timeout=10)
    response.raise_for_status()
    return response.json()


def should_be_blocked(config):
    now = datetime.datetime.now(JST).time()
    sh, sm = map(int, config["block_start"].split(":"))
    eh, em = map(int, config["block_end"].split(":"))
    return datetime.time(sh, sm) <= now < datetime.time(eh, em)


def run_blocker(command):
    subprocess.run([sys.executable, BLOCKER_FILE, command])


current_state = None

print("nukosisnsblocker スケジューラー起動")
print(f"設定取得先: {CLOUD_URL}")
print("終了するには Ctrl+C")

while True:
    try:
        config = load_config()
        should_block = should_be_blocked(config)

        if should_block != current_state:
            now_str = datetime.datetime.now(JST).strftime("%H:%M")
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
