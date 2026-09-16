# cw.zsh — 右侧跟随 shell。由 .zshrc 在 CW_DIRFILE 存在时 source。
# 收到 watcher 的 SIGUSR1 即 cd 到 $CW_DIRFILE 中的目录，不打断当前输入。
[[ -n $TMUX && -n $CW_DIRFILE ]] || return 0
TRAPUSR1() {
  local d; [[ -r $CW_DIRFILE ]] || return 0; d=$(<"$CW_DIRFILE")
  [[ -d $d && $d != $PWD ]] || return 0
  builtin cd -- "$d" || return 0
  # zle -M 写在提示符下方的消息区，下次按键自动清除，且不会被 reset-prompt 覆盖
  if zle; then zle reset-prompt; zle -M "cw → ${(D)d}"; else print -P "%F{244}cw → ${(D)d}%f"; fi
}
# set-option -p 默认作用于 active pane（cw 把左 pane 设为 active），必须显式指定本 pane。
# 注意 zsh 不做单词分割，${VAR:+-t $VAR} 会变成单个参数，故用 if 分开写。
if [[ -n ${TMUX_PANE:-} ]]; then
  tmux set-option -p -t "$TMUX_PANE" @cw_ready 1
else
  tmux set-option -p @cw_ready 1
fi
