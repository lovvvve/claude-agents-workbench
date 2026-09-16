# 踩坑记录

开发 cw 过程中实际踩到并修掉的问题。改代码前扫一眼，别让它们复发。

## tmux

**1. `base-index` 不为 0 时不能用数字索引**

`tmux -t "<session>:0"` 在 `base-index 1` 的配置下报 `can't find window`。一律用 `%N` 形式的 pane id，或按 pane 选项动态查找。

**2. `-t` 默认做前缀匹配**

`has-session -t cw` 会命中 `cw-api`。精确匹配要写 `-t "=cw"`。

**3. `set-option -p` 作用于 active pane，不是执行命令的那个 pane**

`cw` 启动后把左 pane 设为 active，于是右 pane 的 shell 里执行 `tmux set-option -p @cw_ready 1`，标记打到了**左** pane 上。必须显式指定：`set-option -p -t "$TMUX_PANE"`。

**4. session 级自定义选项不可靠**

`tmux set-option -t '=<sess>' @foo bar` 在 3.5a 上报 `no such session`。别用它存状态，改用 `flock`（还顺带解决了 pid 复用和陈旧记录清理的问题）。

**5. `respawn-pane -k` 会保留 pane 选项**

这是好事：重启左 pane 后 `@cw_role` 还在，不用重设。

**6. `shell -c "A; exec B"` 里 `pane_current_command` 恒为 shell 名**

非交互 shell 不做 job control，子进程 A 不会成为前台进程组，所以 tmux 看到的一直是 shell。想知道 A 在不在跑，得查进程树：`pgrep -P <pane_pid> -f 'claude agents'`。

## shell

**7. zsh 不做单词分割**

```bash
V=%9
bash: ${V:+-t $V}  →  两个参数: "-t" "%9"
zsh:  ${V:+-t $V}  →  一个参数: "-t %9"     # tmux 收到后无效
```

写 `.zshrc` 里 source 的文件时尤其要小心，用 `if` 显式展开。

**8. `${$(cmd):-x}` 是 zsh 语法**

bash 不支持，会语法错误。先赋值再 `${var:-x}`。

**9. `set -eu` 下末行的 `[[ cond ]] && cmd`**

条件为假时整条语句返回非零，脚本以非零码退出（哪怕该做的事都做完了）。用 `if/fi`。`A && B && C` 整条失败同理。

**10. flock 的 fd 会被 `exec` / `nohup` 的子进程继承**

`cw` 持锁期间 `exec tmux attach`，锁会一直被 attach 进程持有 → 下次运行 `cw` 卡住。必须在 exec 前 `exec 9>&-`，给 watcher 也要 `nohup ... 9>&-`。

**11. `pkill -f <pattern>` 会匹配到你自己**

在一条命令里既 `echo "...cw-follow..."` 又 `pkill -f cw-follow`，pkill 会把当前 shell 自己杀掉（命令行里含该字符串）。表现是 exit 144 加输出被截断。模式用变量拼：`P="cw-fol"; P="${P}low"`，且同一条命令里别出现该字面串。

## 解析

**12. statusline 用 NBSP（U+00A0）分隔**

```
 v2.1.272 Opus 5 xhigh /home/you/dir  ⎇ master
        ↑ 这些都是 U+00A0，不是空格
```

bash / grep 的 `[[:space:]]` 和 `[^ ]*` 都不认 NBSP —— `grep -oE '/home/[^ ]*'` 会从路径一路吃到行尾。用 Python 的 `str.split()`（它把 NBSP 当空白）。写测试脚本时同样别图省事用 grep。

**13. 别信「路径 ∈ 已知 cwd 列表」这种回退**

早期版本在读不到 statusline 时，退而求其次地找「屏幕上任何一个等于某 session cwd 的路径」。结果 Agent View 加载瞬间（页脚还没渲染出来、分类器判定为非列表态）命中了列表里的目录分组标题，把右 pane cd 到了不相干的 worktree。已删除，只信 statusline 行。

**14. `python3 - <<'PY'` 会占用 stdin**

管道数据进不去。要把屏幕内容喂给 Python 解析，用 `python3 -c "$CLASSIFY"`。

## Claude Code

**15. 别往运行中的 Agent View `send-keys` 文本**

它会落进 dispatch 输入框并**真的派发一个后台 session**。开发时误建过一个叫「claude agents memory script」的 session，白烧了几千 token。自动化脚本必须先确认已退出（屏幕无 Agent View 特征 **且** 进程树里没有 `claude agents`）再敲命令。

误建了用 `claude stop <id>` 停掉、`claude rm <id>` 删除。

**16. 退出 Agent View 是按一次 `Esc`**

不是 `Ctrl+C` 两次（文档里 `Ctrl+C` 描述的是清空输入）。
