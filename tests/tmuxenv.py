"""隔离的 tmux 测试环境。

- 内层 server：被测对象（cw / cw-layout 用的就是它），经 TMUX_TMPDIR 与用户真实 server 隔开；
- 外层 harness server（tmux -L）：开会话跑 `tmux attach` / `cw`，给内层提供真实 tty 客户端；
- HOME / CW_CONFIG_DIR / XDG_RUNTIME_DIR 都指向临时目录，PATH 前置 tests/fakebin（假 claude）。
"""
import os
import shutil
import subprocess
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKEBIN = os.path.join(REPO, 'tests', 'fakebin')


class TmuxEnv:
    _seq = 0

    def __init__(self):
        TmuxEnv._seq += 1
        self.root = tempfile.mkdtemp(prefix='cwt-')
        self.home = os.path.join(self.root, 'home')
        self.cfg = os.path.join(self.root, 'cfg')
        self.run = os.path.join(self.root, 'run')
        sock = os.path.join(self.root, 'sock')
        for d in (self.home, self.cfg, self.run, sock):
            os.makedirs(d, mode=0o700)
        env = dict(os.environ)
        for k in ('TMUX', 'TMUX_PANE', 'LC_TERMINAL', 'STY', 'COLUMNS', 'LINES'):
            env.pop(k, None)
        env.update(TMUX_TMPDIR=sock, HOME=self.home, CW_CONFIG_DIR=self.cfg,
                   XDG_RUNTIME_DIR=self.run, SHELL='/bin/bash',
                   PATH=FAKEBIN + os.pathsep + env.get('PATH', ''))
        self.env = env
        self.harness_name = 'cwt-%d-%d-harness' % (os.getpid(), TmuxEnv._seq)

    def _run(self, argv, check=True):
        return subprocess.run(argv, env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, check=check)

    def tmux(self, *args) -> str:
        """对内层被测 server 执行 tmux 命令，返回去掉末尾换行的 stdout。"""
        return self._run(['tmux', *args]).stdout.rstrip('\n')

    def harness(self, *args) -> str:
        return self._run(['tmux', '-L', self.harness_name, *args]).stdout.rstrip('\n')

    def client(self, cmd: str, width: int, height: int, name: str = 'c') -> None:
        """在 harness 上开一个 width×height 的会话执行 cmd（作为内层的真实 tty 客户端）。"""
        self.harness('new-session', '-d', '-s', name, '-x', str(width), '-y', str(height),
                     'env -u TMUX -u TMUX_PANE %s; sleep 3600' % cmd)
        self.harness('set-option', '-w', '-t', name + ':', 'window-size', 'manual')
        self.resize_client(width, height, name)

    def resize_client(self, width: int, height: int, name: str = 'c') -> None:
        self.harness('resize-window', '-t', name + ':', '-x', str(width), '-y', str(height))

    def close_client(self, name: str = 'c') -> None:
        self._run(['tmux', '-L', self.harness_name, 'kill-session', '-t', '=' + name], check=False)

    def harness_screen(self, name: str = 'c') -> str:
        return self.harness('capture-pane', '-p', '-t', name + ':')

    def wait(self, pred, timeout: float = 10.0, msg: str = '') -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if pred():
                    return
            except subprocess.CalledProcessError:
                pass
            time.sleep(0.1)
        raise AssertionError('等待超时（%.0fs）%s' % (timeout, msg))

    def close(self) -> None:
        self._run(['tmux', '-L', self.harness_name, 'kill-server'], check=False)
        self._run(['tmux', 'kill-server'], check=False)
        shutil.rmtree(self.root, ignore_errors=True)
