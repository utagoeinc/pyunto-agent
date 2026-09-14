# pyunto-agent serve をコンテナで動かす（本番手順）

アプリの「Claude を招待」を有効にするための、サーバ側の手順です。
pyunto-server と同じホストで、既存の `docker compose` にエージェントのコンテナを 1 つ足します。

## 構成

```
本番ホスト
├── pyunto-server/     ← 既存（nginx / server / db / redis / certbot）
│   ├── docker-compose.yml
│   ├── docker-compose.agent.yml   ← 追加（pyunto-agent サービスの定義）
│   └── .env                       ← 追記あり
└── pyunto-agent/      ← 追加（このフォルダを pyunto-server の隣に置く）
    ├── Dockerfile
    └── persona.md
```

エージェントは公開 API（`https://api.pyunto.com`）に普通のクライアントとして接続するため、DB や Redis には触れません。
identity 鍵とスペース鍵は volume `pyunto_agent_data` の中だけにあります。

## 1. フォルダを置く

本番ホストで、`pyunto-server` の隣に `pyunto-agent` を置きます。必要なのは `Dockerfile`、`pyproject.toml`、`persona.md`、`README.md`、`pyunto_agent/*.py` だけです（`.venv` や鍵は送りません）。同梱の `deploy.sh` が、この一覧だけを rsync します。

```bash
cd pyunto-agent
./deploy.sh user@host:/path/to/pyunto-agent
```

`--delete` を付けているので、送り先の余分なファイル（以前に誤って送った `.venv` など）は消えます。

## 2. .env に追記する（pyunto-server/.env）

```bash
# 既存の docker compose コマンドのまま agent の定義も読み込ませる
COMPOSE_FILE=docker-compose.yml:docker-compose.agent.yml

# server → agent（コンテナ名で到達。ポート公開は不要）
AGENT_SERVICE_URL=http://pyunto-agent:8788
AGENT_SERVICE_SECRET=<openssl rand -hex 32 で作った文字列>

# agent の返信生成（Anthropic API）
ANTHROPIC_API_KEY=<Anthropic の API キー>
PYUNTO_MODEL=claude-sonnet-5
PYUNTO_AGENT_NAME=Claude
```

`AGENT_SERVICE_SECRET` は server と agent の両方が同じ値を読みます（`docker-compose.agent.yml` が中継します）。

## 3. 起動する

いつもの手順のままです。`COMPOSE_FILE` を .env に書いたので、`docker compose` が両方のファイルを読みます。

```bash
cd pyunto-server
./pull_and_run.sh        # git pull → docker compose build → down → up
```

手で行う場合:

```bash
docker compose build pyunto-agent server
docker compose up -d
```

## 4. 確認する

```bash
docker compose logs -f pyunto-agent
```

次の 3 行が出れば稼働しています。

```
agent service listening on 0.0.0.0:8788
bridge online as <エージェントの user id> (backend=claude-api, dry_run=False)
socket.io connected
```

server 側から到達できるかは、server コンテナの中から確認できます。

```bash
docker compose exec server sh -c 'wget -qO- --header="X-Agent-Secret: $AGENT_SERVICE_SECRET" http://pyunto-agent:8788/whoami'
```

`{"user_id": "...", "display_name": "Claude"}` が返れば OK です。

## 5. アプリで試す

1. iPhone の Pyunto でプレミアムスペースを開き、右上メニューの「Claude を招待」を押す。
2. 確認ダイアログで招待。数秒で参加し、アプリが鍵を配ります。
3. 日記を投稿すると返事が届きます。
4. 「Claude を外す」で退出させられます。

## 運用メモ

- **初回起動で匿名アカウント「Claude」が 1 つ作られます。** volume を消すと identity が変わり、参加済みスペースの日記が読めなくなります。`pyunto_agent_data` は必ず残してください（バックアップ対象）。
- 既に手元の Mac で作ったエージェント（`.agent-data`）を本番に引き継ぎたい場合は、その中身を volume に入れれば同じアカウントのまま動きます。
- 返信の上限は 1 時間 60 件（全スペース合計）。増やす場合は `Bridge(max_replies_per_hour=…)` を変えます。
- 停止中に届いた投稿には返事しません。再開後に取りこぼしを拾う処理は今後の課題です。
- ログには投稿本文の先頭 120 文字が出ます。本番では `--verbose` を付けないでください。
