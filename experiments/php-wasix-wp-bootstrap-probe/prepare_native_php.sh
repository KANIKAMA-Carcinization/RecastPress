#!/usr/bin/env bash
# Build a native CLI for the hook oracle from the official PHP source release.
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
work="$root/target/php-wasix-wp-bootstrap-probe/native-php"
mkdir -p -- "$work"
build_dir=$(mktemp -d "$work/build.XXXXXX")
archive="$build_dir/php-8.5.12.tar.xz"

curl --fail --silent --show-error --location \
  --proto '=https' --proto-redir '=https' --connect-timeout 30 --max-time 300 \
  --retry 2 --retry-max-time 600 --max-filesize 33554432 \
  --output "$archive" https://www.php.net/distributions/php-8.5.12.tar.xz
# https://discourse.thephp.foundation/t/announce-php-8-5-12-released/6288
printf '%s  %s\n' '08bc2eba2a588654ade1357174aa646b761556c862dda0678345ee29ed481209' "$archive" \
  | sha256sum --check
tar -xJf "$archive" -C "$build_dir"
cd -- "$build_dir/php-8.5.12"

CFLAGS=-O1 ./configure --disable-all --enable-cli --disable-cgi --disable-phpdbg
make -j2 sapi/cli/php
test "$(sapi/cli/php -n -r 'echo PHP_VERSION_ID;')" = 80512
install -m 0755 sapi/cli/php "$build_dir/php"
mv -f -- "$build_dir/php" "$work/php"
