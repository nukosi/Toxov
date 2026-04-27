import sys
import subprocess
import json
import os

HOSTS_FILE = r"C:\Windows\System32\drivers\etc\hosts"
BLOCK_TAG = "# nukosisnsblocker"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_sites():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("sites", [])


def block():
    sites = load_sites()
    with open(HOSTS_FILE, "r") as f:
        content = f.read()
    with open(HOSTS_FILE, "a") as f:
        for site in sites:
            entry = f"0.0.0.0 {site} {BLOCK_TAG}\n"
            if entry not in content:
                f.write(entry)
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)
    print("ブロックしました")


def unblock():
    with open(HOSTS_FILE, "r") as f:
        lines = f.readlines()
    with open(HOSTS_FILE, "w") as f:
        for line in lines:
            if BLOCK_TAG not in line:
                f.write(line)
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)
    print("ブロック解除しました")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方:")
        print("  ブロック:   python blocker.py block")
        print("  解除:       python blocker.py unblock")
    elif sys.argv[1] == "block":
        block()
    elif sys.argv[1] == "unblock":
        unblock()
    else:
        print(f"不明なコマンド: {sys.argv[1]}")
