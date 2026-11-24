# AIがM1 MacでRecastPressのDockerデモを構築する手順

利用者からMacでのローカルデモ構築を依頼されたAIエージェント向けの実行手順。M1系のMacでGitHubから固定ソースを取得し、Docker内でLinux ARM64用イメージをビルドして公開サンプルを起動する。人が操作する場合は[Mac向け手順書](local-demo-macos.ja.md)も参照する。

作成日：2026年11月17日（日本時間）。対象は`dev/v0.6`のコミット`45f77d7ce82699d7dcfb074310e592c278169367`。本書のコマンドはMac実機で未実測。AIは各段階の終了コードと実際の応答を確認し、未確認の項目を成功として報告しない。

## AIへ渡す依頼文

次の文章と本書を、Macを操作できるAIへ渡す。

> この手順に従い、M1 Mac上でRecastPressのローカルDockerデモを構築・起動してください。ソースはGitHubから指定コミットを取得し、新しい専用ディレクトリ、専用Composeプロジェクト、空きポートを使ってください。別スレッドで開発中のリポジトリ、Git状態、ビルド出力、コンテナを変更しないでください。各段階を実測して進め、デモが動いたら起動したままURLと再開・停止コマンドを報告してください。失敗した場合は原因と残っている状態を報告してください。

## 作業の範囲と実行規則

- 実行対象はM1系Mac。Mac本体にRust・Cargo・PHP・DBを導入せず、Docker内でビルドする。
- 変更先は今回作成する専用ディレクトリと、今回の専用Composeプロジェクトに限定する。Docker Desktopが未導入の場合は公式配布物の導入も行う。
- 開発中のリポジトリで`checkout`、`pull`、`stash`、`reset`、`clean`、ビルド、コミット、pushを行わない。既存Dockerfile、Compose、`.dockerignore`、`Cargo.lock`やソースを修正しない。
- 既存のDocker Desktopを更新・再起動したり、リソース設定を変更したりして他の開発を中断しない。Docker全体の停止・prune・コンテナの一括停止を行わない。
- コマンドは順に実行し、失敗した段階で後続の依存処理を止める。ビルドと起動を並行実行しない。長いビルドはログを確認し、利用者へ適宜進捗を伝える。
- 通常の取得・設定・ビルド・起動・検証は依頼の範囲で進め、段階ごとの再確認を求めない。認証、利用条件への同意、OSの認証画面など、利用者の操作が必要な場合に具体的な操作を伝える。パスワードやトークンをチャットへ貼るよう求めない。

このデモの対象は固定HTML・CSS・JavaScriptの公開配信。WordPress管理画面、ログイン、投稿保存、DB、要求時のPHP／Wasm実行まで完成したと報告しない。同一MacのDocker資源は共有されるため、初回ビルドの負荷までは分離されない。

## 実行環境を確認する

以下はzshで実行する。ツール呼び出しごとにシェルが変わる場合、後述の`demo.sh`を各呼び出しで読み込む。

```sh
uname -s
uname -m
git --version
docker --version
docker compose version
docker context ls
docker --context desktop-linux info
```

進行条件は次のすべて。

| 項目 | 条件 |
| --- | --- |
| OS | `Darwin`。WindowsやLinux上では本書のセットアップを実行しない |
| CPU | `arm64`。M1なのに`x86_64`ならRosetta経由のシェルを解消して再確認する |
| Git | `git --version`が成功する |
| Docker | ローカルの`desktop-linux`へ接続できる |
| Compose | 2.24.4以降。ポートの`!override`に必要 |

