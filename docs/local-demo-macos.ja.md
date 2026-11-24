# MacでDockerを使ってRecastPressのローカルデモを動かす

MacへのDocker Desktop導入から、既存Dockerfileによるビルド、ブラウザでの表示確認、停止と再開までの手順。Apple SiliconとIntel Macに対応する。コマンドはMacの「ターミナル」の標準シェルであるzshで、上から順番に実行する。失敗したコマンドがあれば、先へ進まず末尾の対処表を確認する。

デモでは、事前生成したHTML・CSS・JavaScriptをRecastPressの公開HTTPプロセスから配信する。緑のカードとJavaScriptによる表示変更、GET・HEAD・ETagを確認できる。WordPress管理画面、ログイン、DB接続、要求時のPHP／Wasm実行はこの構成に含まれない。

作成日：2026年11月17日（日本時間）。対象ソースは`dev/v0.6`のコミット`45f77d7ce82699d7dcfb074310e592c278169367`。この手順のMac上でのDockerビルド・起動は未実測なので、後述の確認結果で成否を判断する。

## 開発中の作業と分ける

デモ用に次の領域を使う。

| 用途 | 場所または名前 |
| --- | --- |
| デモ一式 | `~/recastpress-mac-demo-20261117/` |
| 固定コミットの独立した取得先 | 上記の`source/` |
| 公開データ | 上記の`artifacts/` |
| デモ固有の設定 | 上記の`demo.env`、`compose.mac.yaml`、`demo.sh` |
| Composeプロジェクト名 | `recastpress-mac-demo-20261117` |
| Mac側の公開ポート | `127.0.0.1:18080` |

