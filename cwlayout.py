"""cwlayout —— cw 布局功能的纯逻辑部分（不依赖 tmux，可直接单元测试）。

布局树 Node 与 tmux 的 #{window_layout} 结构一致：
  lr = 左右切（tmux 的 {...}），tb = 上下切（tmux 的 [...]），pane = 叶子。
尺寸一律按「占父节点切分方向的百分比」保存，套用时再按窗口尺寸换算成绝对值：
tmux 自己套用尺寸不同的布局串时不按比例，而是从右下裁剪（见 docs/pitfalls.md）。
"""
import json
import os
import re
from dataclasses import dataclass, field


@dataclass
class Node:
    kind: str                      # 'pane' | 'lr' | 'tb'
    size: float = 100.0            # 在父节点切分方向上的百分比
    children: list = field(default_factory=list)
    role: str = ''                 # 叶子：'left' | 'right' | 'extra'
    cmd: str = ''                  # extra 叶子的命令文本（'' = 空 shell）
    pane_id: str = ''              # 以下三项只在 parse_tmux_layout 结果里有值，不落盘
    w: int = 0
    h: int = 0


# ---------------------------------------------------------------- tmux 布局串

def layout_checksum(body: str) -> str:
    """与 tmux layout_checksum() 一致的 16 位校验和，4 位小写十六进制。"""
    c = 0
    for ch in body:
        c = ((c >> 1) + ((c & 1) << 15)) & 0xFFFF
        c = (c + ord(ch)) & 0xFFFF
    return '%04x' % c


_CELL = re.compile(r'(\d+)x(\d+),(\d+),(\d+)')
_LEAF_ID = re.compile(r',(\d+)')


def _parse_cell(s: str, i: int):
    m = _CELL.match(s, i)
    if not m:
        raise ValueError('布局串格式错误（位置 %d）：%r' % (i, s[i:i + 20]))
    w, h = int(m.group(1)), int(m.group(2))
    i = m.end()
    if i < len(s) and s[i] in '{[':
        kind, close = ('lr', '}') if s[i] == '{' else ('tb', ']')
        i += 1
        children = []
        while True:
            child, i = _parse_cell(s, i)
            children.append(child)
            if i < len(s) and s[i] == ',':
                i += 1
                continue
            if i < len(s) and s[i] == close:
                i += 1
                break
            raise ValueError('布局串格式错误：缺少 %r（位置 %d）' % (close, i))
        return Node(kind, children=children, w=w, h=h), i
    m = _LEAF_ID.match(s, i)              # tmux 的叶子总是带 ,pane编号
    if not m:
        raise ValueError('布局串格式错误：叶子缺少 pane 编号（位置 %d）' % i)
    return Node('pane', pane_id='%' + m.group(1), w=w, h=h), m.end()


def _fill_sizes(n: Node) -> None:
    if n.kind == 'pane':
        return
    total = sum(c.w if n.kind == 'lr' else c.h for c in n.children) or 1
    for c in n.children:
        c.size = round((c.w if n.kind == 'lr' else c.h) * 100.0 / total, 2)
        _fill_sizes(c)


def parse_tmux_layout(s: str) -> Node:
    """解析 tmux #{window_layout}（可带或不带 4 位校验和前缀）。"""
    body = s.split(',', 1)[1] if re.match(r'^[0-9a-f]{4},', s) else s
    root, end = _parse_cell(body, 0)
    if end != len(body):
        raise ValueError('布局串末尾有多余内容：%r' % body[end:])
    _fill_sizes(root)
    return root


def leaves(n: Node) -> list:
    """深度优先列出叶子：左到右 / 上到下，与 tmux 填格子的顺序一致。"""
    if n.kind == 'pane':
        return [n]
    out = []
    for c in n.children:
        out.extend(leaves(c))
    return out


def _alloc(total: int, weights: list) -> list:
    """按权重把 total 分给各份（最大余数法），每份至少 1。"""
    n = len(weights)
    if total < n:                          # 实在放不下：每份 1，交给 tmux 判断
        return [1] * n
    ws = weights if sum(weights) > 0 else [1] * n   # 权重全 0 时平分
    raw = [total * w / sum(ws) for w in ws]
    out = [max(1, int(r)) for r in raw]
    diff = total - sum(out)
    order = sorted(range(n), key=lambda k: raw[k] - int(raw[k]), reverse=True)
    k = 0
    while diff > 0:                        # 余数补给小数部分最大的
        out[order[k % n]] += 1
        diff -= 1
        k += 1
    while diff < 0:                        # 因「至少 1」超出时从最大的扣
        big = max(range(n), key=lambda j: out[j])
        out[big] -= 1
        diff += 1
    return out


def _render(n: Node, x: int, y: int, w: int, h: int, ids) -> str:
    head = '%dx%d,%d,%d' % (w, h, x, y)
    if n.kind == 'pane':
        return '%s,%d' % (head, next(ids))
    seps = len(n.children) - 1
    parts = []
    if n.kind == 'lr':
        cx = x
        for c, cw in zip(n.children, _alloc(w - seps, [c.size for c in n.children])):
            parts.append(_render(c, cx, y, cw, h, ids))
            cx += cw + 1
        return head + '{' + ','.join(parts) + '}'
    cy = y
    for c, ch in zip(n.children, _alloc(h - seps, [c.size for c in n.children])):
        parts.append(_render(c, x, cy, w, ch, ids))
        cy += ch + 1
    return head + '[' + ','.join(parts) + ']'


