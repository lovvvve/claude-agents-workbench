#!/usr/bin/env bash
# cw — Claude agents 工作台（幂等）
#   左 pane = claude agents（Agent View / attach），右 pane = 自动跟随当前 attach session 目录的 shell
#   重复执行只会回到同一个工作台并自愈缺失的部分，不会新建第二个。
set -eu
SELF=$(readlink -f -- "$0" 2>/dev/null || printf %s "$0")   # 经 symlink 启动需解析真实路径
BIN=$(cd -- "$(dirname -- "$SELF")" && pwd)

usage() {
  cat <<'EOF'
用法: cw [-n 名字] [-r] [-s] [-k] [-h]
  (无参数)   复用或创建工作台 cw，并进入；已存在则自愈缺失的 pane / watcher
             退出 Agent View 后左 pane 会落回 shell（不会自动拉回），可直接敲
             claude agents 重启，或用 cw -r
  -n 名字    使用独立工作台 cw-<名字>（可并存多个工作台）
  -r         重启左 pane 的 Agent View（claude 升级后用；后台 agent 不受影响）
  -s         只打印状态，不进入
  -k         关闭该工作台（session + watcher + 临时文件）
EOF
}
NAME='' MODE=up
while getopts 'n:rskh' o; do
  case $o in
    n) NAME=$OPTARG ;; r) MODE=restart ;; s) MODE=status ;; k) MODE=kill ;; h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
[ $# -gt 0 ] && { echo "cw: 多余参数: $*" >&2; usage >&2; exit 2; }
case $NAME in *[^A-Za-z0-9_-]*) echo "cw: -n 只允许字母数字和 _ -" >&2; exit 2 ;; esac

SESS=cw${NAME:+-$NAME}
RUN=${XDG_RUNTIME_DIR:-/tmp}
DIRFILE=$RUN/cw-dir.$SESS
# 退出 Agent View 后落回交互 shell：pane 与 session 都留着，可直接再敲 claude agents
# （claude 升级后重启就靠这个；不写 exec 的话 pane 会随命令结束而关闭）
LEFT_CMD="claude agents; exec ${SHELL:-/bin/bash}"

has()  { tmux has-session -t "=$SESS" 2>/dev/null; }               # = 前缀 = 精确匹配，避免 cw 命中 cw-foo
pane() { tmux list-panes -t "=$SESS" -F '#{@cw_role} #{pane_id}' 2>/dev/null | awk -v r="$1" '$1==r{print $2; exit}'; }
wpid() { pgrep -f "cw-follow $SESS " 2>/dev/null | head -1; }
# 左 pane 跑的是 `<shell> -c "claude agents; exec <shell>"`：非交互 shell 不做 job control，
# pane_current_command 恒为 shell 名，故用进程树判断 Agent View 是否还活着
agents_alive() {
  local pp; pp=$(tmux display -p -t "$1" '#{pane_pid}' 2>/dev/null) || return 1
  [ -n "$pp" ] && pgrep -P "$pp" -f 'claude agents' >/dev/null 2>&1
}

case $MODE in
  status)
    if has; then
      w=$(wpid || true)
      L=$(pane left)
      if [ -n "$L" ] && agents_alive "$L"; then lst='Agent View 运行中'; else lst='已退出（左 pane 是 shell，敲 claude agents 或 cw -r 重启）'; fi
      printf '%s: 运行中  left=%s  right=%s  watcher=%s\n  左 pane: %s\n  当前目录=%s\n' \
        "$SESS" "${L:-无}" "$(pane right)" "${w:-无}" "$lst" "$(cat "$DIRFILE" 2>/dev/null || echo 无)"
    else
      echo "$SESS: 未运行"
    fi
    other=$(tmux ls -F '#{session_name}' 2>/dev/null | grep -E '^cw[0-9]+$' | tr '\n' ' ' || true)
    if [ -n "$other" ]; then echo "  提示: 存在旧式 session（pid 命名，旧版 cw 遗留）: $other"; fi
    exit 0 ;;
  restart)
    if has; then
      L=$(pane left)
      if [ -n "$L" ]; then
        tmux respawn-pane -k -t "$L" -c "$PWD" "$LEFT_CMD"   # respawn 保留 @cw_role
        echo "已重启 $SESS 的 Agent View（后台 agent 不受影响）"
      else echo "$SESS 没有左 pane，直接跑 cw 重建" >&2; exit 1; fi
    else echo "$SESS 未运行，直接跑 cw 启动" >&2; exit 1; fi
    exit 0 ;;
  kill)
    if has; then tmux kill-session -t "=$SESS"; echo "已关闭 $SESS"; else echo "$SESS 未运行"; fi
    pkill -f "cw-follow $SESS " 2>/dev/null && echo "已停 watcher" || true
    rm -f "$DIRFILE" "$DIRFILE.wlock"
    exit 0 ;;
esac

# ---- 以下为 up：并发执行时用锁串行化，避免两次 cw 同时构建 ----
exec 9>"$RUN/cw-$SESS.lock"
# 合法持锁都在亚秒级（attach 前就释放），等不到 = 被泄漏的 fd 占着；锁按 inode 生效，删文件即解
flock -w 5 9 || { echo "cw: 锁 $RUN/cw-$SESS.lock 被占用（多半是旧 tmux server 继承了 fd）。rm 掉它再重试；fuser -v 可查持有者" >&2; exit 1; }

if ! has; then
  # 9>&-：没有 server 时 new-session 会 fork 出常驻的 tmux server，不关 fd 它会永久持锁
  tmux new-session -d -s "$SESS" \
    -x "$(tput cols 2>/dev/null || echo 200)" -y "$(tput lines 2>/dev/null || echo 50)" \
    -c "$PWD" "$LEFT_CMD" 9>&-
  tmux set-option -p -t "$(tmux list-panes -t "=$SESS" -F '#{pane_id}' | head -1)" @cw_role left
fi

# 左 pane：缺标记则认领第一个 pane；整个 pane 没了才重建。
# 若它已落回 shell（你退出了 Agent View），保持原样 —— 重启请敲 claude agents 或 cw -r
L=$(pane left)
if [ -z "$L" ]; then
  first=$(tmux list-panes -t "=$SESS" -F '#{@cw_role} #{pane_id}' | awk '$1!="right"{print $2; exit}')
  if [ -n "$first" ]; then
    L=$first
  else                                    # 只剩右 pane：在它左边插回一个
    R0=$(pane right)
    L=$(tmux split-window -h -b -l 60% -t "$R0" -c "$PWD" -P -F '#{pane_id}' "$LEFT_CMD")
  fi
  tmux set-option -p -t "$L" @cw_role left
fi

# 右 pane：缺则补一个（CW_DIRFILE 走固定路径，故复用旧 pane 时也一致）
R=$(pane right)
if [ -z "$R" ]; then
  R=$(tmux split-window -h -l 40% -t "$L" -c "$PWD" -e "CW_DIRFILE=$DIRFILE" -P -F '#{pane_id}')
  tmux set-option -p -t "$R" @cw_role right
fi

# watcher：无脑尝试启动，多余的会因为拿不到自己的 flock 而立即自退（故天然单例）
# 9>&- 防止 watcher 继承本脚本的锁，否则 attach 期间锁不释放
nohup "$BIN/cw-follow" "$SESS" "$DIRFILE" >/dev/null 2>&1 9>&- &

tmux select-pane -t "$L"
exec 9>&-                                                   # 进入前释放锁，让下一次 cw 不被阻塞
if [ -n "${TMUX:-}" ]; then exec tmux switch-client -t "=$SESS"; else exec tmux attach -t "=$SESS"; fi
