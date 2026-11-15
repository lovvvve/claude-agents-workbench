"""cwlayout.py 的单元测试（纯逻辑，不依赖 tmux）。

运行：python3 -m unittest tests.test_cwlayout -v（在仓库根目录）
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwlayout import (  # noqa: E402
    DeviceRecord,
    Node,
    builtin_default,
    layout_checksum,
    leaves,
    list_layouts,
    load_devices,
    load_layout,
    normalize_term,
    parse_tmux_layout,
    pick_default,
    remember_device,
    render_tmux_layout,
    same_shape,
    save_layout,
    validate_name,
)

# 用户在 316x72 外接屏上的真实六格布局（tmux #{window_layout}）
USER = ('2fab,316x71,0,0{208x71,0,0[208x52,0,0,0,208x18,0,53{163x18,0,53,2,44x18,164,53,4}],'
        '107x71,209,0[107x52,209,0,1,107x9,209,53,3,107x8,209,63,5]}')


def leaf_h(root, leaf):
    """叶子的绝对高度（parse_tmux_layout 填入）。"""
    return leaf.h


class LayoutTreeTest(unittest.TestCase):
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
        a = parse_tmux_layout(USER)
        b = parse_tmux_layout(render_tmux_layout(a, 120, 40, list(range(6))))
        self.assertTrue(same_shape(a, b))
        self.assertFalse(same_shape(a, builtin_default()))


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.cfg = tempfile.mkdtemp(prefix='cwcfg-')
        self._old = os.environ.get('CW_CONFIG_DIR')
        os.environ['CW_CONFIG_DIR'] = self.cfg

    def tearDown(self):
        if self._old is None:
            os.environ.pop('CW_CONFIG_DIR', None)
        else:
            os.environ['CW_CONFIG_DIR'] = self._old
        shutil.rmtree(self.cfg, ignore_errors=True)

    def test_validate_name(self):
        self.assertIsNone(validate_name('监控 布局'))
        for bad in ('', 'a/b', '.hidden', '默认'):
            self.assertIsNotNone(validate_name(bad))

    def test_roundtrip_cmd_special_chars(self):
        root = builtin_default()
        root.children[1:1] = [Node('pane', 10, role='extra', cmd='echo "a b" $HOME \'q\'')]
        save_layout('中文 名', root)
        self.assertIn('中文 名', list_layouts())
        got = load_layout('中文 名')
        self.assertEqual(leaves(got)[1].cmd, 'echo "a b" $HOME \'q\'')

    def test_load_builtin(self):
        self.assertTrue(same_shape(load_layout('默认'), builtin_default()))

    def test_pick_default_nearest_same_term_and_workbench(self):
        rs = [DeviceRecord('cw', 'iTerm2', 316, 72, 'A'), DeviceRecord('cw', 'iTerm2', 180, 50, 'B'),
              DeviceRecord('cw', '其他', 316, 72, 'C'), DeviceRecord('cw-api', 'iTerm2', 316, 72, 'D')]
        avail = {'A', 'B', 'C', 'D'}
        self.assertEqual(pick_default(rs, 'cw', 'iTerm2', 310, 70, avail), 'A')
        self.assertEqual(pick_default(rs, 'cw', 'iTerm2', 190, 52, avail), 'B')
        self.assertEqual(pick_default(rs, 'cw', '其他', 100, 30, avail), 'C')

    def test_pick_default_deleted_layout_falls_back(self):
        rs = [DeviceRecord('cw', 'iTerm2', 316, 72, 'gone')]
        self.assertEqual(pick_default(rs, 'cw', 'iTerm2', 316, 72, set()), '默认')

    def test_remember_device_overwrites_same_key(self):
        remember_device(DeviceRecord('cw', 'iTerm2', 316, 72, 'A'))
        remember_device(DeviceRecord('cw', 'iTerm2', 316, 72, 'B'))
        self.assertEqual([r.layout for r in load_devices()], ['B'])

    def test_validate_name_rejects_control_chars(self):
        self.assertIsNotNone(validate_name('a\tb'))     # 制表符会破坏 devices 文件
        self.assertIsNotNone(validate_name('a\nb'))

    def test_save_requires_exactly_one_left_and_right(self):
        bad = Node('lr', children=[Node('pane', 50, role='left'), Node('pane', 50, role='extra')])
        with self.assertRaises(ValueError):
            save_layout('缺右', bad)

    def test_normalize_term(self):
        self.assertEqual(normalize_term('iTerm2 3.7.7'), 'iTerm2')
        self.assertEqual(normalize_term(''), '其他')


if __name__ == '__main__':
    unittest.main()
