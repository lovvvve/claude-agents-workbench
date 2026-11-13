# cw 布局保存与按设备选择 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cw -s` 保存当前 tmux 布局；`cw` 启动时弹列表选布局，在单个会话里新建或就地重排（不杀进程），并按设备记住默认项。

**Architecture:** 纯逻辑（布局树解析/按比例渲染/校验和、布局文件、设备记录、pane 分配规则）放进无 tmux 依赖的 `cwlayout.py`，用 unittest 直接测。tmux 交互（保存时抓取、套用时挪 pane、交互式列表）放进可执行脚本 `cw-layout`。`cw`（bash）负责参数、锁、建会话、watcher，并在 attach / switch-client 时链上 `run-shell -b` 调 `cw-layout apply`，这样套用发生在目标客户端已经接入之后，能按它的真实尺寸计算比例。

**Tech Stack:** bash、tmux ≥ 3.2（实测 3.5a）、python3 标准库（3.13，**不用 pytest**，用 `unittest`）、Linux `/proc`。

**Spec:** `docs/superpowers/specs/2026-11-13-cw-layouts-design.md`

## Global Constraints

- 只用 python3 标准库；测试用 `python3 -m unittest`，不引入 pytest 等依赖。
- 附加 pane 一律在 `$HOME`（`~`）启动；只有 ④ 跟随 shell 带 `CW_DIRFILE`。
- 保存的命令**只填进命令行，不回车**：`tmux send-keys -l <文本>`，绝不追加 `Enter`/`C-m`。
- 切换布局时**不杀任何 pane**；用不上的 pane 挪进“保留”窗口（窗口名 `保留`，窗口选项 `@cw_parked=1`）。
- 绝不往 `@cw_role=left` 的 pane（Agent View）发送任何按键或文本。
- 内置布局名固定为 `默认`，结构 = `{left 60%, right 40%}`（左右切），不落盘，也不允许用户以此命名。
- 布局名规则：非空、不含 `/`、不以 `.` 开头、不等于 `默认`；允许中文和空格。
- 配置目录 `${CW_CONFIG_DIR:-~/.config/cw}`：布局文件 `layouts/<名字>.json`，设备记录 `devices`。
- 终端类型只归一成两类：`iTerm2`、`其他`。
- 运行中工作台的列表额外项文案：`保持现状，直接进入`。
- 同一布局但结构对不上时的提示文案：`当前 pane 与布局 X 不一致，未调整；如需还原请在列表中重新选择`（X 替换为布局名）。
- 不用会话级用户选项（pitfalls 第 4 条）；状态都放窗口选项：`@cw_layout`（当前布局名）、`@cw_apply`（待套用请求）、`@cw_parked`。
- 沿用 `docs/pitfalls.md`：`-t "=名字"` 精确匹配；`set-option -p/-w` 显式 `-t`；锁 fd 不能被 tmux server、watcher、exec 继承（`9>&-`）。
- 不挂 tmux resize 钩子；`cw` 在 tmux 外仍用普通 `attach`，不踢其他客户端。
- commit 信息遵循 CLAUDE.md：`<emoji> <type>(<scope>): <中文标题>`，不加 `Co-Authored-By`。

## 实测过的 tmux 事实（计划依据，2026-11-14 在 tmux 3.5a 上验证）

1. 自定义布局串套到不同尺寸的窗口时**不按比例**，而是从右下裁剪。所以按比例要自己算。
2. `select-layout <自定义串>` **忽略串里的 pane id**，按窗口内 pane 的顺序（`list-panes` 顺序）依次填进叶子。所以套用前要用 `swap-pane` 把 pane 排成叶子顺序。
3. `swap-pane` 之后 pane 选项（`@cw_role`）跟着 pane 走。
4. 校验和算法（见 Task 1）对用户布局串算出 `2fab`，与 tmux 一致。
5. `list-panes -t =会话` 不带 `-s` 时**只列当前窗口**。用户切到“保留”窗口时，现有 `cw` 和 `cw-follow` 会找不到 ①④。
6. `attach \; run-shell -b …` 里的命令开始执行时，窗口未必已经变成新客户端的尺寸，时快时慢。`switch-client \; run-shell -b` 实测执行时已是新尺寸。所以 apply 要先等窗口宽度等于客户端宽度。

## Review Focus

1. 用户正停在“保留”窗口时运行 `cw`：①④ 必须按整个会话查找（`list-panes -s`），不能误判缺失而重建。测试见 Task 7 `test_cw_finds_roles_from_parked_window`。
2. 在工作台的某个 pane 里运行 `cw` 或 `cw -s`：保存时这个 pane 记成空 shell，不能记成 `bash …/cw -s`；套用时它回到提示符后应当作空 shell 复用，不该被挪进“保留”。测试见 Task 4 `test_save_caller_pane_is_empty`、Task 5 `test_caller_pane_reused_not_parked`。
3. 命令里有引号、`$`、空格（如 `echo "a b" $HOME`），以及布局名是中文或带空格：保存后原样写入 JSON，套用时原样填进命令行。测试见 Task 2 `test_roundtrip_cmd_special_chars`、Task 5 `test_fill_special_chars_not_executed`。
4. 窗口很小（6 格布局套到 40×12）：渲染不出现 0 宽或 0 高，tmux 能接受。测试见 Task 1 `test_render_tiny_window_no_zero`、Task 5 `test_apply_small_window`。
5. 设备记录引用的布局已被删除：默认项回退到 `默认`，不报错。测试见 Task 2 `test_pick_default_deleted_layout_falls_back`。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `cwlayout.py`（新建） | 纯逻辑：`Node` 布局树、tmux 布局串解析/渲染/校验和、形状比较、内置布局；布局 JSON 读写与命名校验；设备记录；pane 分配规则 `assign_panes` |
| `cw-layout`（新建，可执行 python） | tmux 交互：`save`、`apply`、`menu` 三个子命令 |
| `cw`（修改） | `-s` 保存、`-i` 状态；先弹列表再加锁；去掉“认领第一个 pane”；`list-panes -s`；attach/switch 链上 `run-shell -b apply` |
| `cw-follow`（修改） | `pane()` 改为 `list-panes -s` |
| `tests/test_cwlayout.py`（新建） | `cwlayout.py` 单元测试 |
| `tests/tmuxenv.py`（新建） | 隔离 tmux 测试环境：内层 server（被测）+ 外层 harness server（提供真实 tty 客户端），临时 HOME / 配置目录 / 运行时目录 |
| `tests/fakebin/claude`（新建） | 假 claude：`claude agents` 时循环 sleep，进程命令行含 `claude agents` |
| `tests/test_cw_layout_cli.py`（新建） | `cw-layout save/apply/menu` 集成测试 |
| `tests/test_cw_e2e.py`（新建） | 通过 `cw` 的端到端验收测试 |
| `README.md`、`install.sh`、`docs/pitfalls.md`（修改） | 文档与提示 |

