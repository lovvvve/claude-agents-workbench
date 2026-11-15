"""cw-layout 子命令的集成测试（隔离 tmux server，假 claude）。

运行：python3 -m unittest tests.test_cw_layout_cli -v（在仓库根目录）
"""
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cwlayout  # noqa: E402
from cwlayout import leaves, load_layout, parse_tmux_layout  # noqa: E402
from tests.tmuxenv import REPO, TmuxEnv  # noqa: E402

# 用户真实的六格布局结构；角色/命令按叶子顺序给出（top→sleep 4242，dsh→带引号和 $ 的 echo）
USER = ('2fab,316x71,0,0{208x71,0,0[208x52,0,0,0,208x18,0,53{163x18,0,53,2,44x18,164,53,4}],'
        '107x71,209,0[107x52,209,0,1,107x9,209,53,3,107x8,209,63,5]}')
SIX_ROLES = [('left', ''), ('extra', 'sleep 4242'), ('extra', ''),
             ('right', ''), ('extra', ''), ('extra', 'echo "a b" $HOME')]


def six_layout():
    root = parse_tmux_layout(USER)
    for leaf, (role, cmd) in zip(leaves(root), SIX_ROLES):
        leaf.role, leaf.cmd, leaf.pane_id = role, cmd, ''
    return root


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


