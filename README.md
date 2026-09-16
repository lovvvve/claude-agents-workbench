# cw — Claude Code Agent View 工作台

把 `claude agents`（Agent View）放进 tmux 左半边，右半边给你一个**自动跟随当前 attach session 工作目录**的 shell。

```
┌──────────────────────────────────────────┬──────────────────────────┐
│ claude agents                            │ ~/deploy-argocd          │
│                                          │ $ git log --oneline -3   │
│   ✻ implement ticket 25        Working   │ 3c84dfe fix ingress      │
│   ∙ ask matt skills            Done      │ 4ceb5ff add lint         │
│   ✢ architecture migration     Working   │ $ kubectl get pods       │
│                                          │ ...                      │
│ v2.1.273 Opus 5 max you@host             │ cw → ~/deploy-argocd     │
│ /home/you/deploy-argocd ‹master›         │                          │
└──────────────────────────────────────────┴──────────────────────────┘
          左：原生 Agent View                  右：跟着切目录的 shell
```

在左边 attach 进某个 agent，右边立刻 `cd` 到那个 agent 的工作目录（含 git worktree）；`←` 回列表时右边保持不动；换一个 agent，右边跟着换。

## 解决什么问题

SSH 上服务器跑 `claude agents`，进到某个 session 之后，这个终端窗口就被 Agent View 占满了。想看一眼 `git log`、`kubectl get pods`、跑个测试，都得另开窗口再手动 `cd` 过去。agent 一多，「我现在在哪个目录」本身就成了负担。

## 安装

```bash
git clone <this-repo> ~/claude-agents-workbench
cd ~/claude-agents-workbench
./install.sh
```

`install.sh` 把 `cw` symlink 到 `~/.local/bin`（指向仓库，改完代码即生效），并往 `~/.zshrc` 追加一行加载器。它是幂等的，能就地迁移旧安装；卸载用 `./install.sh --uninstall`。