所有测试统一运行：`python3 -m unittest discover -s tests -v`（在仓库根目录）。

---

### Task 1: 布局树：解析、校验和、按比例渲染、形状比较

**Files:**
- Create: `cwlayout.py`
- Test: `tests/test_cwlayout.py`

**Interfaces:**
- Produces:
  - `@dataclass Node`：
    - `kind: str`（`'pane'` | `'lr'` 左右切 | `'tb'` 上下切）
    - `size: float = 100.0`（在父节点切分方向上的百分比）
    - `children: list[Node]`
    - `role: str = ''`（叶子：`'left'` | `'right'` | `'extra'`）
    - `cmd: str = ''`
    - `pane_id: str = ''`、`w: int = 0`、`h: int = 0`：只在 `parse_tmux_layout` 的结果里填（如 `'%5'`，以及绝对宽高），不落盘
  - `layout_checksum(body: str) -> str`：4 位小写十六进制。
  - `parse_tmux_layout(s: str) -> Node`：输入带校验和前缀的完整串；叶子带 `pane_id='%N'`，`role` 留空；`size` 按兄弟节点的绝对宽（lr）或高（tb）占比计算，保留 2 位小数（分隔线不计）。
  - `leaves(n: Node) -> list[Node]`：深度优先、从左到右或从上到下。
  - `render_tmux_layout(n: Node, width: int, height: int, ids: list[int]) -> str`：返回 `"xxxx,WxH,0,0{...}"`，叶子按 `leaves()` 顺序使用 `ids`。
  - `same_shape(a: Node, b: Node) -> bool`：`kind`、子节点数量逐层相同，且叶子 `role` 中 `left`/`right` 的位置相同（`extra` 之间不比较 `cmd`）。
  - `builtin_default() -> Node`：`lr[ pane(left, 60), pane(right, 40) ]`。

- [ ] **Step 1: 写失败测试**

```python
USER = '2fab,316x71,0,0{208x71,0,0[208x52,0,0,0,208x18,0,53{163x18,0,53,2,44x18,164,53,4}],107x71,209,0[107x52,209,0,1,107x9,209,53,3,107x8,209,63,5]}'

def test_checksum_matches_tmux(self):
    self.assertEqual(layout_checksum(USER.split(',', 1)[1]), '2fab')

def test_parse_user_layout(self):
    r = parse_tmux_layout(USER)
    self.assertEqual(r.kind, 'lr')
    self.assertEqual([l.pane_id for l in leaves(r)], ['%0', '%2', '%4', '%1', '%3', '%5'])
    self.assertAlmostEqual(r.children[0].size, 66.03, places=1)   # 208/(208+107)

def test_render_scales_proportionally(self):
    r = parse_tmux_layout(USER)
    s = render_tmux_layout(r, 200, 50, [0, 2, 4, 1, 3, 5])
    again = parse_tmux_layout(s)
    self.assertEqual(s.split(',', 1)[0], layout_checksum(s.split(',', 1)[1]))
    self.assertTrue(s.split(',', 2)[1] == '200x50')
    self.assertAlmostEqual(again.children[0].size, 66.0, delta=1.0)   # 不是被裁成 75/25
    for leaf in leaves(again):
        self.assertGreaterEqual(leaf_h(again, leaf), 3)                 # 右下两格不再只剩 1~2 行

def test_render_tiny_window_no_zero(self):
    s = render_tmux_layout(parse_tmux_layout(USER), 40, 12, [0, 1, 2, 3, 4, 5])
    self.assertNotRegex(s, r'(^|[,{\[])0x|x0,')

def test_same_shape(self):
    a = parse_tmux_layout(USER); b = parse_tmux_layout(render_tmux_layout(a, 120, 40, list(range(6))))
    self.assertTrue(same_shape(a, b))
    self.assertFalse(same_shape(a, builtin_default()))
```

`leaf_h(root, leaf)` 是测试里的小工具，直接返回 `leaf.h`（由 `parse_tmux_layout` 填入的绝对高度）。

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -m unittest tests.test_cwlayout -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'cwlayout'`）

- [ ] **Step 3: 实现 `cwlayout.py` 中上述函数**

校验和必须与 tmux 的 `layout_checksum` 一致（16 位无符号）：

```python
def layout_checksum(body: str) -> str:
    c = 0
    for ch in body:
        c = ((c >> 1) + ((c & 1) << 15)) & 0xFFFF
        c = (c + ord(ch)) & 0xFFFF
    return '%04x' % c
