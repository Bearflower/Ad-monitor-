# AdWatch 部署文档

## 部署架构

```
用户浏览器
    ↓
nginx (43.156.242.184:80)
    ├── / → 数据看板 (静态文件)
    ├── /api/ → dashboard-api (127.0.0.1:8767)
    └── /adwatch/ → adwatch (127.0.0.1:8768)
                        ↓
                    Docker 容器 (adwatch)
                        ├── Python HTTP 服务 (端口 8768)
                        └── SQLite 数据库 (/app/var/adwatch.sqlite3)
```

- **服务器**: 43.156.242.184
- **访问路径**: `http://43.156.242.184/adwatch/`
- **内部端口**: 8768（仅监听 127.0.0.1，通过 nginx 反向代理对外暴露）
- **数据库**: SQLite，持久化在 `./var/adwatch.sqlite3`

## 项目结构

```
Cross_Border_Electronic_Commerce/
├── Dockerfile              # Docker 镜像构建
├── docker-compose.yml      # Docker Compose 编排
├── .dockerignore           # Docker 构建忽略规则
├── pyproject.toml          # Python 项目配置
├── .env                    # 环境变量
├── VERSION                 # 部署版本标记（防幻觉验证）
├── src/                    # 源码目录
│   └── adwatch/            # 主应用
│       ├── cli.py          # CLI 入口
│       ├── dashboard/      # Web 仪表盘
│       ├── reporting/      # 报告模块
│       ├── strategy/       # 策略规则
│       └── ...
└── var/                    # 数据目录（SQLite 持久化）
```

## 部署流程

### 首次部署

```bash
# 1. 打包
tar czf /tmp/adwatch-deploy.tar.gz \
  Dockerfile docker-compose.yml .dockerignore pyproject.toml .env VERSION src/

# 2. 上传到服务器
scp /tmp/adwatch-deploy.tar.gz root@43.156.242.184:/root/

# 3. SSH 到服务器
ssh root@43.156.242.184

# 4. 解压
mkdir -p /root/adwatch && cd /root/adwatch
tar xzf /root/adwatch-deploy.tar.gz -C /root/adwatch

# 5. 构建并启动
docker-compose build --no-cache
docker-compose up -d

# 6. 验证
curl -s -o /dev/null -w '%{http_code}' http://localhost/adwatch/
# 应返回 200
```

### 部署后验证（防幻觉检查）

每次部署后必须执行以下验证：

```bash
# 第一层：容器状态
docker ps -f name=adwatch --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# 第二层：镜像 ID 一致性
CONTAINER_IMAGE_ID=$(docker inspect -f '{{.Image}}' adwatch)
LATEST_IMAGE_ID=$(docker images --no-trunc --format '{{.ID}}' | head -1)
[ "$CONTAINER_IMAGE_ID" = "$LATEST_IMAGE_ID" ] && echo "✅ 镜像一致" || echo "❌ 镜像不一致"

# 第三层：VERSION 文件
docker exec adwatch cat /app/VERSION

# 第四层：关键文件 MD5
docker exec adwatch md5sum /app/pyproject.toml

# 第五层：功能验证
curl -s -o /dev/null -w '%{http_code}' http://localhost/adwatch/
```

### 更新部署

```bash
# 本地打包
cd /path/to/Cross_Border_Electronic_Commerce
cat > VERSION << EOF
DEPLOY_TIME=$(date '+%Y-%m-%d %H:%M:%S')
GIT_COMMIT=$(git log --oneline -1 2>/dev/null || echo "no-git")
DEPLOY_ID=$(uuidgen | cut -d- -f1)
FILE_MD5=$(md5sum Dockerfile | cut -d' ' -f1)
EOF
tar czf /tmp/adwatch-deploy.tar.gz Dockerfile docker-compose.yml .dockerignore pyproject.toml .env VERSION src/
scp /tmp/adwatch-deploy.tar.gz root@43.156.242.184:/root/

# 服务器更新
ssh root@43.156.242.184
cd /root/adwatch
tar xzf /root/adwatch-deploy.tar.gz -C /root/adwatch
docker-compose build --no-cache
docker-compose up -d --force-recreate
# 执行验证步骤
```

## nginx 配置

已有配置在 `/etc/nginx/conf.d/dashboard.conf`，反向代理部分：

```nginx
# AdWatch 广告管理系统 - 子路径反向代理
location /adwatch/ {
    proxy_pass http://127.0.0.1:8768/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # 重写后端重定向响应
    proxy_redirect / /adwatch/;

    # 重写 HTML 中的链接，使子路径下能正常跳转
    sub_filter_once off;
    sub_filter 'href="/' 'href="/adwatch/';
    sub_filter 'action="/' 'action="/adwatch/';
}
```

**注意**：`sub_filter` 需要 nginx `http_sub_module` 模块（默认已包含）。

## 容器管理

```bash
# 查看容器状态
docker ps -f name=adwatch

# 查看日志
docker logs adwatch --tail 50

# 重启容器
docker-compose restart

# 停止
docker-compose down

# 进入容器
docker exec -it adwatch /bin/bash
```

## 数据库

- 类型：SQLite
- 位置：`/root/adwatch/var/adwatch.sqlite3`
- 持久化：通过 Docker volume 挂载 `./var:/app/var`

### 备份

```bash
# 备份数据库
cp /root/adwatch/var/adwatch.sqlite3 /root/adwatch/var/adwatch.sqlite3.$(date +%Y%m%d).bak

# 恢复
cp /root/adwatch/var/adwatch.sqlite3.bak /root/adwatch/var/adwatch.sqlite3
docker-compose restart
```

## 页面功能

| 功能 | 路径 | 说明 |
|------|------|------|
| 首页 | `/adwatch/` | 数据看板概览 |
| 广告调优 | `/adwatch/optimization` | ROAS、异常、建议 |
| 收入与广告资金 | `/adwatch/ad-funds` | 收入、充值、分账 |
| SKU 与库存 | `/adwatch/inventory` | SKU 成本、采购入库 |
| 月度经营账 | `/adwatch/operating-statement?year=YYYY` | 12 个月利润/现金分离汇总 |

部署验收必须确认月度经营账可访问，并核对经营净利润不扣采购付款和广告充值。
发布包必须由 `git archive` 生成，不得包含 `.env`、SQLite、日志、凭证、临时目录
或原型 Excel；生产环境继续保留既有 `.env` 与 `var/`。TikTok 自动采集仍关闭。
| 策略规则 | `/adwatch/strategy-rules` | 版本管理、Shadow 模拟 |
| 审批审计 | `/adwatch/approvals` | 审批列表、执行审计、熔断 |
| 合伙人分润 | `/adwatch/profit-sharing` | 利润期间、协议、支付 |
| 知识库 | `/adwatch/knowledge` | 文档搜索与查看 |
| 操作面板 | `/adwatch/operations` | 数据采集、调度管理 |

## 首次使用

1. 访问 `http://43.156.242.184/adwatch/`
2. 进入"操作面板" → 执行"收集数据"获取最新的广告数据
3. 数据采集完成后，首页即可查看广告指标
