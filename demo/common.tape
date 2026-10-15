# 共通の設定（record.sh から実行する。作業フォルダは例の写し）
Set Shell "bash"
Set FontFamily "HackGen Console"
Set FontSize 17
Set Width 1500
Set Height 640
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms
Env LANG "ja_JP.UTF-8"
Env LC_ALL "ja_JP.UTF-8"

Hide
Type "set +H; export INPUTRC=@ROOT@/demo/inputrc; bind -f $INPUTRC; export PATH=@ROOT@/.venv/bin:$PATH; clear"
Enter
Sleep 1s
Show
