# CutNet

指定した時間帯にWebサイトを自動でブロックするインターネットデトックスツールです。

## 機能

- ブロックする時間帯とサイトをWebサイトから設定
- PC起動時に自動でブロックが動作
- 緊急解除ボタン（Webサイトから操作）
- ブロック・解除のログ確認

## 使い方

### 1. アカウント登録

[CutNet](https://web-production-ed8c9.up.railway.app) にアクセスして新規登録します。

### 2. 設定

ログイン後、ブロックしたい時間帯とサイトを入力して「設定を保存」します。

**サイトの記述例：**
youtube.com
www.youtube.com
x.com
www.x.com


### 3. CutNet.exe をダウンロード・起動

[最新版をダウンロード](https://github.com/nukosi/nukosisnsblocker/releases/latest/download/CutNet.exe)

- 起動するとUAC（管理者権限）の確認が出るので「はい」をクリック
- 初回起動時にURLの入力ダイアログが出るので、Webサイトの「PC連携トークン」URLを貼り付け
- 以後はPC起動時に自動で動作します

### 4. Edgeを使っている場合

`edge://settings/privacy` を開き、「セキュア DNS を使用する」をオフにしてください。

## 注意事項

- Windows専用
- 管理者権限が必要
- SmartScreenの警告が出た場合は「詳細情報」→「実行」をクリック
- smartappcontrolの警告が出た場合、オフにしてご利用ください。
