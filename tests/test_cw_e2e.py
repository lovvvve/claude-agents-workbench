"""通过 cw 的端到端验收测试（隔离 tmux server；harness 提供真实 tty；假 claude）。

运行：python3 -m unittest tests.test_cw_e2e -v（在仓库根目录）
"""
import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cwlayout  # noqa: E402
from cwlayout import leaves, parse_tmux_layout  # noqa: E402
from tests.test_cw_layout_cli import six_layout  # noqa: E402
from tests.tmuxenv import REPO, TmuxEnv  # noqa: E402

CW = os.path.join(REPO, 'cw')


class CwE2E(unittest.TestCase):
    def setUp(self):
        self.e = TmuxEnv()
        self._old_cfg = os.environ.get('CW_CONFIG_DIR')
        os.environ['CW_CONFIG_DIR'] = self.e.cfg

    def tearDown(self):
        subprocess.run([CW, '-k'], env=self.e.env, capture_output=True)
        self.e.close()
        if self._old_cfg is None:
            os.environ.pop('CW_CONFIG_DIR', None)
        else:
            os.environ['CW_CONFIG_DIR'] = self._old_cfg

    # ---- 辅助
    def run_cw(self, *args):
        r = subprocess.run([CW, *args], env=self.e.env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        return r.stdout + r.stderr

    def type_after(self, prompt, text, name='c', timeout=10.0):
        self.e.wait(lambda: prompt in self.e.harness_screen(name), timeout, msg='等待提示「%s」' % prompt)
        if text:
            self.e.harness('send-keys', '-t', name + ':', '-l', text)
        self.e.harness('send-keys', '-t', name + ':', 'Enter')

    def answer(self, text, name='c'):
        self.type_after('选择 [', text, name)

    def session_exists(self):
        return subprocess.run(['tmux', 'has-session', '-t', '=cw'], env=self.e.env,
                              capture_output=True).returncode == 0

    def all_panes(self):
        out = self.e.tmux('list-panes', '-s', '-t', '=cw', '-F', '#{pane_id}\t#{@cw_role}\t#{window_id}')
        return [l.split('\t') for l in out.split('\n') if l]

    def left_pane(self):
        return next(p for p, r, _ in self.all_panes() if r == 'left')

    def main_panes(self):
        return self.e.tmux('list-panes', '-t', self.left_pane(), '-F', '#{pane_id}').split('\n')

    def roles_count(self, role):
        return sum(1 for _, r, _ in self.all_panes() if r == role)

    def window_layout(self):
        return self.e.tmux('display', '-p', '-t', self.left_pane(), '#{window_layout}')

    def window_width(self):
        return int(self.e.tmux('display', '-p', '-t', self.left_pane(), '#{window_width}'))

    def parked_window(self):
        for line in self.e.tmux('list-windows', '-t', '=cw', '-F', '#{window_id} #{@cw_parked}').split('\n'):
            wid, _, flag = line.partition(' ')
            if flag == '1':
                return wid

    def start_with_layout(self, name='六格'):
        cwlayout.save_layout(name, six_layout())
        cwlayout.remember_device(cwlayout.DeviceRecord('cw', '其他', 200, 50, name))
        self.e.client(CW, 200, 50)
        self.answer('')
        # 等 apply 完整结束（它最后才设 @cw_layout）；只看 pane 数会拿到中间的 tiled 状态
        self.e.wait(lambda: len(self.main_panes()) == 6 and self.e.tmux(
            'show-options', '-wqv', '-t', self.left_pane(), '@cw_layout') == name, 15, msg='六格布局未就位')

    def switch_to(self, index, name):
        self.e.client(CW, 200, 50, name=name)
        self.answer(str(index), name)

    # ---- 用例
    def test_no_saved_layouts_behaves_as_before(self):
        self.e.client(CW, 200, 50)
        self.e.wait(self.session_exists, msg='工作台未创建')
        self.e.wait(lambda: self.roles_count('right') == 1, msg='右 pane 未创建')
        self.assertEqual(len(self.main_panes()), 2)
        self.assertEqual(sorted(r for _, r, _ in self.all_panes()), ['left', 'right'])
        self.assertNotIn('选择 [', self.e.harness_screen())

    def test_save_then_pick_restores_layout(self):
        self.e.client(CW, 200, 50)
        self.e.wait(lambda: self.session_exists() and self.roles_count('right') == 1, msg='工作台未就绪')
        right = next(p for p, r, _ in self.all_panes() if r == 'right')
        extra = self.e.tmux('split-window', '-v', '-t', right, '-P', '-F', '#{pane_id}')
        self.e.wait(lambda: self.e.tmux('display', '-p', '-t', extra, '#{pane_current_command}') == 'bash')
        self.e.tmux('send-keys', '-t', extra, 'sleep 4242', 'Enter')
        self.e.wait(lambda: self.e.tmux('display', '-p', '-t', extra, '#{pane_current_command}') == 'sleep')
        # cw -s：输入名字，命令确认直接回车保留
        self.e.client(CW + ' -s', 120, 30, name='s')
        self.type_after('布局名：', '三格', name='s')
        self.type_after('回车保留', '', name='s')
        # save 先写文件、再设 @cw_layout：两者都到位才算保存完成
        self.e.wait(lambda: '三格' in cwlayout.list_layouts() and self.e.tmux(
            'show-options', '-wqv', '-t', self.left_pane(), '@cw_layout') == '三格', msg='布局未保存')
        self.run_cw('-k')
        self.e.close_client('c')
        self.e.close_client('s')
        # 重新 cw：列表里 1=默认，2=三格
        self.e.client(CW, 200, 50, name='c3')
        self.answer('2', 'c3')
        self.e.wait(lambda: self.session_exists() and len(self.main_panes()) == 3, 15, msg='三格未还原')
        slot = leaves(parse_tmux_layout(self.window_layout()))[2].pane_id
        self.e.wait(lambda: 'sleep 4242' in self.e.tmux('capture-pane', '-p', '-t', slot), msg='命令未填入')
        self.assertEqual(self.e.tmux('display', '-p', '-t', slot, '#{pane_current_command}'), 'bash')

    def test_reenter_same_layout_only_rescales(self):
        self.start_with_layout()
        ids = sorted(self.main_panes())
        self.e.close_client('c')
        self.e.client(CW, 150, 45, name='c2')
        self.answer('', 'c2')                                   # 默认项 = 六格 = 当前布局
        self.e.wait(lambda: self.window_width() == 150, msg='窗口未跟随新客户端')
        self.e.wait(lambda: abs(self._left_ratio() - 0.66) < 0.02, msg='比例未修正')
        self.assertEqual(sorted(self.main_panes()), ids)

    def _left_ratio(self):
        r = parse_tmux_layout(self.window_layout())
        return r.children[0].w / float(r.children[0].w + r.children[1].w)

    def test_keep_changes_nothing(self):
        self.start_with_layout()
        layout = self.window_layout()
        self.e.client(CW, 200, 50, name='c2')
        self.answer('1', 'c2')                                  # 1 = 保持现状
        self.e.wait(lambda: len(self.e.tmux('list-clients', '-t', '=cw').split('\n')) == 2,
                    msg='第二个客户端未接入')
        self.assertEqual(self.window_layout(), layout)

    def test_cw_finds_roles_from_parked_window(self):
        self.start_with_layout()
        self.switch_to(2, 'c2')                                 # 运行中：1=保持现状 2=默认 3=六格
        self.e.wait(lambda: self.parked_window() is not None and len(self.main_panes()) == 2,
                    15, msg='未切到默认布局')
        self.e.tmux('select-window', '-t', self.parked_window())
        self.e.client(CW, 200, 50, name='c3')
        self.answer('1', 'c3')
        self.e.wait(lambda: len(self.e.tmux('list-clients', '-t', '=cw').split('\n')) == 3,
                    msg='第三个客户端未接入')
        self.assertEqual(self.roles_count('left'), 1)
        self.assertEqual(self.roles_count('right'), 1)

    def test_kill_only_own_watcher(self):
        # 同名工作台、不同运行目录的 watcher（比如用户真实的那个）不能被 cw -k 误杀。
        # 用唯一的工作台名，RED 阶段也碰不到用户真实的 `cw-follow cw …`
        name = 'k%d' % os.getpid()
        other = os.path.join(self.e.root, 'elsewhere', 'cw-dir.cw-' + name)
        os.makedirs(os.path.dirname(other))
        dummy = subprocess.Popen(['bash', '-c', 'exec -a "cw-follow cw-%s %s" sleep 600' % (name, other)])
        try:
            time.sleep(0.3)
            subprocess.run([CW, '-n', name, '-k'], env=self.e.env, capture_output=True)
            time.sleep(0.3)
            self.assertIsNone(dummy.poll(), '别的运行目录下的同名 watcher 被误杀')
        finally:
            dummy.kill()
            dummy.wait()

    def test_status_flag_i(self):
        self.start_with_layout()
        out = self.run_cw('-i')
        self.assertIn('布局=六格', out)


if __name__ == '__main__':
    unittest.main()
