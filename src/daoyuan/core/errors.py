# -*- coding: utf-8 -*-
"""统一异常类型。"""


class DaoyuanError(Exception):
    """项目内所有可预期异常的基类。"""


class ConfigError(DaoyuanError):
    """配置缺失或不合法。"""


class StorageError(DaoyuanError):
    """数据库打开、迁移或读写失败。"""


class CollectorError(DaoyuanError):
    """采集流程错误。"""


class RequestBusyError(CollectorError):
    """另一个采集入口正在发微信请求，本次请求等待超时。"""


class RiskControlError(CollectorError):
    """触发验证页、限流或拒绝访问，必须停止当前批次。"""


class ArticleUnavailableError(CollectorError):
    """单篇文章被删除、违规或迁移，只跳过该篇。"""


class ParseError(CollectorError):
    """页面结构不符合预期或正文为空。"""


class AiError(DaoyuanError):
    """DeepSeek 调用或引用校验失败。"""


class ExportError(DaoyuanError):
    """文档导出失败。"""


class SecurityError(DaoyuanError):
    """密钥存储等安全相关错误。"""