class MenuCliTest(CliCase):
    def menu_out(self, extra_env=None):
        r = self.run_cli(['cw-layout', 'menu', '--session', 'cw'], extra_env=extra_env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_menu_no_saved_layouts_keeps(self):
        self.assertEqual(self.menu_out(), '__keep__')

    def test_menu_running_without_record_defaults_to_current(self):
        # 刚 cw -s 存完、这台设备还没选过：回车应保持当前布局，而不是收回「默认」
        cwlayout.save_layout('六格', six_layout())
        r = self.run_cli(['cw-layout', 'menu', '--session', 'cw', '--running', '--current', '六格'],
                         extra_env={'COLUMNS': '120', 'LINES': '40'})
        self.assertEqual(r.stdout.strip(), '六格', r.stderr)

    def test_menu_non_tty_returns_device_default(self):
        cwlayout.save_layout('监控', cwlayout.builtin_default())
        cwlayout.remember_device(cwlayout.DeviceRecord('cw', '其他', 80, 24, '监控'))
        self.assertEqual(self.menu_out({'COLUMNS': '80', 'LINES': '24'}), '监控')


class ApplyTest(CliCase):
    """内层会话 cw：left 跑假 claude agents，right 是 bash；harness 提供 200x50 的真实客户端。"""

    def setUp(self):
        super().setUp()
        e = self.e
        e.tmux('new-session', '-d', '-s', 'cw', '-x', '200', '-y', '50', 'claude agents')
        self.p0 = e.tmux('display', '-p', '-t', '=cw:', '#{pane_id}')
        e.tmux('set-option', '-p', '-t', self.p0, '@cw_role', 'left')
        self.p1 = e.tmux('split-window', '-h', '-l', '40%', '-t', self.p0, '-P', '-F', '#{pane_id}')
        e.tmux('set-option', '-p', '-t', self.p1, '@cw_role', 'right')
        e.client('tmux attach -t =cw', 200, 50)
        e.wait(lambda: self.client_tty() != '', msg='客户端未接入')
        cwlayout.save_layout('六格', six_layout())

    # ---- 辅助
    def client_tty(self):
        return self.e.tmux('list-clients', '-t', '=cw', '-F', '#{client_tty}').split('\n')[0]

    def apply(self, mode, name, caller=''):
        self.e.tmux('set-option', '-w', '-t', self.p0, '@cw_apply', '%s\t%s\t%s' % (mode, name, caller))
        r = self.run_cli(['cw-layout', 'apply', '--session', 'cw', '--client', self.client_tty()])
        self.assertEqual(r.returncode, 0, r.stderr)

    def tm(self, *a):
        return self.e.tmux(*a)

    def main_window_panes(self):
        return self.tm('list-panes', '-t', self.p0, '-F', '#{pane_id}').split('\n')

    def window_layout(self):
        return self.tm('display', '-p', '-t', self.p0, '#{window_layout}')

    def pane_of_slot(self, i):
        return leaves(parse_tmux_layout(self.window_layout()))[i].pane_id

    def capture(self, pane):
        return self.tm('capture-pane', '-p', '-t', pane)

    def pid_of_role(self, role):
        for line in self.tm('list-panes', '-s', '-t', '=cw', '-F', '#{@cw_role} #{pane_pid}').split('\n'):
            r, _, pid = line.rpartition(' ')
            if r == role:
                return int(pid)

    def pids_of(self, pattern):
        r = subprocess.run(['pgrep', '-f', pattern], capture_output=True, text=True)
        return sorted(int(x) for x in r.stdout.split())

    def wait_cmd(self, pane, cmd):
        self.e.wait(lambda: self.tm('display', '-p', '-t', pane, '#{pane_current_command}') == cmd,
                    msg='%s 未进入 %s' % (pane, cmd))

    def left_column_ratio(self):
        r = parse_tmux_layout(self.window_layout())
        return r.children[0].w / float(r.children[0].w + r.children[1].w)

    def window_width_is(self, w):
        return lambda: self.tm('display', '-p', '-t', self.p0, '#{window_width}') == str(w)

    # ---- 用例
    def test_reshape_builds_and_fills_without_executing(self):
        self.apply('reshape', '六格')
        panes = self.tm('list-panes', '-t', self.p0, '-F',
                        '#{pane_id}\t#{@cw_role}\t#{pane_current_command}\t#{pane_current_path}').split('\n')
        self.assertEqual(len(panes), 6)
        extras = [p.split('\t') for p in panes if p.split('\t')[1] not in ('left', 'right')]
        self.assertEqual(len(extras), 4)
        for pid, _, cmd, path in extras:
            self.assertEqual((cmd, path), ('bash', self.e.home), pid)      # 在 ~，命令没跑
        self.assertIn('echo "a b" $HOME', self.capture(self.pane_of_slot(5)))
        self.assertIn('sleep 4242', self.capture(self.pane_of_slot(1)))
        self.assertEqual(self.tm('show-options', '-wqv', '-t', self.p0, '@cw_layout'), '六格')
        self.assertEqual(self.pane_of_slot(0), self.p0)
        self.assertEqual(self.pane_of_slot(3), self.p1)

    def test_fill_special_chars_not_executed(self):
        self.apply('reshape', '六格')
        out = self.capture(self.pane_of_slot(5))
        self.assertIn('echo "a b" $HOME', out)
        self.assertNotIn('a b ' + self.e.home, out)                          # 没有被执行
        left = self.capture(self.p0)                                         # ① 绝不能被填入任何文本
        self.assertNotIn('echo', left)
        self.assertNotIn('sleep 4242', left)

    def test_reshape_keeps_processes_and_left_pane(self):
        left_pid = self.pid_of_role('left')
        busy = self.tm('split-window', '-d', '-t', self.p1, '-P', '-F', '#{pane_id}')
        self.wait_cmd(busy, 'bash')
        self.tm('send-keys', '-t', busy, 'sleep 777', 'Enter')
        self.wait_cmd(busy, 'sleep')
        self.apply('reshape', '默认')
        self.assertEqual(self.pid_of_role('left'), left_pid)                 # ① 没有重启
        self.assertEqual(self.main_window_panes(), [self.p0, self.p1])
        parked = self.tm('list-windows', '-t', '=cw', '-F', '#{window_name} #{@cw_parked}')
        self.assertIn('保留 1', parked)
        self.assertTrue(self.pids_of('^sleep 777$'))

    def test_reshape_reuses_parked_matching_cmd(self):
        self.apply('reshape', '六格')
        slot1 = self.pane_of_slot(1)
        self.tm('send-keys', '-t', slot1, 'Enter')                           # 用户按回车，sleep 4242 开始跑
        self.wait_cmd(slot1, 'sleep')
        pid = self.pids_of('^sleep 4242$')
        self.apply('reshape', '默认')
        self.assertEqual(len(self.main_window_panes()), 2)
        self.apply('reshape', '六格')
        self.assertEqual(self.pids_of('^sleep 4242$'), pid)                  # 进程没变
        self.assertEqual(self.pane_of_slot(1), slot1)
        self.assertEqual(self.tm('display', '-p', '-t', slot1, '#{pane_current_command}'), 'sleep')

    def test_running_shell_script_not_treated_as_idle(self):
        # bash 脚本在前台时 pane_current_command 也是 bash，但它不是「停在提示符的空 shell」
        script = self.tm('split-window', '-d', '-t', self.p1, '-P', '-F', '#{pane_id}')
        self.wait_cmd(script, 'bash')
        self.tm('send-keys', '-t', script, "bash -c 'sleep 600; :'", 'Enter')
        self.e.wait(lambda: self.pids_of("^bash -c sleep 600; :$"), msg='脚本未启动')
        self.apply('reshape', '六格')
        self.assertNotIn(script, self.main_window_panes())                  # 挪进了「保留」，没被拿来复用
        self.assertNotIn('sleep 4242', self.capture(script))                # 没往运行中的脚本里打字

    def test_park_many_panes_on_short_terminal(self):
        # 24 行终端：往「保留」窗口挪 6 个忙碌 pane，不能因为一直对半拆同一个 pane 而失败
        self.e.resize_client(200, 24)
        self.e.wait(self.window_width_is(200))
        self.e.wait(lambda: self.tm('display', '-p', '-t', self.p0, '#{window_height}') == '23')
        busy = []
        for i in range(6):
            self.tm('select-layout', '-t', self.p0, 'tiled')
            p = self.tm('split-window', '-d', '-t', self.p1, '-P', '-F', '#{pane_id}')
            self.wait_cmd(p, 'bash')
            self.tm('send-keys', '-t', p, 'sleep 77%d' % i, 'Enter')
            self.wait_cmd(p, 'sleep')
            busy.append(p)
        self.apply('reshape', '默认')
        parked = [w for w in self.tm('list-windows', '-t', '=cw', '-F', '#{window_id} #{@cw_parked}').split('\n')
                  if w.endswith(' 1')][0].split()[0]
        self.assertEqual(sorted(self.tm('list-panes', '-t', parked, '-F', '#{pane_id}').split('\n')), sorted(busy))
        for i in range(6):
            self.assertTrue(self.pids_of('^sleep 77%d$' % i))

    def test_tmux_failure_reported_not_traceback(self):
        # tmux 命令中途失败（比如 apply 期间有 pane 被关）：要给出中文提示并返回 1，不能抛 traceback 了事
        failbin = os.path.join(self.e.root, 'failbin')
        os.makedirs(failbin)
        real = shutil.which('tmux')
        with open(os.path.join(failbin, 'tmux'), 'w') as f:
            f.write('#!/bin/sh\nif [ "$1" = select-layout ] && [ "$4" != tiled ]; then\n'
                    '  echo "fake select-layout failure" >&2; exit 1\nfi\nexec %s "$@"\n' % real)
        os.chmod(os.path.join(failbin, 'tmux'), 0o755)
        self.tm('set-option', '-w', '-t', self.p0, '@cw_apply', 'reshape\t六格\t')
        r = self.run_cli(['cw-layout', 'apply', '--session', 'cw', '--client', self.client_tty()],
                         extra_env={'PATH': failbin + os.pathsep + self.e.env['PATH']})
        self.assertEqual(r.returncode, 1)
        self.assertNotIn('Traceback', r.stderr)
        self.assertIn('cw: 布局「六格」套用失败', r.stderr)

    def test_fill_tmux_special_texts_verbatim(self):
        # 结尾 ; 会被 tmux 当命令分隔符、以 - 开头会被当选项：都要原样填进命令行，且不执行
        cmds = ['find . -name x -exec echo {} \\;', 'echo done;', '--version']
        root = cwlayout.Node('lr', children=[
            cwlayout.Node('pane', 40, role='left'),
            cwlayout.Node('tb', 60, children=[cwlayout.Node('pane', 40, role='right')] +
                          [cwlayout.Node('pane', 20, role='extra', cmd=c) for c in cmds])])
        cwlayout.save_layout('特殊', root)
        self.apply('reshape', '特殊')
        for i, c in enumerate(cmds):
            p = self.pane_of_slot(2 + i)
            self.e.wait(lambda: c in self.capture(p), msg='%r 未原样填入：%r' % (c, self.capture(p)))
            self.assertEqual(self.tm('display', '-p', '-t', p, '#{pane_current_command}'), 'bash')

    def test_caller_pane_reused_not_parked(self):
        caller = self.tm('split-window', '-d', '-t', self.p1, '-P', '-F', '#{pane_id}')
        self.wait_cmd(caller, 'bash')
        self.tm('send-keys', '-t', caller, 'sleep 1', 'Enter')               # 模拟正在跑 cw，马上会结束
        self.wait_cmd(caller, 'sleep')
        self.apply('reshape', '六格', caller=caller)
        self.assertIn(caller, self.main_window_panes())

    def test_rescale_same_layout_after_resize(self):
        self.apply('reshape', '六格')
        before = sorted(self.main_window_panes())
        self.e.resize_client(140, 40)
        self.e.wait(self.window_width_is(140), msg='窗口未跟随客户端缩小')
        self.apply('rescale', '六格')
        self.assertEqual(sorted(self.main_window_panes()), before)
        self.assertAlmostEqual(self.left_column_ratio(), 0.66, delta=0.02)

    def test_rescale_mismatch_does_nothing(self):
        self.apply('reshape', '六格')
        self.tm('split-window', '-d', '-t', self.pane_of_slot(2))
        layout = self.window_layout()
        self.apply('rescale', '六格')
        self.assertEqual(self.window_layout(), layout)

    def test_apply_small_window(self):
        self.e.resize_client(40, 12)
        self.e.wait(self.window_width_is(40), msg='窗口未跟随客户端缩小')
        self.apply('reshape', '六格')
        self.assertEqual(len(self.main_window_panes()), 6)


if __name__ == '__main__':
    unittest.main()
