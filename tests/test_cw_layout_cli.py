"""cw-layout 子命令的集成测试（隔离 tmux server，假 claude）。

运行：python3 -m unittest tests.test_cw_layout_cli -v（在仓库根目录）
"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwlayout import leaves, load_layout  # noqa: E402
from tests.tmuxenv import REPO, TmuxEnv  # noqa: E402


class CliCase(unittest.TestCase):
    """建好隔离环境；子类按需建会话。"""

    def setUp(self):
        self.e = TmuxEnv()
        self._old_cfg = os.environ.get('CW_CONFIG_DIR')
        os.environ['CW_CONFIG_DIR'] = self.e.cfg

    def tearDown(self):
        self.e.close()
        if self._old_cfg is None:
            os.environ.pop('CW_CONFIG_DIR', None)
        else:
            os.environ['CW_CONFIG_DIR'] = self._old_cfg

    def run_cli(self, args, stdin=subprocess.DEVNULL, extra_env=None):
        env = dict(self.e.env, **(extra_env or {}))
        return subprocess.run([os.path.join(REPO, args[0])] + args[1:], env=env,
                              capture_output=True, text=True, stdin=stdin)

    def build_three_panes(self):
        """内层会话 cw：pane0=left，pane1=right，pane2（从 pane1 竖切）跑 sleep 4242。"""
        e = self.e
        e.tmux('new-session', '-d', '-s', 'cw', '-x', '120', '-y', '40')
        p0 = e.tmux('display', '-p', '-t', '=cw:', '#{pane_id}')
        e.tmux('set-option', '-p', '-t', p0, '@cw_role', 'left')
        p1 = e.tmux('split-window', '-h', '-t', p0, '-P', '-F', '#{pane_id}')
        e.tmux('set-option', '-p', '-t', p1, '@cw_role', 'right')
        p2 = e.tmux('split-window', '-v', '-t', p1, '-P', '-F', '#{pane_id}')
        e.wait(lambda: e.tmux('display', '-p', '-t', p2, '#{pane_current_command}') == 'bash')
        e.tmux('send-keys', '-t', p2, 'sleep 4242', 'Enter')
        e.wait(lambda: e.tmux('display', '-p', '-t', p2, '#{pane_current_command}') == 'sleep')
        return p0, p1, p2


class SaveTest(CliCase):
    def setUp(self):
        super().setUp()
        self.p0, self.p1, self.p2 = self.build_three_panes()

    def test_save_captures_roles_cmds_proportions(self):
        r = self.run_cli(['cw-layout', 'save', '--session', 'cw', '--name', '监控', '--yes'])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.e.cfg, 'layouts', '监控.json')))
        ls = leaves(load_layout('监控'))
        self.assertEqual([l.role for l in ls], ['left', 'right', 'extra'])
        self.assertEqual(ls[2].cmd, 'sleep 4242')
        self.assertEqual(self.e.tmux('show-options', '-wqv', '-t', '=cw:', '@cw_layout'), '监控')

    def test_save_caller_pane_is_empty(self):
        r = self.run_cli(['cw-layout', 'save', '--session', 'cw', '--name', 'x', '--yes',
                          '--caller-pane', self.p2])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(leaves(load_layout('x'))[2].cmd, '')

    def test_save_invalid_name_and_not_running(self):
        r = self.run_cli(['cw-layout', 'save', '--session', 'cw', '--name', 'a/b', '--yes'])
        self.assertEqual(r.returncode, 1)
        r = self.run_cli(['cw-layout', 'save', '--session', 'nope', '--name', 'ok', '--yes'])
        self.assertEqual(r.returncode, 1)


if __name__ == '__main__':
    unittest.main()
