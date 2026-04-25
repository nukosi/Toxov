import customtkinter as ctk
import json
import os
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


def is_blocking_time(config):
    now = datetime.datetime.now().time()
    sh, sm = map(int, config["block_start"].split(":"))
    eh, em = map(int, config["block_end"].split(":"))
    return datetime.time(sh, sm) <= now < datetime.time(eh, em)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("nukosisnsblocker")
        self.geometry("420x540")
        self.resizable(False, False)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.config_data = load_config()
        self._build_ui()
        self._refresh_status()

    def _build_ui(self):
        ctk.CTkLabel(self, text="Freedom", font=ctk.CTkFont(size=26, weight="bold")).pack(pady=(20, 4))

        self.status_label = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=14))
        self.status_label.pack(pady=(0, 16))

        # ブロック時間
        time_frame = ctk.CTkFrame(self)
        time_frame.pack(fill="x", padx=24, pady=6)

        ctk.CTkLabel(time_frame, text="ブロック時間", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 6))

        row = ctk.CTkFrame(time_frame, fg_color="transparent")
        row.pack(pady=(0, 10))

        ctk.CTkLabel(row, text="開始").pack(side="left", padx=(10, 4))
        self.start_entry = ctk.CTkEntry(row, width=75, placeholder_text="08:00")
        self.start_entry.insert(0, self.config_data["block_start"])
        self.start_entry.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(row, text="終了").pack(side="left", padx=(0, 4))
        self.end_entry = ctk.CTkEntry(row, width=75, placeholder_text="21:00")
        self.end_entry.insert(0, self.config_data["block_end"])
        self.end_entry.pack(side="left", padx=(0, 10))

        # サイト一覧
        sites_frame = ctk.CTkFrame(self)
        sites_frame.pack(fill="both", expand=True, padx=24, pady=6)

        ctk.CTkLabel(sites_frame, text="ブロックするサイト", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 6))

        self.sites_box = ctk.CTkTextbox(sites_frame, height=140, font=ctk.CTkFont(size=13))
        self.sites_box.pack(fill="both", expand=True, padx=10)
        self.sites_box.insert("end", "\n".join(self.config_data.get("sites", [])))

        add_row = ctk.CTkFrame(sites_frame, fg_color="transparent")
        add_row.pack(pady=8)

        self.new_site_entry = ctk.CTkEntry(add_row, width=210, placeholder_text="例: instagram.com")
        self.new_site_entry.pack(side="left", padx=(10, 6))
        self.new_site_entry.bind("<Return>", lambda e: self._add_site())

        ctk.CTkButton(add_row, text="追加", width=60, command=self._add_site).pack(side="left", padx=(0, 10))

        # 保存ボタン
        ctk.CTkButton(self, text="設定を保存", height=38, command=self._save).pack(pady=14)

    def _add_site(self):
        site = self.new_site_entry.get().strip()
        if not site:
            return
        current = self.sites_box.get("1.0", "end").strip()
        self.sites_box.delete("1.0", "end")
        self.sites_box.insert("end", (current + "\n" + site) if current else site)
        self.new_site_entry.delete(0, "end")

    def _save(self):
        start = self.start_entry.get().strip()
        end = self.end_entry.get().strip()

        # 簡易バリデーション
        try:
            sh, sm = map(int, start.split(":"))
            eh, em = map(int, end.split(":"))
            assert 0 <= sh < 24 and 0 <= sm < 60
            assert 0 <= eh < 24 and 0 <= em < 60
        except Exception:
            self.status_label.configure(text="時刻の形式が正しくありません (例: 08:00)", text_color="#ffa94d")
            return

        sites = [s.strip() for s in self.sites_box.get("1.0", "end").splitlines() if s.strip()]

        self.config_data["block_start"] = start
        self.config_data["block_end"] = end
        self.config_data["sites"] = sites
        save_config(self.config_data)

        self.status_label.configure(text="保存しました", text_color="#69db7c")
        self.after(2000, self._refresh_status)

    def _refresh_status(self):
        self.config_data = load_config()
        if is_blocking_time(self.config_data):
            s = self.config_data["block_start"]
            e = self.config_data["block_end"]
            self.status_label.configure(text=f"● ブロック中  ({s} ～ {e})", text_color="#ff6b6b")
        else:
            self.status_label.configure(text="● 解除中", text_color="#69db7c")


if __name__ == "__main__":
    app = App()
    app.mainloop()
