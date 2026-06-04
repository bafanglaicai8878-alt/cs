# CS Steam 游戏盒子

Steam 游戏入库、CDK 激活与 Web 管理台。

## 一键部署（推荐）

**Windows：** 双击 `deploy.bat`  
**Linux：** `bash 运行.sh` 或 `bash 后台运行.sh`

自动完成：安装依赖 → 生成/合并 `config.json` → 初始化数据库 → 启动 Web（默认 `admin` / `admin123`）

详细说明见 [DEPLOY.md](DEPLOY.md) 与 [小白部署.txt](小白部署.txt)

- **服务器：** `bash 后台运行.sh`（后台）或 `bash 运行.sh`（前台）
- **停止服务：** `bash stop_web.sh` 或 `kill $(cat web.pid)`
- **用户盒子：** `start_box.bat`（需 `config.json` 中 `Box_Server_URL` 指向服务器）

## 功能

- 游戏搜索与一键入库
- CDK 生成与兑换（`irm 服务器 | iex`）
- Web 管理台（搜索游戏、批量生成 CDK、多 GitHub Token 清单同步）
- 内置 Hook 部署（PowerShell 静默激活）

## 盒子与线上统一账号

在 **服务器** 与 **本地盒子** 的 `config.json` 中设置相同项：

```json
"Box_Server_URL": "http://你的公网IP:8787",
"Box_Vip_Days_Per_CDK": 30
```

- 盒子 GUI 登录/注册/VIP 走线上数据库（与 Web 同机）
- 代理在 Web 后台生成 CDK；用户在盒子激活 CDK 后自动获得 VIP
- 不填 `Box_Server_URL` 时，盒子仍使用本机 `client_db.json`（仅本电脑）

## 线上数据库（SQLite / MySQL）

在 **服务器** `config.json` 启用后，代理/CDK/盒子用户/游戏清单缓存均写入数据库。

**SQLite（单机默认）：**

```json
"Database": {
  "enabled": true,
  "engine": "sqlite",
  "path": "data/app.db"
}
```

**MySQL（推荐生产环境）：**

```json
"Database": {
  "enabled": true,
  "engine": "mysql",
  "host": "127.0.0.1",
  "port": 3306,
  "user": "cai_stloader",
  "password": "你的密码",
  "database": "cai_stloader"
}
```

迁移已有 JSON / SQLite 到 MySQL：

```bash
python3 migrate_to_database.py
bash 后台运行.sh
```

MySQL 模式下 `admin_db.json`、`cdk_db.json` 等不再更新，数据以数据库为准（旧 JSON 可备份后删除）。

## GitHub Token（清单同步）

后台 → 系统设置 → **GitHub Token**，每行一个 Token，可叠加 API 配额加速全量清单抓取。

## 快速开始

```bash
pip install -r requirements.txt
cp config.example.json config.json
python3 setup_deploy.py
python3 web_server.py --host 0.0.0.0 --port 8787
```

## 其他入口

| 命令 | 说明 |
|------|------|
| `python frontend_box.py` | 游戏盒子 GUI |
| `python gen_cdk.py 730 --count 5` | 生成 CDK |
| `irm http://IP:8787/hook \| iex` | 仅安装 Hook |