还需要配一个满足条件的 statusline，见 [statusline 要求](#statusline-要求)。

**依赖**：`tmux`（建议 ≥ 3.2）、`python3`、`flock`、`pgrep`、`claude`。不需要 `jq`。

**shell**：右 pane 用 zsh 时体验最好——通过 `SIGUSR1` + `zle` 切目录，不会打断你正在敲到一半的命令。其他 shell 自动降级为 `send-keys cd`，功能一样，只是 `cd` 会进 shell 历史，且仅在空闲提示符下切换（不会打断 `vim`、`tail -f`）。

## 用法

```bash
cw            # 进入工作台；已存在则复用并自愈，不会新建第二个
cw -n api     # 独立的第二个工作台（cw-api），互不干扰
cw -r         # 重启左 pane 的 Agent View（claude 升级后用）
cw -s         # 查看状态，不进入
cw -k         # 关闭工作台
cw -h         # 帮助
```

**退出与重启**：在 Agent View 里按 `Esc`（一次即可）退出后，左 pane 会落回 shell，pane 和 session 都留着。直接敲 `claude agents` 就能重新进去——claude 升级后就这么重启。`cw` **不会**把你主动退出的左 pane 强行拉回 Agent View。

**幂等**：重复执行 `cw` 只会回到同一个工作台，并补齐缺失的部分——右 pane 被关掉会补回，watcher 挂了会重启（且只会有一个），并发执行由 `flock` 串行化。

## 组成

| 文件 | 作用 |
|---|---|
| `cw` | 启动器。建/复用 tmux 工作台，自愈 pane 与 watcher，`-n/-r/-s/-k` 都在这里 |
| `cw-follow` | 后台 watcher。每 0.7s 抓一次左 pane，识别出当前目录后通知右 pane |
| `cw.zsh` | 右 pane zsh 的 `SIGUSR1` 陷阱，收到信号就 `cd` 并重绘提示符 |
| `statusline.sh` | 可选但推荐的状态栏实现，保证左 pane 上有 cw 需要的识别特征 |
| `install.sh` | 安装 / 迁移 / 卸载 |

## 工作原理

核心难点：**Claude Code 不对外暴露「当前 attach 的是哪个 session」**。

排查过的信号源：

| 信号源 | 结论 |
|---|---|
| `claude agents --json` | 有每个 session 的 `cwd`，但没有「哪个正被 attach」 |
| `~/.claude/jobs/<id>/state.json` | `firstTerminalAt` / `lastTerminalAt` 是终态时间，不是 attach 事件 |
| `~/.claude/daemon/roster.json` | 只有 worker 元数据 |
| `~/.claude/daemon/attach-journal/*.json` | 只记录手势的 pid / surface / via，**不含目标 session** |
| `~/.claude/sessions/<pid>.json`、transcript、`daemon.log` | 均无 |
| attach 客户端进程 | 不 chdir、不改终端标题 |
| pty / socket 拓扑 | 全部经 daemon 的 `control.sock` 中转，看不出对应关系 |
| statusline 的 stdin JSON | 有 `session_id` / `cwd` / `worktree.*`，但**没有「我是否正被 attach」这一位**；后台 session 同样会跑 statusline |
| hooks | 有 `CwdChanged`，但没有 attach 相关事件 |
| 终端 winsize | 未能证实可用于区分（试过「只有被 attach 的 REPL 跟随 resize」这条思路，没验证成功） |

唯一可靠的信号是物理层面的：**被 attach 的那个 session 的画面，就显示在左 pane 上**。而它底部的 statusline 明文写着工作目录。

于是 `cw-follow` 每 0.7s `tmux capture-pane` 左 pane：

- 屏幕含 Agent View 页脚特征（`enter to return · space to reply` 等）→ 列表态，保持不动；
- 否则在底部 6 行里找**同一行既含 `vX.Y.Z` 版本号、又含一个真实存在的绝对路径**的那一行 → 那就是当前 session 的 cwd。

目录变了就写进 `$XDG_RUNTIME_DIR/cw-dir.<工作台名>`，然后通知右 pane：若右 pane 的 shell 已加载 `cw.zsh`（pane 选项 `@cw_ready=1`），发 `SIGUSR1`，由 `TRAPUSR1` 执行 `cd` + `zle reset-prompt`；否则退回 `send-keys cd`。

两个 pane 用 tmux pane 选项 `@cw_role=left/right` 标记，watcher 每轮动态查找——所以 pane 被关掉重建后 watcher 不必重启。watcher 自身用 `flock -n` 保证单例，`cw` 可以无脑启动它，多余的会自己退出。

## statusline 要求

**这是 cw 能否工作的前提。**

识别条件：左 pane 底部 6 行内，同一行里既有 `vX.Y.Z` 版本号，又有一个**绝对路径**形式的真实目录（`~/xxx` 这种缩写过不了存在性检查）。

这里要分清两层：**机制**属于 Claude Code 自身——`settings.json` 里的 `statusLine` 由它调用，喂一份 JSON 到命令的 stdin，再把命令的 stdout 渲染到界面底部；**但显示什么完全由那个命令决定**。实测把 `statusLine` 换成空输出后，Claude Code 内置底部只剩 `⏵⏵ auto mode on · ← N agents` 这类徽章，**不含工作目录**。

所以你需要配一个会打印版本号和绝对路径的 statusline。两种做法：

**① 用本仓库的 `statusline.sh`**

```
v2.1.273 Opus 5 max you@host /home/you/project ‹master●› · 会话名
[███████░░░░░░░░░] 435k/1.0M (44%)  Session: 21% 6m  Week: 18% 14h 0m  $40.62
```

第 1 行：版本 · 模型 · effort · `user@host` · 绝对路径 · git 分支（脏仓库带红 `●`）· 改动行数 · 会话名
第 2 行：context 进度条（按用量变色）· 5 小时窗口用量与重置倒计时 · 7 天窗口 · 累计费用

配色骨架取自 oh-my-zsh 的 `bira` 主题，信息量对齐 ccstatusline 的常见布局。只依赖 `python3` 和 `git`，渲染约 35ms。

```bash
ln -sfn ~/claude-agents-workbench/statusline.sh ~/.claude/statusline.sh
```

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash ~/.claude/statusline.sh",
    "padding": 0,
    "refreshInterval": 10
  }
}
```

**② 用 [ccstatusline](https://github.com/sirmalloc/ccstatusline)**，并确保 `version` 与 `current-working-dir` 两个 widget 都开着。

条件不满足时，`cw -s` 的「当前目录」会一直是空，右 pane 不跟随。

> 改 `statusline.sh` 时请保留第 1 行的版本号和绝对路径，脚本开头的注释里写明了原因。

## 已知限制

- **不跟随列表里的光标**：只有真正 attach 进某个 session 才切目录，在列表里上下移动不会切（选中行的 SGR 是 `48;5;237`，想要的话可以据此扩展）。
- **右 pane 忙时不切**：右 pane 正在跑 `vim` / `tail -f` 之类时本轮跳过，等它回到提示符再切。这是有意为之——免得把 `cd` 打进正在编辑的文件里。
- **0.7s 轮询**：不是事件驱动。Claude Code 没有提供可订阅的 attach 事件。
- **别往运行中的 Agent View 发送文本**：`tmux send-keys` 的内容会落进它的 dispatch 输入框，真的派发一个后台 session。写自动化脚本时先确认它已退出。
- **新目录首次使用会先弹信任确认**：Claude Code 的 workspace trust 按目录记，在没打开过的目录里第一次跑会先要你确认，确认后才进得了 Agent View。

## 附：让 Claude Code 的 Bash 工具用 bash

和 cw 无关，但同属「让 Claude Code 在服务器上更好用」的配置。Bash 工具默认使用 `$SHELL`；登录 shell 是 zsh 的话，模型写出的命令会不时踩到 zsh 与 bash 的差异——最典型的是 zsh 不做单词分割，`${VAR:+-t $VAR}` 会展开成单个参数而不是两个。

在 `~/.claude/settings.json` 里固定为 bash：

```json
{
  "env": { "CLAUDE_CODE_SHELL": "/usr/bin/bash" }
}
```

实测对照（各跑一次 `claude -p '... echo "bash=[$BASH_VERSION] zsh=[$ZSH_VERSION]"'`）：

```
不设置  →  bash=[]              zsh=[5.9]
设置后  →  bash=[5.2.37(1)...]  zsh=[]
```

该变量在官方 settings 文档里没有记载，是从二进制里找到并实测确认的。**环境变量在会话启动时读取，改完需要重启 claude 会话才生效。**

切换前建议确认 bash 环境下 PATH 完整（`bash -ic 'command -v claude git gh'`）——如果你的 PATH 只在 `.zshrc` 里设置，切过去工具会找不到。另外它会改写子进程里的 `$SHELL`，脚本若要判断用户的交互 shell，应该读 passwd 而不是 `$SHELL`。

## 开发笔记

`docs/pitfalls.md` 记录了开发过程中实际踩到的坑（tmux 的 pane 定位与选项作用域、zsh 与 bash 的差异、statusline 用 NBSP 分隔导致的解析陷阱等）。改代码前值得扫一眼。

## 许可

MIT
