"""L5 连接器：与外部世界的边界（架构设计 L5）"""


class ConnectorError(Exception):
    """读取外部数据失败。code 是错误码，params 用于填入错误信息"""

    def __init__(self, code: str, **params: str):
        super().__init__(code)
        self.code = code
        self.params = params
