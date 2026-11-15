"""cwlayout.py 的单元测试（纯逻辑，不依赖 tmux）。

运行：python3 -m unittest tests.test_cwlayout -v（在仓库根目录）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwlayout import (  # noqa: E402
    builtin_default,
    layout_checksum,
    leaves,
    parse_tmux_layout,
    render_tmux_layout,
    same_shape,
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


if __name__ == '__main__':
    unittest.main()
