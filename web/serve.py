"""云端启动入口：直接读取 PORT 环境变量，避免依赖 shell 展开。"""
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, log_level="info")
