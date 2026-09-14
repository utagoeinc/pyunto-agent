# Claude を交換日記の相手にする手順（claude -p 版）

Pyunto の交換日記に Claude を招待し、あなたが書いた日記に Claude が返事をするようにする手順です。
Claude は「Claude Code」というターミナル用のアプリを通して動きます。API キーは要りません。
ふだんターミナルを使わない方でも、上から順にコピーして実行すれば 15 分ほどで終わります。

> 対象: macOS（Apple シリコン / Intel どちらも可）。Windows でも同じ流れで動きますが、コマンドの一部（venv の有効化）が異なります。

---

## 0. 用意するもの

| もの | 用途 |
|---|---|
| Claude のアカウント（Pro または Max プラン） | Claude Code にログインするため |
| パソコン（Mac） | Claude を動かし続ける場所。スリープしていると返事が止まります |
| iPhone / Android の Pyunto アプリ | 日記を書く側 |
| Node.js 18 以上、Python 3.11 以上 | それぞれ Claude Code と橋渡しプログラムの実行に必要（手順 1 で入れます） |

---

## 1. Claude Code を入れてログインする

ターミナルを開きます（macOS: アプリケーション → ユーティリティ → ターミナル）。

Node.js が入っているか確認します。

```bash
node -v
```

`v18` 以上が出れば OK です。出ない場合は <https://nodejs.org/ja> から LTS 版を入れ、ターミナルを開き直してください。

Claude Code を入れます。

```bash
npm install -g @anthropic-ai/claude-code
```

ログインします。ブラウザが開くので、Claude のアカウントで許可してください。

```bash
claude
```

画面が開いたら `/login` と打って Enter、ブラウザで承認したあと `/exit` で閉じます。

動作確認。次の 1 行で短い返事が返れば準備完了です。

```bash
claude -p "こんにちは。1行で自己紹介して"
```

---

## 2. 橋渡しプログラム（pyunto-agent）を入れる

Python が入っているか確認します。

```bash
python3 --version
```

`3.11` 以上が出れば OK です。出ない場合は <https://www.python.org/downloads/> から入れてください。

pyunto-agent を置きたい場所に移動して、取得します（配布形態に応じて zip 展開または git clone）。

```bash
cd ~
git clone https://github.com/pyunto/pyunto-agent
cd pyunto-agent
```

仮想環境を作ってインストールします。

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

動作確認。初回はあなた専用のエージェント用アカウント（表示名 Claude）が自動で作られます。

```bash
.venv/bin/pyunto-agent whoami
```

`user_id:` と `identity_public_key:` が表示されれば OK です。表示名を変えたい場合は、先に次のように指定します。

```bash
PYUNTO_AGENT_NAME="クロード" .venv/bin/pyunto-agent whoami
```

---

## 3. Pyunto アプリで招待リンクを作る

1. Pyunto アプリを開きます。Claude 専用の交換日記を新しく作るなら、トーク一覧の「交換日記（２人用）新規作成」を選びます。既存の共有スペースに入れるなら、そのスペースを開いてメニュー（右上の三本線）の「現在のスペースに友達追加」を選びます。自分専用の「記録」スペースには招待できません。
2. 招待画面で「リンクをコピー」を押します。`https://api.pyunto.com/invite/…` または `pyunto://invite/…` の形のリンクがコピーされます。
3. そのリンクを Mac に送ります（AirDrop、メモ、自分宛のメール など）。

---

## 4. Claude をスペースに参加させる

ターミナルで、コピーしたリンクを貼り付けて実行します（クォートで囲みます）。

```bash
.venv/bin/pyunto-agent join 'https://api.pyunto.com/invite/ここにリンク'
```

`joined space …` と出れば参加完了です。

**ここで一度、iPhone でそのスペースを開いてください。** アプリが新しいメンバー（Claude）に、日記を読むための鍵を渡します。開くだけで完了します。

確認します。

```bash
.venv/bin/pyunto-agent whoami
```

スペースの行が `key=yes` になっていれば、Claude が日記を読める状態です。`key=no` のままなら、iPhone でスペースをもう一度開いてから再確認してください。

---

## 5. Claude を起動する

```bash
.venv/bin/pyunto-agent run --backend command --command 'claude -p --output-format json' --persona persona.md
```

`bridge online` と `socket.io connected` が出れば待機中です。このターミナルは開いたままにしておきます。

---

## 6. 日記を書く

iPhone の Pyunto で、そのスペースに日記を 1 件投稿します。数秒〜十数秒で同じスレッドに Claude の返事が届きます。

- 同じスレッドに書き足すと、Claude はそのスレッドの流れ（直近 12 件）を読んで返します。
- 新しいスレッドを立てると、新しい話題として返します。
- 写真だけの投稿には返事をしません。文章を添えると返します。

---

## 7. 止める・再開する

- 止める: 手順 5 のターミナルで `Ctrl + C`。
- 再開する: 手順 5 のコマンドをもう一度実行。参加や鍵の準備はやり直さなくて大丈夫です。
- Mac がスリープすると止まります。長く動かすなら「システム設定 → バッテリー／省エネルギー」でスリープしない設定にしてください。

---

## よくあるつまずき

| 症状 | 対処 |
|---|---|
| `claude: command not found` | Node.js のインストール後にターミナルを開き直していない。開き直して `npm install -g @anthropic-ai/claude-code` をやり直す |
| `claude -p` が何も返さない／ログインを求める | `claude` を起動して `/login` をやり直す |
| `key=no` のまま | iPhone でそのスペースを開く。招待した本人（メンバー）が開く必要があります |
| 投稿しても返事が来ない | ターミナルのログを見る。`skipping entry` なら鍵が未配布、`command exited` なら Claude 側のエラー（利用上限など） |
| 返事が遅い | `claude -p` は 1 件ごとに起動するため 3〜10 秒かかります。仕様です |
| 返事の口調を変えたい | `persona.md` を書き換えて、手順 5 を再実行 |

---

## 知っておいてほしいこと

- 日記の本文は、返事を作るために Claude（Anthropic）へ送られます。交換日記の相手にもそのことを伝えてください。
- Claude のアカウントには利用上限があります。上限に達すると返事が止まり、時間が経つと再開します。
- Claude が読めるのは、招待したスペースだけです。あなたの「記録」スペースや他のスペースは読めません。
- エージェントの鍵は `~/.pyunto-agent/` に保存されます。このフォルダを消すと、Claude は日記を読めなくなります（もう一度招待からやり直しになります）。
