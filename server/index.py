"""Vercel Python Serverless 入口。

Vercel 的 Python 运行时会自动发现本文件里名为 ``app`` 的 ASGI 变量，
并把它作为 Serverless Function 运行、接管所有进来的请求。

这里只做一件事：把 app 包里已经装配好的 FastAPI 实例导出。
之所以单独留一个入口文件而不是让 Vercel 直接指向 app/main.py，
是因为 Vercel 约定入口在项目根（server/）下，这样部署配置最简单，
也不必改动 app/ 内部任何相对导入。
"""

from app.main import app  # noqa: F401  —— 供 Vercel 运行时按名字发现
