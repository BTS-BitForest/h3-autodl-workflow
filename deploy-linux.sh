#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
REPO=BTS-BitForest/h3-autodl-workflow
TAG=v2026.09.30
HOLD=${1:-}
if [[ $(id -u) != 0 ]]; then echo '请在 AutoDL 服务器以 root 运行。'; exit 1; fi
if [[ $(uname -m) != x86_64 ]]; then echo '仅支持 Linux x86_64。'; exit 1; fi
BASE=/root/autodl-tmp/h3-deploy
mkdir -p "$BASE"
exec 9>"$BASE/deploy.lock"
flock -n 9 || { echo '另一个部署程序正在运行。'; exit 1; }
if [[ ! -x /opt/h3-suite/venv/bin/python ]]; then
  if [[ -e /opt/h3-suite || -e /opt/h3-python ]]; then echo '发现未完成或不同的安装，请检查后再部署；不会覆盖。'; exit 1; fi
  python3 - <<'CHECK'
import os,shutil
assert os.stat('/root/autodl-tmp').st_dev!=os.stat('/').st_dev, '需要独立 AutoDL 数据盘'
assert shutil.disk_usage('/opt').free>20*1024**3, '系统盘至少预留20GiB'
assert shutil.disk_usage('/root/autodl-tmp').free>100*1024**3, '数据盘至少预留100GiB'
CHECK
  if ! command -v curl >/dev/null; then apt-get update -qq; apt-get install -y curl; fi
  echo '从公开 GitHub Release 下载，无需 GitHub 账号。'
  curl --fail --location --retry 3 --connect-timeout 30 --speed-limit 1024 --speed-time 60 "https://github.com/$REPO/archive/refs/tags/$TAG.tar.gz" -o "$BASE/source.tar.gz"
  mkdir -p "$BASE/source" "$BASE/assets"
  tar -xzf "$BASE/source.tar.gz" --strip-components=1 -C "$BASE/source"
  python3 "$BASE/source/install/download_assets.py" --repo "$REPO" --tag "$TAG" --output "$BASE/assets"
  python3 "$BASE/source/install/install.py" --assets "$BASE/assets"
fi
flock -u 9
/opt/h3-suite/venv/bin/python /opt/h3-suite/scripts/launch_session.py
python3 /opt/h3-suite/scripts/watch_download.py
if [[ "$HOLD" == --hold ]]; then
  echo '面板连接已保持；关闭此窗口断开本地隧道，服务器任务继续运行。'
  while sleep 30; do :; done
fi
