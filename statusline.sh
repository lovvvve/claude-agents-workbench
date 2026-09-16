#!/usr/bin/env bash
# Claude Code 状态栏
#
# 骨架取自 oh-my-zsh bira 主题（ZSH_THEME="bira"）的配色与 git 写法，
# 信息量对齐 ccstatusline 的默认布局（~/.config/ccstatusline/settings.json）：
#   第 1 行  version · model · effort · cwd · git-branch · git-changes · session-name
#   第 2 行  context-bar · session-usage · reset-timer · weekly-usage · weekly-reset · session-cost
#
# 两条硬约束，改这个脚本时别破坏：
#   1) 第 1 行必须同时出现 vX.Y.Z 版本号和「绝对路径」形式的工作目录；
#   2) 目录不要缩写成 ~ 。
# 原因：本仓库的 cw-follow 靠「同一行里既有 vX.Y.Z、又有能通过
# os.path.isdir() 的目录」这条启发式，从左 pane 抓出当前 attach 的 session 的 cwd。
# 缩写过的 ~/xxx 过不了 isdir()，去掉版本号同样会让识别失效。
#
# JSON 解析走 python3（不依赖 jq）。

input=$(cat)
printf '%s' "$input" | python3 -c '
import json, os, subprocess, sys, time

try:
    d = json.load(sys.stdin)
except Exception:
    d = {}

R  = "\033[0m";   B  = "\033[1m";   DIM = "\033[2m"
GRN= "\033[32m";  RED= "\033[31m";  BLU = "\033[34m"
YLW= "\033[33m";  CYN= "\033[36m";  MAG = "\033[35m"
BGRN="\033[92m";  WHT= "\033[37m"

def g(path, default=None):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur

# ---------- 第 1 行 ----------
ver   = g("version", "")
model = (g("model.display_name", "") or "").split(" (")[0]   # "Opus 5 (1M context)" -> "Opus 5"
eff   = g("effort.level", "")
cwd   = g("workspace.current_dir") or g("cwd") or os.getcwd()
sname = g("session_name", "")

user = os.environ.get("USER") or "?"
try:
    host = os.uname().nodename.split(".")[0]
except Exception:
    host = "?"
ucol = RED if os.geteuid() == 0 else GRN

def git(*args):
    try:
        r = subprocess.run(["git", "-C", cwd, "--no-optional-locks", *args],
                           capture_output=True, text=True, timeout=2)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""

branch = git("symbolic-ref", "--quiet", "--short", "HEAD") or git("rev-parse", "--short", "HEAD")
gitpart = ""
if branch:
    porcelain = git("status", "--porcelain")
    dirty = f"{RED}●" if porcelain else ""
    # git-changes：统计改动行数，对齐 ccstatusline 的 (+n,-n)
    numstat = git("diff", "--numstat", "HEAD")
    add = rem = 0
    for ln in numstat.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 2:
            if parts[0].isdigit(): add += int(parts[0])
            if parts[1].isdigit(): rem += int(parts[1])
    changes = f" {YLW}(+{add},-{rem}){R}" if (add or rem) else ""
    gitpart = f" {YLW}‹{branch}{dirty}{YLW}›{R}{changes}"

head = ""
if ver:   head += f"{DIM}v{ver}{R} "
if model: head += f"{RED}{model}{R} "
if eff:   head += f"{MAG}{eff}{R} "

line1 = f"{head}{B}{ucol}{user}@{host}{R} {B}{BLU}{cwd}{R}{gitpart}"
if sname:
    line1 += f" {DIM}· {sname}{R}"
print(line1)

# ---------- 第 2 行 ----------
def human(n):
    n = n or 0
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n//1000}k"
    return str(n)

def countdown(ts):
    if not ts: return ""
    try:
        left = int(ts) - int(time.time())
    except Exception:
        return ""
    if left <= 0: return "0m"
    if left > 90 * 86400: return ""        # 明显异常的时间戳，宁可不显示
    d, rem = divmod(left, 86400)
    h, m = divmod(rem // 60, 60)
    if d: return f"{d}d {h}h"
    if h: return f"{h}h {m}m"
    return f"{m}m"

used_pct = g("context_window.used_percentage")
tot_in   = g("context_window.total_input_tokens", 0)
win      = g("context_window.context_window_size", 0)

seg = []
if used_pct is not None and win:
    width  = 16
    filled = max(0, min(width, round(used_pct * width / 100)))
    col    = BGRN if used_pct < 60 else (YLW if used_pct < 85 else RED)
    bar    = f"{col}{chr(9608)*filled}{DIM}{chr(9617)*(width-filled)}{R}"
    seg.append(f"[{bar}] {human(tot_in)}/{human(win)} ({used_pct}%)")

s_pct = g("rate_limits.five_hour.used_percentage")
if s_pct is not None:
    t = countdown(g("rate_limits.five_hour.resets_at"))
    seg.append(f"{CYN}Session: {s_pct}%{R}" + (f" {DIM}{t}{R}" if t else ""))

w_pct = g("rate_limits.seven_day.used_percentage")
if w_pct is not None:
    t = countdown(g("rate_limits.seven_day.resets_at"))
    seg.append(f"{BLU}Week: {w_pct}%{R}" + (f" {DIM}{t}{R}" if t else ""))

cost = g("cost.total_cost_usd")
if cost:
    seg.append(f"{GRN}${cost:.2f}{R}")

if seg:
    print("  ".join(seg))
'
