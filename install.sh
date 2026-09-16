#!/usr/bin/env bash
# install.sh — 安装 cw：把仓库里的 cw 链到 PATH，并让交互 shell 加载右 pane 的目录跟随逻辑。
#
# 设计上不复制脚本，而是 symlink 到本仓库，这样改完代码立即生效，不会出现
# 「仓库里是新的、装在 PATH 上的是旧的」。重复执行安全（幂等）。
#
# 用法:
#   ./install.sh              安装 / 更新
#   ./install.sh --uninstall  卸载（删 symlink 与 rc 里的加载行）
#   CW_BIN_DIR=~/bin ./install.sh    指定链接目录（默认 ~/.local/bin）
#   CW_SHELL=zsh ./install.sh        指定交互 shell（默认读 passwd）
set -euo pipefail

REPO=$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
BIN_DIR=${CW_BIN_DIR:-$HOME/.local/bin}
LINK=$BIN_DIR/cw
MARK='# cw: claude agents 工作台——右侧 shell 跟随当前 attach session 的目录'
LOAD="if [[ -n \$CW_DIRFILE ]]; then source '$REPO/cw.zsh'; fi"

say()  { printf '%s\n' "$*"; }
warn() { printf '警告: %s\n' "$*" >&2; }
die()  { printf '错误: %s\n' "$*" >&2; exit 1; }

# 登录 shell：优先读 passwd，别信 $SHELL —— 某些环境（如 Claude Code 设了
# CLAUDE_CODE_SHELL 之后）会改写 $SHELL，据此判断会误判用户的交互 shell。
# 可用 CW_SHELL=zsh ./install.sh 手动指定。
login_shell() {
  local s=${CW_SHELL:-}
  [ -n "$s" ] || s=$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f7 || true)
  [ -n "$s" ] || s=${SHELL:-}
  printf '%s\n' "${s##*/}"
}

rc_file() {
  case $(login_shell) in
    zsh)  printf '%s\n' "$HOME/.zshrc" ;;
    bash) printf '%s\n' "$HOME/.bashrc" ;;
    *)    printf '' ;;
  esac
}

uninstall() {
  [ -L "$LINK" ] && { rm -f "$LINK"; say "已删除 $LINK"; } || say "未安装 $LINK"
  local rc; rc=$(rc_file)
  if [ -n "$rc" ] && [ -f "$rc" ] && grep -qF 'cw.zsh' "$rc"; then
    cp "$rc" "$rc.bak-$(date +%Y%m%d%H%M%S)"
    grep -vF 'cw.zsh' "$rc" | grep -vF "$MARK" > "$rc.tmp" && mv "$rc.tmp" "$rc"
    say "已从 $rc 移除加载行（原文件已备份）"
  fi
  say "卸载完成。运行中的工作台不受影响，用 cw -k 关闭。"
  exit 0
}

[ "${1:-}" = "--uninstall" ] && uninstall
[ -n "${1:-}" ] && die "未知参数: $1（支持 --uninstall）"

# ---- 1) 依赖检查 ----
miss=()
for c in tmux python3 flock pgrep; do command -v "$c" >/dev/null 2>&1 || miss+=("$c"); done
[ ${#miss[@]} -gt 0 ] && die "缺少依赖: ${miss[*]}"
command -v claude >/dev/null 2>&1 || warn "PATH 里找不到 claude，cw 启动后左 pane 会报错"
tmux -V | grep -qE 'tmux (3\.[2-9]|[4-9])' || warn "建议 tmux >= 3.2（当前 $(tmux -V)）"

# ---- 2) 链接到 PATH ----
mkdir -p "$BIN_DIR"
if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
  die "$LINK 已存在且不是符号链接，请先手动处理"
fi
ln -sfn "$REPO/cw" "$LINK"
say "已链接 $LINK -> $REPO/cw"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR 不在 PATH 中，请自行加入" ;;
esac

# ---- 3) 交互 shell 的加载行 ----
rc=$(rc_file)
if [ "$(login_shell)" != zsh ]; then
  say "当前登录 shell 是 $(login_shell)（非 zsh）：跳过 cw.zsh 安装。"
  say "  若判断有误，用 CW_SHELL=zsh ./install.sh 重跑。"
  say "  右 pane 会自动降级为 send-keys 方式切目录，功能不受影响，"
  say "  区别只是 cd 命令会出现在 shell 历史里，且不会在你输入到一半时静默切换。"
elif [ -z "$rc" ] || [ ! -f "$rc" ]; then
  warn "找不到 $HOME/.zshrc，请手动加入下面一行：\n  $LOAD"
else
  if grep -qF "$REPO/cw.zsh" "$rc"; then
    say "$rc 已包含本仓库的加载行，跳过"
  else
    cp "$rc" "$rc.bak-$(date +%Y%m%d%H%M%S)"
    if grep -qF 'cw.zsh' "$rc"; then          # 旧安装（指向别处）→ 就地替换
      grep -vF 'cw.zsh' "$rc" | grep -vF "$MARK" > "$rc.tmp" && mv "$rc.tmp" "$rc"
      say "已移除 $rc 中指向旧路径的加载行"
    fi
    printf '\n%s\n%s\n' "$MARK" "$LOAD" >> "$rc"
    say "已向 $rc 追加加载行（原文件已备份）"
  fi
fi

# ---- 4) 收尾 ----
say ""
say "安装完成。用法："
say "  cw          进入工作台（已存在则复用，不会新建第二个）"
say "  cw -s       查看状态    cw -r  重启 Agent View    cw -k  关闭"
say ""
say "注意：已经开着的右 pane 不会自动加载跟随逻辑，重开一个 shell 或重启工作台（cw -k && cw）后生效。"
