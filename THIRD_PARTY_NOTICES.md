# 第三方组件与许可说明

本仓库的源代码采用 MIT License。下列第三方组件不因本仓库的 MIT 许可而改变其原有许可；分发源码或打包后的程序时，请同时遵守对应条款。

## 运行时组件

### PySide6 / Qt 6

- 版本：PySide6 6.12.0（Qt 6）
- 许可：LGPLv3 / GPLv3 / Qt 商业许可，按具体使用方式选择。
- 项目主页：https://www.qt.io/
- 许可说明：https://www.qt.io/licensing/
- LGPL 说明：https://doc.qt.io/qt-6/lgpl.html

如果分发包含 Qt 的 EXE，请自行确认并满足 LGPLv3 的要求（例如动态链接、保留版权与许可声明、提供替换 Qt 库的方式等）。本仓库不提供法律意见。

### Python

- 版本：3.11+（开发机为 3.15）
- 许可：Python Software Foundation License 2.0
- 项目主页：https://www.python.org/

### SQLite

- 版本：随 Python 内置（开发机为 3.53.4）
- 许可：Public Domain
- 项目主页：https://www.sqlite.org/

## 构建与开发工具

### PyInstaller

- 版本：6.22.3
- 许可：GPL-2.0-or-later，附带 bootloader 例外条款
- 项目主页：https://pyinstaller.org/
- 许可说明：https://pyinstaller.org/en/stable/license.html

PyInstaller 仅用于构建，不进入源码运行路径。它生成的 bootloader 受其例外条款约束；分发构建产物前请阅读其许可说明。

### Pillow

- 版本：12.3.0
- 许可：HPND License
- 项目主页：https://python-pillow.org/

仅用于从授权 Logo 生成 Windows 图标，不是程序运行依赖。

### pytest

- 版本：8.x（可选开发依赖）
- 许可：MIT License
- 项目主页：https://pytest.org/

## 服务与字体

### DeepSeek API

- 用途：可选的在线问答服务。
- 说明：本仓库不包含 DeepSeek 的 SDK、密钥或服务端代码；调用时需遵守 DeepSeek 的服务条款与隐私政策。
- 项目主页：https://www.deepseek.com/

### 中文字体

程序优先使用系统已安装的 Microsoft YaHei 等中文字体，不随仓库分发字体文件。若在没有中文字体的环境中运行，界面可能出现方框或回退字体。

## 内容与品牌素材

- 本仓库不包含任何公众号文章正文、文章图片、数据库、用户 Logo 或打包产物。
- `assets/brand/app_icon.ico` 是中性的占位图标；如替换为自有 Logo，请确认你拥有相应版权或授权。
- 示例文本、测试数据均为合成内容，不对应真实文章或人物。
