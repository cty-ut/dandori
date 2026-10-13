"""把 pytest 的失败（junit.xml）转成 GitHub 的注释（annotation）

注释会显示在 PR 的代码旁边，而且不登录也能通过 API 读取（日志需要登录）。
GitHub 每个步骤最多显示 10 条错误注释。
"""

import sys
import xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding="utf-8")       # Windows 的默认编码（cp1252）写不了日文
root = ET.parse(sys.argv[1]).getroot()
for case in root.iter("testcase"):
    for problem in [*case.iter("failure"), *case.iter("error")]:
        name = f"{case.get('classname')}.{case.get('name')}"
        text = (problem.get("message") or "") + "\n" + (problem.text or "")
        # 注释的内容不能有换行：用 %0A 表示
        body = text.strip()[-1500:].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        path = case.get("file") or "tests"
        line = int(case.get("line") or 0) + 1          # junit 的行号从 0 开始
        print(f"::error file={path},line={line},title={name}::{body}")
