# 完全版（約 1 分半）：日本語のプログラム → 説明 → 試し → AI が書く → 基盤
#   DEEPSEEK_API_KEY を設定してから：bash demo/record.sh full
# AI の場面は本物のモデル（DeepSeek）を呼ぶ。結果は毎回少し違う
Output "@ROOT@/demo/full.gif"
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
Sleep 7s

Type "clear"
Enter
Type "# AI が日本語の要望からプログラムを書く（CSV の中身は送らない）"
Enter
Sleep 1s
Type `dandori-ai 作る "「入金明細.csv」の金額を入金日ごとに合計して表示して" --出力 日別集計.dandori --提供元 deepseek --非対話`
Enter
Wait@120s
Sleep 4s
Type "clear"
Enter
Type "cat 日別集計.dandori && dandori 実行 日別集計.dandori"
Enter
Sleep 6s

Type "clear"
Enter
Type "# 基盤：承認したプログラムだけを、予定どおりに実行する"
Enter
Sleep 1s
Type "dandori-platform 登録 入金消込.dandori"
Enter
Sleep 2s
Type "dandori-platform 承認 入金消込 --承認者 田中 --はい | tail -4"
Enter
Sleep 3s
Type "dandori-platform 一覧"
Enter
Sleep 6s
