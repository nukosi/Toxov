# Toxov

> 🇯🇵 日本語版READMEは [README_ja.md](README_ja.md) をご覧ください。

**Toxov** is a Windows app that automatically blocks distracting websites and apps during scheduled time periods — with no easy way around it.

![Hero](https://toxov.net/static/ogp.png)

---

## Why Toxov?

Browser extensions and app timers all have workarounds. You can just open a different browser or disable the extension when you really want to.

Toxov works differently. It rewrites the **hosts file** and adds **Windows Firewall rules** directly, so there's no quick escape hatch. You set your schedule once from a web dashboard, and it runs silently in the background every time your PC starts.

---

## Screenshots

> 📸 *Dashboard screenshot coming soon*

> 🎬 *Demo video (YouTube blocked in real time) coming soon*

---

## Features

- **Schedule-based blocking** — set start/end times from your browser, blocking runs automatically
- **Websites + apps** — block sites via hosts file and apps via Windows Firewall
- **No easy workarounds** — works across all browsers, no extension to disable
- **Streak tracking** — consecutive clean days without emergency unblock
- **Points & season ranking** — stay motivated with a leaderboard
- **Emergency unblock** — available when you truly need it, with a point penalty
- **Streak shield** — protect your streak once per month (Premium)
- **Bilingual** — Japanese and English UI

---

## Download

**[⬇ Download Toxov.exe (latest)](https://github.com/nukosi/Toxov/releases/latest/download/Toxov.exe)**

Or visit [toxov.net](https://toxov.net) for the full setup guide.

**System requirements:** Windows 10 / 11 (64-bit), administrator privileges required

---

## Getting Started

1. Create an account at [toxov.net](https://toxov.net)
2. Set your block schedule, sites, and apps from the dashboard
3. Download and run `Toxov.exe`
4. Enter the 6-character code shown on the **PC Link** page
5. Done — blocking starts automatically on every boot

---

## Pricing

| Plan | Price |
|---|---|
| Monthly | ¥1,000 / month |
| Yearly | ¥5,000 / year |
| Early Bird | ¥3,000 / year (limited) |

All plans include a **7-day free trial**.

---

## FAQ

**Q: Will it affect my other browsers?**
A: Yes. Toxov modifies the system hosts file, which affects all browsers on your PC.

**Q: What happens if I move Toxov.exe to a different folder?**
A: The autostart task updates itself on every launch, so it will always point to the current location.

**Q: SmartScreen shows a warning — is it safe?**
A: Yes. Toxov is unsigned (no code signing certificate yet), which triggers SmartScreen. Click "More info" → "Run anyway". You can verify the file on VirusTotal (see below).

**Q: Does it work on Mac or mobile?**
A: Windows only for now. See Roadmap below.

**Q: How do I uninstall?**
A: Delete `Toxov.exe` and remove the scheduled task named "Toxov" from Windows Task Scheduler. Your account and settings on toxov.net are separate.

---

## VirusTotal

Some antivirus engines flag PyInstaller-packaged executables as suspicious. Toxov is clean — you can verify it yourself:

**SHA256:** `e3ad8361b7785da79100a5d055fdfedccc4ac5c4a1f67541b9c4458536e31559`

4 / 70 vendors flagged it (Bkav Pro, SecureAge, Yandex, CrowdStrike) — all known false positives for PyInstaller apps that modify system files. Major engines (Windows Defender, Avast, Bitdefender, etc.) show clean.

---

## Roadmap

- [ ] Android app
- [ ] Multi-language support (beyond Japanese/English)
- [ ] Mac support

---

## Contact

[toxov.net/contact-en](https://toxov.net/contact-en) or [toxovmail@gmail.com](mailto:toxovmail@gmail.com)

---

© 2026 Toxov
