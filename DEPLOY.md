# 一键部署说明

## Windows（本机或服务器）

1. 安装 [Python 3.10+](https://www.python.org/downloads/)（勾选 Add to PATH）
2. 双击 **`deploy.bat`**
3. 浏览器自动打开管理后台，默认账号：**admin / admin123**

云服务器请在安全组放行 **TCP 8787**，并将 `config.json` 里 `Server.public_url` 和 `Box_Server_URL` 改成公网地址（或重新运行一次 `python setup_deploy.py`）。

## Linux 服务器

```bash
chmod +x deploy.sh
./deploy.sh
```

### 已有宝塔 / wwwroot 站点（互不影响）

在 **SSH 已连接的 Cursor 窗口**里，把本项目放到任意非 wwwroot 目录后执行：

```bash
chmod +x deploy-server.sh
PUBLIC_URL=http://你的公网IP:8787 ./deploy-server.sh
```

- 安装目录默认：`/opt/cai-install_stloader`（**不会**写入 `/www/wwwroot`）
- 监听端口：**8787**（不用 80/443，不改 Nginx / 宝塔配置）
- 仅新增 systemd 服务：`cai-stloader-web`

后台运行示例（不用 systemd 时）：

```bash
nohup python3 web_server.py --host 0.0.0.0 --port 8787 > web.log 2>&1 &
```

## 游戏盒子（用户电脑）

1. 确保服务器 `deploy.bat` 已启动
2. 用户电脑 `config.json` 中 `Box_Server_URL` 指向服务器（`setup_deploy` 会自动写入本机局域网地址；跨网需手改公网 IP）
3. 双击 **`start_box.bat`**

## 常用地址

| 功能 | 路径 |
|------|------|
| 超级管理 | `/admin/login` |
| 代理工作台 | `/portal` |
| 用户激活 | `irm http://服务器:8787 \| iex` |
| 域名首页 | 浏览器访问 `/` 将跳转到 Steam 商店官网（`irm` 命令不变） |

数据保存在服务器 **`data/app.db`**，无需再维护 `admin_db.json`。