開発中のチェックアウトではブランチ切替、pull、ビルド、既存設定の編集を行わない。未コミットの開発内容はデモへ取り込まず、固定コミットのソースだけを使う。Composeプロジェクト名による環境の分離は[Dockerの公式仕様](https://docs.docker.com/compose/how-tos/project-name/)に従う。

Docker DesktopのCPU・メモリ・ディスクとビルドキャッシュは他のコンテナと共有する。同じMacで開発している場合、初回ビルドによる負荷は発生する。Docker Desktopをすでに利用中なら再インストール・更新・再起動は省略する。

## Docker DesktopとGitを用意する

1. Appleメニューの「このMacについて」でチップを確認する。ターミナルの`uname -m`でも、通常はApple Siliconが`arm64`、Intelが`x86_64`と表示される。
2. [Docker DesktopのMac用インストールページ](https://docs.docker.com/desktop/setup/install/mac-install/)から、チップに対応するインストーラーを取得する。対応macOSと利用条件も同ページで確認する。
3. `Docker.dmg`を開き、Dockerを「アプリケーション」へドラッグする。
4. 「アプリケーション」からDockerを起動し、利用条件と初期設定を確認して、Engineが起動するまで待つ。
5. ターミナルを新しく開き、次を実行する。

```sh
docker --version
docker compose version
docker context ls
docker --context desktop-linux info
git --version
```

Docker DesktopにはEngine・CLI・Composeが同梱されるため、Composeを別途インストールする必要はない。[Compose導入の公式説明](https://docs.docker.com/compose/install/)

この手順ではCompose **2.24.4以降**を使う。後述の`!override`によるポート置換に必要である。`docker --context desktop-linux info`でローカルのDocker Desktopへ接続できることも確認する。以後のコマンドは接続先を明示し、普段のDocker contextを切り替えずに実行する。

Gitが未導入なら、表示されるCommand Line Toolsの案内に従う。案内が出なければ`xcode-select --install`で導入し、完了後に`git --version`を再実行する。Mac本体へのRust・Cargo・PHP・MySQLのインストールは不要。

## デモ用のソースを取得する

以後はデモ専用のターミナルで作業する。デモディレクトリがすでにある場合は、初回作成を繰り返さず「停止と次回の再開」を使う。

```sh
RP_DEMO_ROOT="$HOME/recastpress-mac-demo-20261117"
mkdir "$RP_DEMO_ROOT"
```

次の取得方法のうち、どちらか一つを実行する。

### GitHubから取得する場合

```sh
git clone --single-branch --branch dev/v0.6 --no-checkout \
  https://github.com/KANIKAMA-Carcinization/RecastPress.git \
  "$RP_DEMO_ROOT/source"
```

非公開リポジトリの場合は、閲覧権限のあるGit認証が必要。認証情報をコマンドのURLへ直接書き込まない。

### 同じMacに既存リポジトリがある場合

下記の`/absolute/path/to/recastpress`だけを、既存リポジトリの絶対パスへ置き換える。`--no-hardlinks`でGitオブジェクトも独立してコピーする。

```sh
git clone --no-hardlinks --no-checkout \
  /absolute/path/to/recastpress \
  "$RP_DEMO_ROOT/source"
```

### 取得後にコミットを固定する

```sh
git -C "$RP_DEMO_ROOT/source" checkout --detach \
  45f77d7ce82699d7dcfb074310e592c278169367
git -C "$RP_DEMO_ROOT/source" rev-parse HEAD
git -C "$RP_DEMO_ROOT/source" status --short
ls "$RP_DEMO_ROOT/source/deploy/development/Dockerfile"
ls "$RP_DEMO_ROOT/source/deploy/development/compose.yaml"
```

`rev-parse`が指定したコミットを返し、`status --short`が空なら準備完了。コミットが見つからなければ、そのコミットを持つ既存リポジトリから取得する。別のコミットへ黙って置き換えない。

Dockerfileは、この取得によって配置されるビルド手順のファイルである。Dockerfile自体をインストールしたり、手書きで作り直したりする操作は不要。使用するのは[既存Dockerfile](../deploy/development/Dockerfile)と[既存Compose設定](../deploy/development/compose.yaml)。

## デモ用の操作コマンドを作る

専用ディレクトリへ操作用ファイルを作る。下記を`cat`から最後の`EOF`までまとめて貼り付ける。

```sh
cat > "$RP_DEMO_ROOT/demo.sh" <<'EOF'
RP_DEMO_ROOT="$HOME/recastpress-mac-demo-20261117"
RP_DEMO_SOURCE="$RP_DEMO_ROOT/source"
RP_DEMO_URL='http://localhost:18080/asset-demo?view=1'

rp_docker() {
  docker --context desktop-linux "$@"
}

rp_demo() (
  unset RECASTPRESS_BUILDER_IMAGE RECASTPRESS_RUNTIME_IMAGE
  unset RECASTPRESS_ARTIFACT_DIR RECASTPRESS_PUBLIC_HOST
  unset RP_DEMO_PLATFORM COMPOSE_PROFILES DOCKER_DEFAULT_PLATFORM
  rp_docker compose \
    --env-file "$RP_DEMO_ROOT/demo.env" \
    -p recastpress-mac-demo-20261117 \
    -f "$RP_DEMO_SOURCE/deploy/development/compose.yaml" \
    -f "$RP_DEMO_ROOT/compose.mac.yaml" "$@"
)
EOF

source "$RP_DEMO_ROOT/demo.sh"
```

`rp_demo`は、呼び出した処理の中だけで同名の環境変数を外し、デモ専用の設定ファイルを読み込む。普段の開発用環境変数を変更しない。

## Rustのベースイメージを取得して固定する

既存Dockerfileは、builderに**Rust 1.96.x**を要求する。ここでは取得候補を公式Rustイメージの`rust:1.96.0-bookworm`とし、Mac上で実際にpullできること、CPU、Rustバージョンを確認してから使う。`latest`への置換は行わない。

```sh
case "$(uname -m)" in
  arm64) RP_PLATFORM='linux/arm64' ;;
  x86_64) RP_PLATFORM='linux/amd64' ;;
  *) echo 'このCPUは本手順の対象外です' ;;
esac

RP_BASE_TAG='docker.io/library/rust:1.96.0-bookworm'
rp_docker pull --platform "$RP_PLATFORM" "$RP_BASE_TAG"
rp_docker image inspect "$RP_BASE_TAG" \
  --format '{{.Os}}/{{.Architecture}} {{json .RepoDigests}}'
```

Apple Siliconでは`linux/arm64`、Intelでは`linux/amd64`が期待値。Rosetta配下のターミナルでは`uname -m`が`x86_64`になる場合があるため、Apple Siliconではターミナルの「Rosettaを使用して開く」を外して最初から実行する。

pullに成功したイメージからdigest参照を取得し、その参照でRustを起動する。

```sh
RP_BASE_IMAGE="$(rp_docker image inspect "$RP_BASE_TAG" \
  --format '{{index .RepoDigests 0}}')"
printf '%s\n' "$RP_BASE_IMAGE"
rp_docker run --rm --platform "$RP_PLATFORM" \
  --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  "$RP_BASE_IMAGE" rustc --version
```

`repository@sha256:...`形式の実値と`rustc 1.96.0 ...`を確認する。タグの取得やCPU確認に失敗した場合はここで停止する。digestはダウンロードした内容を固定する値であり、イメージの信頼性や署名を自動検証するものではない。[pullとdigest指定の公式説明](https://docs.docker.com/reference/cli/docker/image/pull/)

デモではbuilderとruntimeに**同じdigestのイメージ**を指定する。ビルド時と実行時のlibc・共有ライブラリの差を避けるためで、runtimeにもRustツールが残り、イメージは大きくなる。配布用の軽量イメージを決める作業は別途行う。

## 公開データとMac用設定を配置する

固定サンプルをデモ専用ディレクトリへコピーする。開発側の元ファイルは編集しない。

```sh
mkdir "$RP_DEMO_ROOT/artifacts"
cp "$RP_DEMO_SOURCE/examples/sealed-assets-bundle.json" \
  "$RP_DEMO_ROOT/artifacts/public.json"
cp "$RP_DEMO_SOURCE/examples/sealed-assets-receipt.json" \
  "$RP_DEMO_ROOT/artifacts/public.receipt.json"
chmod 755 "$RP_DEMO_ROOT/artifacts"
chmod 644 "$RP_DEMO_ROOT/artifacts/public.json" \
  "$RP_DEMO_ROOT/artifacts/public.receipt.json"

printf 'RECASTPRESS_BUILDER_IMAGE=%s\nRECASTPRESS_RUNTIME_IMAGE=%s\nRECASTPRESS_ARTIFACT_DIR="%s"\nRECASTPRESS_PUBLIC_HOST=localhost:18080\nRP_DEMO_PLATFORM=%s\n' \
  "$RP_BASE_IMAGE" "$RP_BASE_IMAGE" "$RP_DEMO_ROOT/artifacts" "$RP_PLATFORM" \
  > "$RP_DEMO_ROOT/demo.env"
chmod 600 "$RP_DEMO_ROOT/demo.env"
```

`public.json`は公開する内容、`public.receipt.json`は対応するファイルの長さとSHA-256を照合するための記録。`artifacts/`には、この2ファイルだけを置く。

次に、既存Composeへ追加するMac専用設定を作る。

```sh
cat > "$RP_DEMO_ROOT/compose.mac.yaml" <<'EOF'
services:
  public:
    image: recastpress-mac-demo-20261117:local
    platform: "${RP_DEMO_PLATFORM:?Set the native Mac Linux platform}"
    ports: !override
      - "127.0.0.1:18080:8080"
EOF

rp_demo config
```

解決された設定で次を確認する。

- Mac側の公開先が`127.0.0.1:18080`だけで、元の`127.0.0.1:8080`が追加されていない。
- コンテナ側のlistenは`0.0.0.0:8080`、許可するHostは`localhost:18080`。
- `/artifacts`がデモ用ディレクトリからの読み取り専用マウントになっている。
- builderとruntimeが取得した同じdigestを参照している。

`!override`はポート一覧全体を置換する指定。通常の追加マージでは元の8080も公開されるため、この指定を使う。[Composeの置換ルール](https://docs.docker.com/reference/compose-file/merge/#replace-value)

ポートが空いていることも確認する。

```sh
lsof -nP -iTCP:18080 -sTCP:LISTEN
```

出力がなければ次へ進む。使用中なら、そのプロセスを止めず、`compose.mac.yaml`のMac側ポートと`demo.env`の`RECASTPRESS_PUBLIC_HOST`、`demo.sh`のURLを同じ空きポートへ変更する。コンテナ側の8080はそのままにする。変更後は`source "$RP_DEMO_ROOT/demo.sh"`と`rp_demo config`を再実行する。

## Dockerfileからビルドして起動する

```sh
rp_demo build public
rp_demo run --rm --no-deps -T public \
  check-package /artifacts/public.json /artifacts/public.receipt.json
```

初回はベースイメージやCargo依存の取得、Rustのreleaseビルドに時間がかかる。ホスト側の`target/`をマウントしないため、既存のCargoビルドと出力先を共有しない。

`check-package`が終了コード0でJSONの検査結果を返すことを確認する。サンプルはbundle version 3、3ページの構成。`run`では通常のサービス用ポートを公開しないため、起動前にデータを検査できる。[Compose runの仕様](https://docs.docker.com/reference/cli/docker/compose/run/)

```sh
rp_demo up -d --no-build --pull never public
rp_demo ps
rp_demo logs --tail 50 public
```

`public`が実行中で、ログに`listening on 0.0.0.0:8080`が出ることを確認する。管理用profileは起動しない。組み込みhealthcheckは無効なので、`ps`の実行中表示だけで成功とせず、次のHTTP確認まで行う。

## ブラウザとHTTP応答を確認する

まず公開準備が完了していることを確認する。`-4`はMac側でIPv4のloopbackへ接続する指定。

```sh
curl -4 -i http://localhost:18080/health/ready
curl -4 -i "$RP_DEMO_URL"
open "$RP_DEMO_URL"
```

期待する結果は次のとおり。

| 確認対象 | 期待する結果 |
| --- | --- |
| `/health/ready` | HTTP 200、本文`ready` |
| `/asset-demo?view=1` | HTTP 200、HTML、ETagヘッダー |
| ブラウザ | 見出し`Fixed sealed assets`と緑のカード |
| JavaScript | カード内に`JavaScript loaded.`と表示 |
| `/` | このサンプルでは未定義なので404 |

URLの`?view=1`も必要。`http://127.0.0.1:18080/asset-demo?view=1`をそのまま開くと、許可Hostの`localhost:18080`と一致せず421になる。IPへ直接接続する場合は次のようにHostを指定する。

```sh
curl -i -H 'Host: localhost:18080' \
  'http://127.0.0.1:18080/asset-demo?view=1'
```

HEADと条件付きGETも確認できる。

```sh
curl -4 -I "$RP_DEMO_URL"
RP_ETAG="$(curl -4 -fsSI "$RP_DEMO_URL" | \
  awk 'tolower($1) == "etag:" { gsub("\r", "", $2); print $2 }')"
printf '%s\n' "$RP_ETAG"
curl -4 -sS -o /dev/null -w '%{http_code}\n' \
  -H "If-None-Match: $RP_ETAG" "$RP_DEMO_URL"
```

HEADは200で本文なし、ETagを使ったGETは304が期待値。ここまで確認できれば、この固定サンプルのローカルデモは完了。

## 日本語の記事サンプルへ切り替える

記事テキストの表示も見たい場合は、publicを止めてから、デモ側の2ファイルを対で差し替える。

```sh
rp_demo stop public
cp "$RP_DEMO_SOURCE/examples/prepared-posts-bundle.json" \
  "$RP_DEMO_ROOT/artifacts/public.json"
cp "$RP_DEMO_SOURCE/examples/prepared-posts-receipt.json" \
  "$RP_DEMO_ROOT/artifacts/public.receipt.json"
rp_demo run --rm --no-deps -T public \
  check-package /artifacts/public.json /artifacts/public.receipt.json
rp_demo up -d --no-build --pull never public
open 'http://localhost:18080/prepared-post'
```

日本語とHTML風の文字列を含む固定記事を表示する。記事本文は文字としてエスケープされ、HTMLとして実行されない。CMSからの投稿・保存操作ではない。

最初のサンプルへ戻す場合も、停止してから`sealed-assets-bundle.json`と`sealed-assets-receipt.json`を同じコピー先へ戻し、検査・起動する。公開データは起動時に読み込むため、稼働中のファイル書き換えはそのまま表示へ反映されない。

## 停止と次回の再開

一時停止する場合：

```sh
rp_demo stop public
```

デモ用コンテナとネットワークを片付ける場合：

```sh
rp_demo down
```

どちらもデモ用ソース・設定・公開データとビルド済みイメージは残る。`rp_demo down`は専用プロジェクトだけを対象にする。Docker全体の停止、`docker system prune`、全コンテナの一括停止は行わない。

次回はDocker Desktopが起動している状態で、新しいターミナルから次を実行する。

```sh
source "$HOME/recastpress-mac-demo-20261117/demo.sh"
rp_demo up -d --no-build --pull never public
curl -4 -i http://localhost:18080/health/ready
open "$RP_DEMO_URL"
```

記事サンプルへ切り替えたままなら、ブラウザで開くURLは`http://localhost:18080/prepared-post`にする。Mac側ポートを変更した場合は、ここにある確認URLも変更する。保存したdigestのイメージを削除していなければ、次回のreleaseビルドは不要。

## うまく起動しないとき

| 症状 | 確認と対処 |
| --- | --- |
| `docker: command not found` | Docker Desktopを起動し、ターミナルを開き直す。CLIの配置はDocker Desktopの設定を確認する。 |
| `desktop-linux`がない、Engineへ接続できない | Docker Desktopが起動済みか、`docker context ls`と`docker --context desktop-linux info`を確認する。リモートの開発用contextへ切り替えて代用しない。 |
| `!override`の解析エラー | `docker compose version`を確認する。2.24.4未満なら対応版が必要。他の開発でDockerを使用中なら更新は別の時間に行う。 |
| `manifest unknown`／`no matching manifest` | Rustの指定タグとCPU向けイメージを取得できていない。ここで停止し、公式Rustイメージのタグ・architectureを確認する。 |
| Rustバージョンの拒否 | `demo.env`のbuilderをdigest参照で確認し、Rust 1.96.xのイメージを用意する。Dockerfileのバージョン検査は外さない。 |
| Cargo取得で失敗 | Docker Desktopのプロキシ設定、ネットワーク、registryとcrates.ioへの接続を確認する。`Cargo.lock`は変更しない。 |
| `port is already allocated` | `lsof`で18080の使用状況を確認し、デモ側のポート・Host・URLだけをそろえて変更する。 |
| `Mounts denied` | Docker Desktopのファイル共有設定で`~/recastpress-mac-demo-20261117/artifacts`を共有できるか確認する。共有設定の変更に再起動が必要なら、他の開発への影響を確認してから行う。 |
| ファイルがない／読めない | `demo.env`の公開ディレクトリが絶対パスか、2ファイルがあるか、ディレクトリ755・ファイル644かを確認する。 |
| `package check failed`、起動直後に終了 | `rp_demo logs --tail 100 public`を確認する。bundleとreceiptを同じサンプルからコピーし直し、`check-package`を再実行する。receiptは元bundleと対で使う。 |
| readinessが503 | 公開準備ができていない。`rp_demo config`でbundle／receipt引数とマウントを確認する。 |
| ページが421 | `localhost:18080`でアクセスしているか、許可Hostとポートが一致しているか確認する。 |
| ページが404 | `/asset-demo?view=1`の完全なURLを使う。記事サンプルへ切り替えた場合は`/prepared-post`を使う。 |
| ファイルを変えても表示が同じ | データを対で検査したうえで`rp_demo restart public`を実行する。起動済みプロセスはファイルを監視しない。 |

既存構成の詳細は[開発用Docker候補](../deploy/development/README.ja.md)、配信サンプルの内容は[固定CSSとJavaScript](sealed-public-assets-development.ja.md)、記事の準備は[公開準備記事](prepared-posts-development.ja.md)を参照。
