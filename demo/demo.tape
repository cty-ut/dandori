# 短い版（約 30 秒）：日本語のプログラム → 説明 → 試し
#   bash demo/record.sh demo    （リポジトリの直下で）
Output "@ROOT@/demo/demo.gif"
Source common.tape

Type "# 段取り：日本語で書く、業務自動化のためのプログラミング言語"
Enter
Sleep 1.5s
Type "cat 入金消込.dandori"
Enter
Sleep 6s

Type "clear"
Enter
Type "# 実行する前に、何を読み書きするかを確かめる"
Enter
Sleep 1s
Type "dandori 説明 入金消込.dandori"
Enter
Sleep 5s

Type "clear"
Enter
Type "# 試しに実行する（ファイルは書き出さない）"
Enter
Sleep 1s
Type "dandori 試し 入金消込.dandori --今日 2026年11月2日"
Enter
Sleep 8s