```

渲染规则（测试不能完全决定的部分）：
- 叶子写成 `WxH,X,Y,ID`；容器写成 `WxH,X,Y{…}`（lr）或 `WxH,X,Y[…]`（tb）。
- n 个子节点时，可分配的长度 = 父长度 − (n−1)，每条分隔线占 1。
- 按 `size` 权重分配，用最大余数法凑满总长；每个子节点至少 1。不够时从最大的那份里扣。
- 子节点的坐标依次累加“长度 + 1”。

- [ ] **Step 4: 运行，确认通过**

Run: `python3 -m unittest tests.test_cwlayout -v`
Expected: 5 tests OK

- [ ] **Step 5: Commit**

```bash
git add cwlayout.py tests/test_cwlayout.py
git commit -m "✨ feat(layout): 新增布局树解析与按比例渲染"
```

---

### Task 2: 布局文件、命名校验、设备记录

**Files:**
- Modify: `cwlayout.py`
- Test: `tests/test_cwlayout.py`

**Interfaces:**
- Consumes: Task 1 的 `Node`、`builtin_default()`
- Produces:
  - `BUILTIN_NAME = '默认'`
  - `config_dir() -> str`：`os.environ.get('CW_CONFIG_DIR')`，否则 `~/.config/cw`
  - `validate_name(name: str) -> str | None`：合法返回 `None`，否则返回中文错误信息
  - `list_layouts() -> list[str]`：`layouts/*.json` 去掉扩展名，按名字排序（不含内置）
  - `load_layout(name: str) -> Node`：`默认` 返回内置布局；其他读 JSON
  - `save_layout(name: str, root: Node) -> None`：写 JSON，`ensure_ascii=False`、`indent=2`，自动建目录
  - `@dataclass DeviceRecord`：`workbench: str`、`term: str`、`width: int`、`height: int`、`layout: str`
  - `normalize_term(raw: str) -> str`：`raw` 里含 `iterm2`（不区分大小写）时返回 `'iTerm2'`，否则返回 `'其他'`
  - `load_devices() -> list[DeviceRecord]`、`remember_device(rec: DeviceRecord) -> None`：同一 `(workbench, term, width, height)` 只保留一条，后写覆盖
  - `pick_default(records, workbench, term, width, height, available: set[str]) -> str`：只看同一工作台、同一终端类型的记录，取 `|Δw|+|Δh|` 最小的一条；它的布局不在 `available` 里时返回 `BUILTIN_NAME`；没有记录时也返回 `BUILTIN_NAME`

JSON 格式（版本 1）。叶子写 `pane`，容器写 `split`：

```json
{"version": 1, "root": {"split": "lr", "children": [
  {"size": 66.03, "split": "tb", "children": [
    {"size": 74.29, "pane": "left"},
    {"size": 25.71, "split": "lr", "children": [
      {"size": 78.74, "pane": "extra", "cmd": "top"},
      {"size": 21.26, "pane": "extra", "cmd": ""}]}]},
  {"size": 33.97, "split": "tb", "children": [
    {"size": 75.36, "pane": "right"},
    {"size": 13.04, "pane": "extra", "cmd": ""},
    {"size": 11.59, "pane": "extra", "cmd": "npm exec @deepseek-ai/dsh@next web"}]}]}}
```

设备记录文件 `devices`：每行 `workbench\tterm\twidth\theight\tlayout`。

- [ ] **Step 1: 写失败测试**（`setUp` 里把 `CW_CONFIG_DIR` 指向 `tempfile.mkdtemp()`）

```python
def test_validate_name(self):
    self.assertIsNone(validate_name('监控 布局'))
    for bad in ('', 'a/b', '.hidden', '默认'):
        self.assertIsNotNone(validate_name(bad))

def test_roundtrip_cmd_special_chars(self):
    root = builtin_default(); root.children[1:1] = [Node('pane', 10, role='extra', cmd='echo "a b" $HOME \'q\'')]
    save_layout('中文 名', root)
    self.assertIn('中文 名', list_layouts())
    got = load_layout('中文 名')
    self.assertEqual(leaves(got)[1].cmd, 'echo "a b" $HOME \'q\'')

def test_load_builtin(self):
    self.assertTrue(same_shape(load_layout('默认'), builtin_default()))

def test_pick_default_nearest_same_term_and_workbench(self):
    rs = [DeviceRecord('cw', 'iTerm2', 316, 72, 'A'), DeviceRecord('cw', 'iTerm2', 180, 50, 'B'),
          DeviceRecord('cw', '其他', 316, 72, 'C'), DeviceRecord('cw-api', 'iTerm2', 316, 72, 'D')]
    self.assertEqual(pick_default(rs, 'cw', 'iTerm2', 310, 70, {'A', 'B', 'C', 'D'}), 'A')
    self.assertEqual(pick_default(rs, 'cw', 'iTerm2', 190, 52, {'A', 'B', 'C', 'D'}), 'B')
    self.assertEqual(pick_default(rs, 'cw', '其他', 100, 30, {'A', 'B', 'C', 'D'}), 'C')

def test_pick_default_deleted_layout_falls_back(self):
    self.assertEqual(pick_default([DeviceRecord('cw', 'iTerm2', 316, 72, 'gone')], 'cw', 'iTerm2', 316, 72, set()), '默认')

def test_remember_device_overwrites_same_key(self):
    remember_device(DeviceRecord('cw', 'iTerm2', 316, 72, 'A')); remember_device(DeviceRecord('cw', 'iTerm2', 316, 72, 'B'))
    self.assertEqual([r.layout for r in load_devices()], ['B'])

def test_normalize_term(self):
    self.assertEqual(normalize_term('iTerm2 3.7.7'), 'iTerm2'); self.assertEqual(normalize_term(''), '其他')
```

- [ ] **Step 2: 运行，确认失败**

Run: `python3 -m unittest tests.test_cwlayout -v` → 新增用例 FAIL（`ImportError`）

- [ ] **Step 3: 在 `cwlayout.py` 实现上述接口**
- [ ] **Step 4: 运行，确认通过**

Run: `python3 -m unittest tests.test_cwlayout -v` → 全部 OK

- [ ] **Step 5: Commit**

```bash
git add cwlayout.py tests/test_cwlayout.py
git commit -m "✨ feat(layout): 新增布局文件读写与设备记录"
```

---

### Task 3: pane 分配规则 `assign_panes`

**Files:**
- Modify: `cwlayout.py`
- Test: `tests/test_cwlayout.py`

**Interfaces:**
- Consumes: Task 1 的 `Node`、`leaves()`
- Produces:
  - `@dataclass Candidate`：`pane_id: str`、`fg_cmd: str`（前台命令，停在提示符时为 `''`）、`idle: bool`（停在 shell 提示符）
  - `@dataclass Assignment`：`slots: list[tuple[str | None, bool]]`，与 `leaves(target)` 一一对应，元素是 `(pane_id，或 None 表示新开, 是否需要填命令)`；`park: list[str]`，表示用不上、要挪进“保留”的候选
  - `assign_panes(target: Node, left_id: str, right_id: str, candidates: list[Candidate]) -> Assignment`

规则（按设计 §7.4）。候选的优先次序就是 `candidates` 列表里的顺序，调用方保证“主窗口在前、保留窗口在后”：
1. `left` 叶子分配 `left_id`，`right` 叶子分配 `right_id`，都不填命令。
2. 有命令的 `extra` 叶子：按叶子顺序，取第一个 `fg_cmd == cmd`（完全相等）的候选，不填命令。
3. 其余 `extra` 叶子（空命令，或第 2 步没匹配上）：按叶子顺序，取第一个 `idle` 的候选；`cmd` 非空时需要填命令。
4. 还没分到的叶子用 `None`（新开）；`cmd` 非空时需要填命令。
5. 剩下的候选全部进 `park`，保持原顺序。

- [ ] **Step 1: 写失败测试**

```python
def T(*cmds):   # 目标：left、right 加若干 extra
    return Node('lr', children=[Node('pane', 30, role='left'), Node('pane', 30, role='right')] +
                [Node('pane', 10, role='extra', cmd=c) for c in cmds])

def test_reuse_running_same_cmd(self):
    a = assign_panes(T('dsh web'), '%0', '%1', [Candidate('%7', 'vim x', False), Candidate('%8', 'dsh web', False)])
    self.assertEqual(a.slots, [('%0', False), ('%1', False), ('%8', False)])
    self.assertEqual(a.park, ['%7'])

def test_idle_shell_gets_fill(self):
    a = assign_panes(T('top', ''), '%0', '%1', [Candidate('%5', '', True)])
    self.assertEqual(a.slots[2:], [('%5', True), (None, False)])

def test_new_pane_needs_fill(self):
    self.assertEqual(assign_panes(T('top'), '%0', '%1', []).slots[2], (None, True))

def test_busy_never_reused_for_other_cmd(self):
    a = assign_panes(T(''), '%0', '%1', [Candidate('%9', 'vim x', False)])
    self.assertEqual(a.slots[2], (None, False)); self.assertEqual(a.park, ['%9'])
```

- [ ] **Step 2: 运行，确认失败**：`python3 -m unittest tests.test_cwlayout -v`
- [ ] **Step 3: 实现 `assign_panes`**
- [ ] **Step 4: 运行，确认通过**：同上，全部 OK
- [ ] **Step 5: Commit**

```bash
git add cwlayout.py tests/test_cwlayout.py
git commit -m "✨ feat(layout): 新增切换布局时的 pane 分配规则"
```

---

### Task 4: `cw-layout save`（抓取当前布局）与隔离 tmux 测试环境

**Files:**
- Create: `cw-layout`（`#!/usr/bin/env python3`，可执行；`sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))` 后 `import cwlayout`）
- Create: `tests/tmuxenv.py`、`tests/fakebin/claude`（可执行）
- Test: `tests/test_cw_layout_cli.py`

**Interfaces:**
- Consumes: Task 1、2 的 `parse_tmux_layout`、`leaves`、`save_layout`、`validate_name`、`list_layouts`
- Produces（`cw-layout` 内部，Task 5/6 复用）：
  - `tmux(*args: str) -> str`：`subprocess.run(['tmux', *args], capture_output=True, text=True, check=True).stdout.rstrip('\n')`
  - `main_window(sess: str) -> str`：`list-panes -s -t =sess` 中 `@cw_role=left` 所在的 `window_id`（如 `@3`）
  - `pane_fg_cmd(pane_pid: int) -> str`：读 `/proc/<pane_pid>/stat` 第 8 个字段 tpgid（从最后一个 `)` 之后切分）。tpgid 等于 shell 自己的 pgid 时返回 `''`；否则读 `/proc/<tpgid>/cmdline`，按 `\0` 切分后 `shlex.join`
  - `capture(sess: str, caller_pane: str) -> Node`：解析主窗口的 `#{window_layout}`；叶子 `role` 取自 `@cw_role`，没有值时为 `extra`；`extra` 的 `cmd` 取 `pane_fg_cmd`；`caller_pane` 那个 pane 强制 `cmd=''`
  - CLI：`cw-layout save --session SESS [--name NAME] [--yes] [--caller-pane %N]`。成功后把主窗口的 `@cw_layout` 设为 NAME，退出码 0；工作台未运行或名字非法时退出码 1，并向 stderr 输出中文原因
- `tests/tmuxenv.py` 提供 `class TmuxEnv`（`setUp` 里创建，`tearDown` 里 `close()`）：
  - 属性 `env: dict`（`TMUX_TMPDIR`、`HOME`、`CW_CONFIG_DIR`、`XDG_RUNTIME_DIR` 都指向临时目录；`PATH` 前置 `tests/fakebin`；`SHELL=/bin/bash`；删除 `TMUX`、`TMUX_PANE`、`LC_TERMINAL`），以及 `home`、`cfg`、`run`
  - `tmux(*args) -> str`：对内层被测 server 执行
  - `client(cmd: str, width: int, height: int, name='c') -> None`：在外层 harness server（`tmux -L cwt-<pid>-harness`）上开一个尺寸为 width×height 的会话，在里面执行 `env -u TMUX … cmd`，为内层提供真实 tty 客户端
  - `resize_client(width, height, name='c')`、`close_client(name='c')`、`wait(pred, timeout=10.0)`（每 0.1s 轮询，超时 `AssertionError`）、`close()`
- 交互流程（不带 `--yes` 时）：
  1. 没给 `--name` 时提示输入名字（`布局名：`），非法时提示原因并重新输入；
  2. 逐个列出附加 pane：`[序号] 宽x高  命令：<cmd 或 （空 shell）>`，提示 `回车保留 / 输入新命令 / 输入 - 清空：`；
  3. 同名已存在时问 `布局 X 已存在，覆盖？[y/N]`。

`--yes` 表示：接受识别结果，同名直接覆盖。

- [ ] **Step 1: 写失败测试**（`tests/test_cw_layout_cli.py`，用 `TmuxEnv` 在内层手工建一个 3 格会话：`new-session -d -s cw -x 120 -y 40`，pane0 设 `@cw_role left`，横切出 pane1 设 `@cw_role right`，再从 pane1 竖切出 pane2，在 pane2 里 `send-keys 'sleep 4242' Enter`）

```python
def test_save_captures_roles_cmds_proportions(self):
    run(['cw-layout', 'save', '--session', 'cw', '--name', '监控', '--yes'])
    self.assertTrue(os.path.exists(os.path.join(e.cfg, 'layouts', '监控.json')))
    ls = leaves(load_layout('监控'))
    self.assertEqual([l.role for l in ls], ['left', 'right', 'extra'])
    self.assertEqual(ls[2].cmd, 'sleep 4242')
    self.assertEqual(e.tmux('show-options', '-wqv', '-t', '=cw:', '@cw_layout'), '监控')

def test_save_caller_pane_is_empty(self):
    run(['cw-layout', 'save', '--session', 'cw', '--name', 'x', '--yes', '--caller-pane', pane2])
    self.assertEqual(leaves(load_layout('x'))[2].cmd, '')

def test_save_invalid_name_and_not_running(self):
    self.assertEqual(run(['cw-layout', 'save', '--session', 'cw', '--name', 'a/b', '--yes']).returncode, 1)
    self.assertEqual(run(['cw-layout', 'save', '--session', 'nope', '--name', 'ok', '--yes']).returncode, 1)
```

`run()` 在 `e.env` 下执行仓库内的 `cw-layout`，返回 `CompletedProcess`；测试进程里读写 `cwlayout` 前也要把 `CW_CONFIG_DIR` 设成 `e.cfg`。

- [ ] **Step 2: 运行，确认失败**：`python3 -m unittest tests.test_cw_layout_cli -v` → FAIL（`cw-layout` 不存在）
- [ ] **Step 3: 实现 `tests/tmuxenv.py`、`tests/fakebin/claude`（参数为 `agents` 时 `while :; do sleep 1; done`）、`cw-layout` 的公共函数和 `save`**
- [ ] **Step 4: 运行，确认通过**：同上 3 tests OK；再跑 `python3 -m unittest discover -s tests -v`，全部 OK
- [ ] **Step 5: Commit**

```bash
git add cw-layout tests/tmuxenv.py tests/fakebin/claude tests/test_cw_layout_cli.py
git commit -m "✨ feat(layout): 新增 cw-layout save 抓取并保存当前布局"
```

---

### Task 5: `cw-layout apply`（新建、就地重排、只重算比例）

**Files:**
- Modify: `cw-layout`
- Test: `tests/test_cw_layout_cli.py`

**Interfaces:**
- Consumes: Task 1–4 的 `load_layout`、`assign_panes`、`Candidate`、`render_tmux_layout`、`same_shape`、`parse_tmux_layout`、`main_window`、`pane_fg_cmd`、`tmux`
- Produces：CLI `cw-layout apply --session SESS --client TTY`。它从主窗口的窗口选项 `@cw_apply` 读取请求（格式 `mode\tname\tcaller_pane`，mode 为 `reshape` 或 `rescale`），读完立刻 `set-option -wu` 清掉。失败时通过 `tmux display-message -c TTY` 给出中文提示，退出码非 0。Task 6 的 `cw` 会设置 `@cw_apply`，然后用 `run-shell -b` 调用它。

执行顺序：
1. 用 `fcntl.flock` 锁 `${XDG_RUNTIME_DIR:-/tmp}/cw-${SESS}.lock`，最多等 5s，与 `cw` 用同一把锁。
2. 最多等 3s，直到主窗口的 `#{window_width}` 等于 `display -p -c TTY '#{client_width}'`（依据实测事实 6）；超时就按当前尺寸继续。如果 `caller_pane` 非空，再最多等 1.5s，直到它回到 shell 提示符。
3. `rescale`：用 `parse_tmux_layout(#{window_layout})` 加上 `@cw_role` 得到当前树，与目标布局做 `same_shape` 比较。
   - 一致：按当前 pane 顺序 `render_tmux_layout` 后 `select-layout`，不挪 pane、不填命令。
   - 不一致：`display-message` 给出 Global Constraints 里那条提示，什么都不改。
4. `reshape`：
   1. 候选 = 主窗口里除 ①④ 以外的 pane（按 pane 序号），再接上 `@cw_parked=1` 窗口里的 pane；`idle` 表示 `#{pane_current_command}` 属于 `sh|bash|zsh|fish|ksh|dash`。
   2. 调 `assign_panes` 得到分配结果。
   3. `park` 里的 pane 挪进“保留”窗口：还没有这个窗口时，用 `break-pane -d -P -F '#{window_id}' -n 保留` 新建，再设 `@cw_parked 1` 和 `automatic-rename off`；已有时用 `join-pane -d`。
   4. 被选中的、原本在“保留”窗口里的 pane，用 `join-pane -d` 拉回主窗口。
   5. 先 `select-layout tiled`，再为每个 `None` 槽位找面积最大的 pane 做 `split-window -d -c $HOME -P -F '#{pane_id}'`；split 失败就提示并停止，不杀任何 pane。
   6. 用 `swap-pane -d` 把窗口里的 pane 排成叶子顺序（依据实测事实 2）。
   7. `render_tmux_layout(目标, 窗口宽, 窗口高, ids)` 后 `select-layout`。
   8. 对需要填命令的槽位：等这个 pane 是 shell，并且 `capture-pane -p` 里已经有非空白内容（提示符出现了），最多等 5s，然后 `send-keys -t <pane> -l <cmd>`。**不发 Enter**；需要填命令的槽位永远不会是 ①。
5. 两种模式都要做的收尾：设置 `@cw_layout`（只有 reshape 成功，或 rescale 结构一致时才设），`select-window` 主窗口，`select-pane` ①。

- [ ] **Step 1: 写失败测试**（用 `TmuxEnv.client` 让内层会话有一个真实客户端，例如 `client('tmux attach -t =cw', 200, 50)`；用 `save_layout` 预先写好目标布局；每个用例先 `set-option -w @cw_apply …`，再执行 `cw-layout apply --session cw --client <tty>`，tty 用 `list-clients -F '#{client_tty}'` 获取）

```python
def test_reshape_builds_and_fills_without_executing(self):
    # 目标：用户的六格布局，其中 top 换成 'sleep 4242'，dsh 换成 'echo "a b" $HOME'（同时覆盖 Review Focus 第 3 条）
    apply('reshape', '六格')
    panes = e.tmux('list-panes', '-t', '=cw:', '-F', '#{pane_id} #{@cw_role} #{pane_current_command} #{pane_current_path}').splitlines()
    self.assertEqual(len(panes), 6)
    extras = [p for p in panes if p.split()[1] not in ('left', 'right')]
    self.assertTrue(all(p.split()[2] == 'bash' and p.split()[3] == e.home for p in extras))   # 在 ~，命令没跑
    self.assertIn('echo "a b" $HOME', capture(pane_of_slot(5)))                               # 文本原样在命令行
    self.assertEqual(e.tmux('show-options', '-wqv', '-t', '=cw:', '@cw_layout'), '六格')

def test_fill_special_chars_not_executed(self):   # 在上一个用例的基础上，检查没有出现 "a b /tmp…" 这样的执行结果
    self.assertNotIn('a b ' + e.home, capture(pane_of_slot(5)))

def test_reshape_keeps_processes_and_left_pane(self):
    left_pid = pid_of_role('left'); vim_like = start_in_new_pane('sleep 777')        # 一个不在目标布局里的忙碌 pane
    apply('reshape', '默认')
    self.assertEqual(pid_of_role('left'), left_pid)                                    # ① 没有重启
    parked = e.tmux('list-windows', '-t', '=cw', '-F', '#{window_name} #{@cw_parked}')
    self.assertIn('保留 1', parked)
    self.assertTrue(process_alive('sleep 777'))

def test_reshape_reuses_parked_matching_cmd(self):   # 切走再切回：sleep 4242 从“保留”回到原槽位，进程不变
    pid = pid_of_cmd('sleep 4242'); apply('reshape', '默认'); apply('reshape', '六格')
    self.assertEqual(pid_of_cmd('sleep 4242'), pid)
    self.assertEqual(slot_pane_cmd(1), 'sleep')

def test_caller_pane_reused_not_parked(self):
    apply('reshape', '六格', caller=extra_idle_pane)
    self.assertIn(extra_idle_pane, main_window_panes())

def test_rescale_same_layout_after_resize(self):
    apply('reshape', '六格'); before = pane_ids(); e.resize_client(140, 40); e.wait(window_width_is(140))
    apply('rescale', '六格')
    self.assertEqual(pane_ids(), before)
    self.assertAlmostEqual(left_column_ratio(), 0.66, delta=0.02)

def test_rescale_mismatch_does_nothing(self):
    apply('reshape', '六格'); e.tmux('split-window', '-d', '-t', some_extra); layout = window_layout()
    apply('rescale', '六格')
    self.assertEqual(window_layout(), layout)

def test_apply_small_window(self):
    e.resize_client(40, 12); apply('reshape', '六格')
    self.assertEqual(len(main_window_panes()), 6)
```

上面的 `apply`、`pid_of_role`、`capture`、`pane_of_slot` 等辅助函数写在本测试文件里，都是对 `e.tmux(...)` 和 `/proc` 的简单封装。

- [ ] **Step 2: 运行，确认失败**：`python3 -m unittest tests.test_cw_layout_cli -v` → 新增用例 FAIL
- [ ] **Step 3: 实现 `apply` 子命令**
- [ ] **Step 4: 运行，确认通过**：`python3 -m unittest discover -s tests -v` → 全部 OK
- [ ] **Step 5: Commit**

```bash
git add cw-layout tests/test_cw_layout_cli.py
git commit -m "✨ feat(layout): 新增 cw-layout apply 就地重排与按比例套用"
```

---

### Task 6: `cw-layout menu`（列表、设备识别、记住选择）

**Files:**
- Modify: `cw-layout`、`cwlayout.py`
- Test: `tests/test_cwlayout.py`、`tests/test_cw_layout_cli.py`

**Interfaces:**
- Consumes: Task 2 的 `list_layouts`、`load_devices`、`pick_default`、`remember_device`、`normalize_term`、`BUILTIN_NAME`
- Produces：
  - `cwlayout.menu_items(saved: list[str], running: bool, current: str) -> list[tuple[str, str]]`：返回 `(值, 显示文本)` 列表。运行中时第一项是 `('__keep__', '保持现状，直接进入')`；接着是 `('默认', '默认')` 和已保存的各项；`current` 那一项的显示文本后追加 `  ← 当前`。
  - `cwlayout.resolve_choice(text: str, items, default_value: str) -> str | None`：空串返回 `default_value`；合法序号返回对应的值；其他返回 `None`，由调用方重新提问。
  - `detect_device() -> tuple[str, int, int]`（在 `cw-layout` 里）：
    - `TMUX` 非空时：类型用 `display -p '#{client_termtype}'`，尺寸用 `'#{client_width} #{client_height}'`；
    - 否则：类型用 `LC_TERMINAL`，尺寸依次尝试 `os.get_terminal_size(sys.stderr.fileno())` → 环境变量 `COLUMNS`/`LINES` → `80, 24`；
    - 类型都经过 `normalize_term`。
    - **不能用 `shutil.get_terminal_size()`**：`cw` 用 `$(…)` 捕获 menu 的 stdout，stdout 是管道，会错拿到默认值。
  - CLI：`cw-layout menu --session SESS [--running] [--current NAME]`。stdout 只输出选中的值（`__keep__` 或布局名）；提示和列表写到 stderr。用户 EOF 或 Ctrl-C 时退出码 130。
- 行为：
  - `list_layouts()` 为空时，直接输出 `__keep__`，不弹列表。`cw` 收到它就按现在的行为处理。
  - stdin 不是 tty 时，不提问，直接输出默认项。
  - 默认项 = `pick_default(...)`，并在列表里标 `*`；`cwlayout.py` 用 `workbench=SESS`。
  - 选中的不是 `__keep__` 时，调用 `remember_device(DeviceRecord(SESS, term, w, h, 选中值))`。
  - 提示文案用 `选择 [默认项序号]：`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cwlayout.py
def test_menu_items_running_marks_current(self):
    items = menu_items(['监控', '开发'], True, '监控')
    self.assertEqual(items[0], ('__keep__', '保持现状，直接进入'))
    self.assertEqual([v for v, _ in items], ['__keep__', '默认', '监控', '开发'])
    self.assertTrue(items[2][1].endswith('← 当前'))

def test_resolve_choice(self):
    items = menu_items(['监控'], False, '')
    self.assertEqual(resolve_choice('', items, '监控'), '监控')
    self.assertEqual(resolve_choice('1', items, '监控'), '默认')       # 序号从 1 开始（不运行时没有“保持现状”）
    self.assertIsNone(resolve_choice('9', items, '监控'))

# tests/test_cw_layout_cli.py（非 tty：stdin 用 DEVNULL）
def test_menu_no_saved_layouts_keeps(self):
    self.assertEqual(menu_out(), '__keep__')

def test_menu_non_tty_returns_device_default(self):
    save_layout('监控', builtin_default()); remember_device(DeviceRecord('cw', '其他', 80, 24, '监控'))
    self.assertEqual(menu_out(env_extra={'COLUMNS': '80', 'LINES': '24'}), '监控')
```

运行中工作台列表的编号规则：序号就是 `menu_items` 的下标加 1，“保持现状”是 1。`test_resolve_choice` 测的是不运行时的列表。

测试里 stderr 是管道，`detect_device` 会走 `COLUMNS`、`LINES` 这条回退，测试据此固定尺寸。

- [ ] **Step 2: 运行，确认失败**：`python3 -m unittest discover -s tests -v` → 新增用例 FAIL
- [ ] **Step 3: 实现 `menu_items`、`resolve_choice`、`detect_device`、`menu` 子命令**
- [ ] **Step 4: 运行，确认通过**：`python3 -m unittest discover -s tests -v` → 全部 OK
- [ ] **Step 5: Commit**

```bash
git add cw-layout cwlayout.py tests/test_cwlayout.py tests/test_cw_layout_cli.py
git commit -m "✨ feat(layout): 新增启动时的布局选择列表与设备记忆"
```

---

### Task 7: 接入 `cw`，同步更新 `cw-follow` 与文档

**Files:**
- Modify: `cw`（参数解析、`pane()`、状态、自愈、up 流程、`usage()`）
- Modify: `cw-follow`（`pane()`）
- Modify: `README.md`、`install.sh`（结尾的用法提示）、`docs/pitfalls.md`（新增 tmux 实测事实 1、2、5、6）
- Test: `tests/test_cw_e2e.py`

**Interfaces:**
- Consumes: Task 4–6 的 `cw-layout save/apply/menu` CLI 和窗口选项 `@cw_apply`、`@cw_layout`
- Produces（用户可见）：
  - `cw [-n 名字] [-s] [-i] [-r] [-k] [-h]`：`-s` 保存（调用 `cw-layout save --session "$SESS"`，在 tmux 内追加 `--caller-pane "$TMUX_PANE"`）；`-i` 状态（原 `-s` 的输出，外加一行 `  布局=<@cw_layout 或 无>`）。

`cw` 的 up 流程按这个顺序：
1. 查询 `has` 和 `@cw_layout`，然后 `choice=$(python3 "$BIN/cw-layout" menu --session "$SESS" [--running] --current "$cur")`；退出码非 0 就直接退出，不做任何改动。**此时不持锁。**
2. 加锁，然后建会话和自愈。和现在的区别：
   - `pane()` 改用 `list-panes -s`；
   - 左 pane 缺失时**不再认领第一个 pane**：右 pane 在就从它左边 `split-window -h -b -l 60%` 新建，否则走现有的新建会话分支；
   - 右 pane 和 watcher 的逻辑不变。
3. `choice != __keep__` 时：`mode=rescale`（工作台原本在运行且 `choice == cur`），否则 `mode=reshape`。在 ① 所在窗口执行 `set-option -w -t "$L" @cw_apply "$mode<TAB>$choice<TAB>${TMUX_PANE:-}"`，并追加 attach 后要执行的命令：

```bash
APPLY=(\; run-shell -b "python3 '$BIN/cw-layout' apply --session '$SESS' --client '#{client_tty}' >/dev/null 2>&1")
```

4. `exec 9>&-` 之后，`tmux attach -t "=$SESS" "${APPLY[@]}"`；在 tmux 内则用 `switch-client`。`choice == __keep__` 时 `APPLY` 为空，行为和现在一致。

- [ ] **Step 1: 写失败的端到端测试**（`TmuxEnv.client('cw', 200, 50)` 在 harness 里真正执行 `cw`；stdin 是 tty，所以用 `send-keys` 向 harness 输入编号和回车。harness 那个 pane 跑的是 `cw` 的列表，不是 Agent View）

```python
def test_no_saved_layouts_behaves_as_before(self):
    e.client('cw', 200, 50); e.wait(session_exists('cw'))
    self.assertEqual(len(inner_panes()), 2)
    self.assertEqual(roles(), ['left', 'right'])

def test_save_then_pick_restores_layout(self):     # 验收第 1 条
    build_six_pane_layout_in_cw(); run_cw('-s') ; answer('六格'); confirm_all()
    run_cw('-k'); e.client('cw', 200, 50); answer('')       # 回车，选设备默认项
    e.wait(lambda: len(inner_panes()) == 6)

def test_reenter_same_layout_only_rescales(self):  # 验收：换尺寸后回车选当前布局
    start_with_layout('六格'); ids = pane_ids()
    e.close_client(); e.client('cw', 150, 45, name='c2'); answer('')
    e.wait(lambda: window_width() == 150); self.assertEqual(pane_ids(), ids)

def test_keep_changes_nothing(self):
    start_with_layout('六格'); layout = window_layout()
    e.client('cw', 200, 50, name='c2'); answer('1')     # 1 = 保持现状
    self.assertEqual(window_layout(), layout)

def test_cw_finds_roles_from_parked_window(self):  # Review Focus 第 1 条
    start_with_layout('六格'); switch_to('默认')       # 产生“保留”窗口
    e.tmux('select-window', '-t', parked_window()); e.client('cw', 200, 50, name='c2'); answer('1')
    self.assertEqual(roles_count('left'), 1); self.assertEqual(roles_count('right'), 1)

def test_status_flag_i(self):
    out = run_cw('-i'); self.assertIn('布局=', out)
```

测试文件里的辅助函数：
- `start_with_layout(name)`：用 `save_layout` 写入六格布局（命令用 `sleep 4242`），写一条使它成为默认项的设备记录（`COLUMNS=200 LINES=50`、类型 `其他`），`e.client('cw', 200, 50)`，`answer('')`，然后等 pane 数达到 6；
- `switch_to(name)`：开一个新客户端，执行 `cw`，输入该布局的序号；
- `answer(text)`：等 harness 里出现 `选择 [`，再 `send-keys -l text` 和 `Enter`。

- [ ] **Step 2: 运行，确认失败**：`python3 -m unittest tests.test_cw_e2e -v` → FAIL（`-s` 还是状态、没有列表等）
- [ ] **Step 3: 按上述流程修改 `cw` 和 `cw-follow`**
- [ ] **Step 4: 运行，确认通过**：`python3 -m unittest discover -s tests -v` → 全部 OK
- [ ] **Step 5: 更新文档**
  - README 的用法表和新的“布局”一节：`-s`、`-i`、列表、“保留”窗口、只填不执行、按设备默认；
  - `usage()` 和 `install.sh` 结尾的提示改成 `-i`；
  - `docs/pitfalls.md` 的 tmux 一节新增：自定义布局串不按比例、`select-layout` 忽略 pane id、`list-panes -t 会话` 只列当前窗口、`attach \; run-shell` 执行时窗口未必已 resize。
- [ ] **Step 6: 手工验收**（在用户真实环境里，由用户执行）
  - 在当前六格布局上 `cw -s` 保存，`cw -k` 后再 `cw` 选它：结构和比例还原，`top`、`dsh web` 在命令行里未执行；
  - 在 Windows Terminal 里于 tmux 内执行 `tmux display -p '#{client_termtype}'`，记录输出。不含 `iTerm2` 就归为“其他”，与 Mac 区分开。
- [ ] **Step 7: Commit**

```bash
git add cw cw-follow README.md install.sh docs/pitfalls.md tests/test_cw_e2e.py
git commit -m "✨ feat(cw): 启动时选择布局、cw -s 保存布局、cw -i 查看状态"
```