Docker Desktopが未導入の場合は、[公式Mac導入ページ](https://docs.docker.com/desktop/setup/install/mac-install/)で対応macOSと利用条件を確認し、Apple Silicon用を取得して`/Applications`へ配置・起動する。GUIの認証や同意が必要なら、必要な箇所だけ利用者へ操作を引き継ぎ、終了後に上記確認を再実行する。ComposeはDocker Desktopに同梱される。[公式説明](https://docs.docker.com/compose/install/)

Gitが未導入ならCommand Line Toolsの案内に従う。必要なら`xcode-select --install`で導入を開始し、完了後に再確認する。既存Docker Desktopのバージョン不足や再起動が必要な変更で他の作業へ影響する場合は、その変更を実施せず状況を報告する。

## 新しい専用ディレクトリと空きポートを選ぶ

毎回の新規セットアップに別名を使う。再開の依頼なら、末尾の再開手順を使い、既存ディレクトリへ初期設定を書き直さない。

```sh
RP_DEMO_PROJECT="recastpress-mac-demo-ai-$(TZ=Asia/Tokyo date +%Y%m%d-%H%M%S)"
RP_DEMO_ROOT="$HOME/$RP_DEMO_PROJECT"
mkdir "$RP_DEMO_ROOT"
mkdir "$RP_DEMO_ROOT/evidence"
RP_DEMO_SOURCE="$RP_DEMO_ROOT/source"
RP_DEMO_IMAGE="$RP_DEMO_PROJECT:local"
```

`mkdir`に失敗したら停止する。既存の同名ディレクトリは削除・上書きしない。新しい名前を選び直す。

```sh
lsof -nP -iTCP:18080 -sTCP:LISTEN
```

出力がなければ`RP_DEMO_PORT=18080`とする。使用中なら18081〜18089を順に調べて空きポートを選ぶ。使用中のプロセスは停止しない。`lsof`自体の実行失敗と、該当リスナーがない結果を区別する。

```sh
RP_DEMO_PORT=18080
```

上記代入は実際に選んだ番号へ合わせる。以後は同じ変数からポート・許可Host・URLを生成する。

## GitHubから固定コミットを取得する

```sh
git clone --single-branch --branch dev/v0.6 --no-checkout \
  https://github.com/KANIKAMA-Carcinization/RecastPress.git \
  "$RP_DEMO_SOURCE"
git -C "$RP_DEMO_SOURCE" checkout --detach \
  45f77d7ce82699d7dcfb074310e592c278169367
git -C "$RP_DEMO_SOURCE" rev-parse HEAD
git -C "$RP_DEMO_SOURCE" status --short
ls "$RP_DEMO_SOURCE/deploy/development/Dockerfile"
ls "$RP_DEMO_SOURCE/deploy/development/compose.yaml"
```

指定コミットとの一致、作業ツリーが空、両ファイルの存在を確認する。認証に失敗したら利用者の既存Git認証を使い、秘密をURLへ埋め込まない。コミットが見つからなくても最新HEADや別コミットへ置き換えない。取得先と認証・コミットの問題を報告する。

Dockerfileはcloneで取得される。別途インストールしたり、AIが生成したDockerfileへ差し替えたりしない。

## 次の呼び出しでも使える操作関数を保存する

```sh
{
  printf 'RP_DEMO_ROOT=%q\n' "$RP_DEMO_ROOT"
  printf 'RP_DEMO_PROJECT=%s\n' "$RP_DEMO_PROJECT"
  printf 'RP_DEMO_PORT=%s\n' "$RP_DEMO_PORT"
  printf 'RP_DEMO_IMAGE=%s\n' "$RP_DEMO_IMAGE"
  cat <<'EOF'
RP_DEMO_SOURCE="$RP_DEMO_ROOT/source"
RP_DEMO_URL="http://localhost:$RP_DEMO_PORT/asset-demo?view=1"

rp_docker() {
  docker --context desktop-linux "$@"
}

rp_demo() (
  unset RECASTPRESS_BUILDER_IMAGE RECASTPRESS_RUNTIME_IMAGE
  unset RECASTPRESS_ARTIFACT_DIR RECASTPRESS_PUBLIC_HOST
  unset RP_DEMO_PLATFORM RP_DEMO_IMAGE RP_DEMO_PORT
  unset COMPOSE_PROFILES DOCKER_DEFAULT_PLATFORM
  rp_docker compose \
    --env-file "$RP_DEMO_ROOT/demo.env" \
    -p "$RP_DEMO_PROJECT" \
    -f "$RP_DEMO_SOURCE/deploy/development/compose.yaml" \
    -f "$RP_DEMO_ROOT/compose.mac.yaml" "$@"
)
EOF
} > "$RP_DEMO_ROOT/demo.sh"

source "$RP_DEMO_ROOT/demo.sh"
```

保存された絶対パスを作業記録に保持する。以後、新しいシェルではその`demo.sh`を`source`してから実行する。Docker contextは各コマンドで明示し、既定contextを変更しない。

## ARM64のベースイメージを取得する

既存DockerfileはRust 1.96.xを要求する。下記タグは取得候補であり、実際のpullと検査の成功を必須とする。

```sh
RP_BASE_TAG='docker.io/library/rust:1.96.0-bookworm'
rp_docker pull --platform linux/arm64 "$RP_BASE_TAG"
rp_docker image inspect "$RP_BASE_TAG" \
  --format '{{.Os}}/{{.Architecture}} {{json .RepoDigests}}'
RP_BASE_IMAGE="$(rp_docker image inspect "$RP_BASE_TAG" \
  --format '{{index .RepoDigests 0}}')"
printf '%s\n' "$RP_BASE_IMAGE"
rp_docker run --rm --platform linux/arm64 \
  --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  "$RP_BASE_IMAGE" rustc --version
```

`linux/arm64`、空でない`repository@sha256:...`参照、`rustc 1.96.0 ...`の出力を確認する。失敗時は停止し、`latest`、AMD64、別のRust系列へ変更しない。builderとruntimeに同じdigestを使い、実行時ライブラリの差を避ける。runtimeにもRustが残る大きいデモ用イメージになる。

## 公開データとComposeの追加設定を作る

```sh
mkdir "$RP_DEMO_ROOT/artifacts"
cp "$RP_DEMO_SOURCE/examples/sealed-assets-bundle.json" \
  "$RP_DEMO_ROOT/artifacts/public.json"
cp "$RP_DEMO_SOURCE/examples/sealed-assets-receipt.json" \
  "$RP_DEMO_ROOT/artifacts/public.receipt.json"
chmod 755 "$RP_DEMO_ROOT/artifacts"
chmod 644 "$RP_DEMO_ROOT/artifacts/public.json" \
  "$RP_DEMO_ROOT/artifacts/public.receipt.json"

printf 'RECASTPRESS_BUILDER_IMAGE=%s\nRECASTPRESS_RUNTIME_IMAGE=%s\nRECASTPRESS_ARTIFACT_DIR="%s"\nRECASTPRESS_PUBLIC_HOST=localhost:%s\nRP_DEMO_PLATFORM=linux/arm64\nRP_DEMO_PORT=%s\nRP_DEMO_IMAGE=%s\n' \
  "$RP_BASE_IMAGE" "$RP_BASE_IMAGE" "$RP_DEMO_ROOT/artifacts" \
  "$RP_DEMO_PORT" "$RP_DEMO_PORT" "$RP_DEMO_IMAGE" \
  > "$RP_DEMO_ROOT/demo.env"
chmod 600 "$RP_DEMO_ROOT/demo.env"

cat > "$RP_DEMO_ROOT/compose.mac.yaml" <<'EOF'
services:
  public:
    image: "${RP_DEMO_IMAGE:?Set the dedicated demo image}"
    platform: "${RP_DEMO_PLATFORM:?Set linux/arm64}"
    ports: !override
      - "127.0.0.1:${RP_DEMO_PORT:?Set the free demo port}:8080"
EOF

rp_demo config --format json > "$RP_DEMO_ROOT/evidence/compose.resolved.json"
```

JSONを読み、ビルド前に以下をすべて確認する。`config`の成功だけで確認済みとしない。

- `public.platform`が`linux/arm64`で、イメージ名が今回専用の値。
- build contextとDockerfileが今回取得した`source/`を参照する。
- builder/runtime引数が取得した同じdigestを参照する。
- Mac側の公開先が`127.0.0.1`の選んだ1ポートだけ。元の8080が追加公開されていない。
- commandのlistenが`0.0.0.0:8080`、許可Hostが`localhost:選んだポート`。
- `/artifacts`が今回の専用ディレクトリからの読み取り専用マウント。
- 非root、read-only root filesystem、capability削除の既存設定が保たれている。

`!override`はCompose 2.24.4以降の機能。[公式仕様](https://docs.docker.com/reference/compose-file/merge/#replace-value)

## ビルド成果物を確認してデータを検査する

```sh
rp_demo build public
rp_docker image inspect "$RP_DEMO_IMAGE" \
  --format '{{.Id}} {{.Os}}/{{.Architecture}}'
rp_demo run --rm --no-deps -T public \
  check-package /artifacts/public.json /artifacts/public.receipt.json
```

各コマンドの終了コードを個別に確認する。AI用ツールのパイプでログ保存する場合は、最後の`tee`だけの成功をビルド成功とみなさない。

成果物のイメージIDと`linux/arm64`を記録する。`check-package`は終了コード0、bundle version 3、3ページが期待値。失敗時にreceiptやbundleを手で修正して通さず、同じ固定コミットからのコピーかを確認する。

これでビルド成果物はMacのローカルDockerへ登録される。起動は次のComposeで行う。ホストへLinuxバイナリを直接配置する操作や、`docker save`／`docker load`による別PCからの搬入はこの経路では不要。

## 起動してHTTPを検査する

```sh
rp_demo up -d --no-build --pull never public
rp_demo ps
rp_demo logs --tail 50 public
curl -4 --connect-timeout 3 --max-time 10 -i \
  "http://localhost:$RP_DEMO_PORT/health/ready"
curl -4 --connect-timeout 3 --max-time 10 -i "$RP_DEMO_URL"
curl -4 --connect-timeout 3 --max-time 10 -I "$RP_DEMO_URL"
```

接続失敗やreadiness 503の場合はログを確認する。起動待ちの再確認は最大30秒程度に区切り、無期限に再試行しない。`ps`の実行中表示だけでは成功としない。

| 検査 | 合格条件 |
| --- | --- |
| 起動ログ | `listening on 0.0.0.0:8080` |
| readiness | HTTP 200、本文`ready` |
| デモGET | HTTP 200、HTML、空でないETag |
| デモHEAD | HTTP 200、本文なし、GETと同じETag |
| 条件付きGET | 同じETagを送るとHTTP 304、本文なし |
| 不一致Host | 下記のIP URLでHTTP 421 |

```sh
RP_ETAG="$(curl -4 --connect-timeout 3 --max-time 10 -fsSI "$RP_DEMO_URL" | \
  awk 'tolower($1) == "etag:" { gsub("\r", "", $2); print $2 }')"
printf '%s\n' "$RP_ETAG"
curl -4 --connect-timeout 3 --max-time 10 -sS -o /dev/null \
  -w '%{http_code} %{size_download}\n' \
  -H "If-None-Match: $RP_ETAG" "$RP_DEMO_URL"
curl --connect-timeout 3 --max-time 10 -sS -o /dev/null \
  -w '%{http_code}\n' \
  "http://127.0.0.1:$RP_DEMO_PORT/asset-demo?view=1"
```

ETagが空なら条件付きGETへ進まない。期待する出力は順にETag、`304 0`、`421`。curlの終了コード0だけではHTTPの合格判定にならないため、statusとbodyも確認する。

## ブラウザ表示と再起動を検査する

ブラウザを操作できるツールがあれば`RP_DEMO_URL`へ移動し、次を確認する。

- 見出しが`Fixed sealed assets`。
- カードに緑の背景と枠がある。
- `JavaScript loaded.`と表示される。

ブラウザを操作できない環境では、次で表示を開ける。ただし、開くコマンドの成功だけで表示確認済みとしない。

```sh
open "$RP_DEMO_URL"
```

続けて今回のpublicだけを再起動し、同じURLとreadinessが200に戻ることを確認する。

```sh
rp_demo restart public
curl -4 --connect-timeout 3 --max-time 10 -i \
  "http://localhost:$RP_DEMO_PORT/health/ready"
curl -4 --connect-timeout 3 --max-time 10 -i "$RP_DEMO_URL"
git -C "$RP_DEMO_SOURCE" status --short
```

再起動直後の接続失敗は最大30秒程度まで確認し、作業ツリーが空のままであることも確認する。成功後はデモを起動したまま利用者へ渡す。

## 失敗時に行うこと

失敗した段階、終了コード、関連する短いログを記録する。接続先、ファイルのコピー先、ポート設定など、専用環境内の設定誤りは修正して再確認できる。対処の詳細は[人向け手順書の対処表](local-demo-macos.ja.md#うまく起動しないとき)を参照する。

固定コミット、ARM64、Rust系列、バンドルの受理条件を変更しない。ソース変更が必要ならデモ構築の問題として報告し、開発中のスレッドでの修正と混ぜない。

今回起動したコンテナが失敗状態なら、所有する専用プロジェクトであることを確認して`rp_demo down`で回収する。ビルド失敗だけなら既存の他プロジェクトへ操作は不要。診断用の専用ディレクトリとイメージは残し、何が残ったか報告する。

## 再開と停止

次回、保存済みの`demo.sh`の絶対パスを使う。下記の`/absolute/path/to/demo.sh`は、今回の実際のパスへ置き換える。

```sh
source /absolute/path/to/demo.sh
rp_demo config
rp_demo up -d --no-build --pull never public
curl -4 --connect-timeout 3 --max-time 10 -i \
  "http://localhost:$RP_DEMO_PORT/health/ready"
open "$RP_DEMO_URL"
```

再開時はそのファイルと設定が今回の専用環境のものか確認する。イメージがなければ、固定コミットとベースdigestを確認してからビルド段階へ戻る。新しい設定や最新タグで上書きしない。

一時停止は`rp_demo stop public`。コンテナと専用ネットワークの回収は`rp_demo down`。いずれも開発中の別プロジェクトへ操作しない。

## 利用者への完了報告

今回の`evidence/result.md`へ検査結果を保存し、利用者にも次を簡潔に報告する。認証情報を記録しない。

- 成否と、実際に開けるデモURL。
- 固定コミット、専用ディレクトリ、Composeプロジェクト名。
- ベースイメージの実digest、成果物のイメージID、`linux/arm64`の確認結果。
- package検査、readiness、GET、HEAD、304、不一致Host、再起動の各結果。
- ブラウザで実確認できた項目と、未確認の項目。
- 実際の絶対パスを含む再開・停止コマンド。パスに空白があれば引用符で囲む。
- 起動したままか、失敗後に停止したか、残っているファイルやコンテナ。

HTTP検査がそろえば公開サンプルの起動成功とし、ブラウザ検査の有無は分けて報告する。Mac実行・ARM64検査・HTTP確認を行っていない状態では、デモ起動成功と報告しない。