def render_tmux_layout(n: Node, width: int, height: int, ids: list) -> str:
    """按比例把布局树渲染成 width×height 的 tmux 布局串（含校验和）。

    ids 依 leaves() 顺序给出叶子的 pane 编号；tmux 实际按窗口内 pane 顺序填格子，
    编号只是让串合法，调用方要先把 pane 排成叶子顺序。
    """
    body = _render(n, 0, 0, width, height, iter(ids))
    return '%s,%s' % (layout_checksum(body), body)


def _role_key(role: str) -> str:
    return role if role in ('left', 'right') else 'extra'


def same_shape(a: Node, b: Node) -> bool:
    """切分结构相同，且 left/right 叶子位置相同（extra 之间不比较命令）。"""
    if a.kind != b.kind:
        return False
    if a.kind == 'pane':
        return _role_key(a.role) == _role_key(b.role)
    return len(a.children) == len(b.children) and all(
        same_shape(x, y) for x, y in zip(a.children, b.children))


def builtin_default() -> Node:
    """内置「默认」布局：左 Agent View 60%，右跟随 shell 40%。"""
    return Node('lr', children=[Node('pane', 60.0, role='left'),
                                Node('pane', 40.0, role='right')])


# ---------------------------------------------------------------- 布局文件

BUILTIN_NAME = '默认'


def config_dir() -> str:
    return os.environ.get('CW_CONFIG_DIR') or os.path.expanduser('~/.config/cw')


def _layouts_dir() -> str:
    return os.path.join(config_dir(), 'layouts')


def validate_name(name: str):
    """布局名合法返回 None，否则返回中文原因。允许中文和空格。"""
    if not name or not name.strip():
        return '名字不能为空'
    if '/' in name:
        return '名字不能包含 /'
    if name.startswith('.'):
        return '名字不能以 . 开头'
    if any(ord(c) < 32 for c in name):
        return '名字不能包含换行、制表符等控制字符'
    if name == BUILTIN_NAME:
        return '「%s」是内置布局，请换个名字' % BUILTIN_NAME
    return None


def list_layouts() -> list:
    """已保存的布局名（不含内置），按名字排序。"""
    try:
        files = os.listdir(_layouts_dir())
    except FileNotFoundError:
        return []
    return sorted(f[:-5] for f in files if f.endswith('.json') and not f.startswith('.'))


def _to_json(n: Node, root: bool = False) -> dict:
    if n.kind == 'pane':
        role = n.role or 'extra'
        d = {'size': n.size, 'pane': role}
        if role == 'extra':
            d['cmd'] = n.cmd
        return d
    d = {} if root else {'size': n.size}
    d.update({'split': n.kind, 'children': [_to_json(c) for c in n.children]})
    return d


def _from_json(d: dict) -> Node:
    size = float(d.get('size', 100.0))
    if 'pane' in d:
        return Node('pane', size, role=d['pane'], cmd=d.get('cmd', '') if d['pane'] == 'extra' else '')
    if d.get('split') not in ('lr', 'tb') or not d.get('children'):
        raise ValueError('split 必须是 lr / tb 且有 children')
    return Node(d['split'], size, children=[_from_json(c) for c in d['children']])


def _check_roles(root: Node) -> None:
    roles = [l.role for l in leaves(root)]
    if roles.count('left') != 1 or roles.count('right') != 1:
        raise ValueError('布局必须恰好包含一个 left（Agent View）和一个 right（跟随 shell）')


def load_layout(name: str) -> Node:
    if name == BUILTIN_NAME:
        return builtin_default()
    with open(os.path.join(_layouts_dir(), name + '.json'), encoding='utf-8') as f:
        data = json.load(f)
    root = _from_json(data['root'])
    _check_roles(root)
    return root


def _write_atomic(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def save_layout(name: str, root: Node) -> None:
    err = validate_name(name)
    if err:
        raise ValueError(err)
    _check_roles(root)
    text = json.dumps({'version': 1, 'root': _to_json(root, root=True)}, ensure_ascii=False, indent=2)
    _write_atomic(os.path.join(_layouts_dir(), name + '.json'), text + '\n')


# ---------------------------------------------------------------- 设备记录

@dataclass
class DeviceRecord:
    workbench: str
    term: str
    width: int
    height: int
    layout: str


def normalize_term(raw: str) -> str:
    return 'iTerm2' if 'iterm2' in (raw or '').lower() else '其他'


def _devices_path() -> str:
    return os.path.join(config_dir(), 'devices')


def load_devices() -> list:
    try:
        with open(_devices_path(), encoding='utf-8') as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        p = line.split('\t')
        if len(p) == 5 and p[2].isdigit() and p[3].isdigit():
            out.append(DeviceRecord(p[0], p[1], int(p[2]), int(p[3]), p[4]))
    return out


def remember_device(rec: DeviceRecord) -> None:
    """同一 (工作台, 终端类型, 宽, 高) 只留一条，后写覆盖。"""
    key = (rec.workbench, rec.term, rec.width, rec.height)
    keep = [r for r in load_devices() if (r.workbench, r.term, r.width, r.height) != key]
    keep.append(rec)
    _write_atomic(_devices_path(), ''.join(
        '%s\t%s\t%d\t%d\t%s\n' % (r.workbench, r.term, r.width, r.height, r.layout) for r in keep))


def pick_default(records, workbench: str, term: str, width: int, height: int, available) -> str:
    """同工作台、同终端类型里尺寸最接近的记录；布局已不存在或无记录时回退「默认」。"""
    cands = [r for r in records if r.workbench == workbench and r.term == term]
    if not cands:
        return BUILTIN_NAME
    best = min(cands, key=lambda r: abs(r.width - width) + abs(r.height - height))
    return best.layout if best.layout in available else BUILTIN_NAME
