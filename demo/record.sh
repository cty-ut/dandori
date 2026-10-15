#!/bin/bash
# デモの録画（README の動画）。リポジトリの直下で：bash demo/record.sh full
#
# 準備（例を一時フォルダに写す）はこのスクリプトが行い、VHS には表示するだけのコマンドしか打たせない。
# VHS は文字をキー入力として送るため、設定が合わないと日本語が編集キーとして解釈され、
# 打った命令が変わってしまうことがある。危ない命令（削除など）は .tape に絶対に書かない。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d -t dandori-demo)"
cp -R "$ROOT/examples/入金消込/." "$WORK/"
cd "$WORK"
# .tape の中の @ROOT@ をリポジトリの場所に置き換えて、作業フォルダで実行する
for tape in "$ROOT"/demo/*.tape; do
  sed "s|@ROOT@|$ROOT|g" "$tape" > "$WORK/$(basename "$tape")"
done
vhs "$WORK/${1:-full}.tape"
echo "録画しました：$ROOT/demo/${1:-full}.gif（作業フォルダ：$WORK）"
