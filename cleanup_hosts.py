HOSTS_FILE = r"C:\Windows\System32\drivers\etc\hosts"

with open(HOSTS_FILE, "r") as f:
    lines = f.readlines()

with open(HOSTS_FILE, "w") as f:
    for line in lines:
        if "# freedom-blocker" not in line and "# nukosisnsblocker" not in line and "# CutNet" not in line:
            f.write(line)

print("hostsファイルをクリーンアップしました")
