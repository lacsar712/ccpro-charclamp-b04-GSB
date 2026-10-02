# CharClamp-01 · 炭窑焖烧志

窑场炭窑与焖烧班次台账基线项目（Litestar + SQLAlchemy 2 + Jinja2 + HTMX）。

## 技术栈

| 层 | 技术 |
| --- | --- |
| Web | Litestar · Jinja2 · HTMX CDN · Session 认证 |
| 数据 | SQLAlchemy 2（async） · PostgreSQL 15 |
| 部署 | Docker Compose · Uvicorn |
| 结构 | `domain/` · `infra/` · `web/` 分层（非 Django apps） |

## 路径与端口

- **项目路径**：`d:\work\document\bytecode\claudeCodePro\CharClamp\CharClamp-01`
- **Web**：http://localhost:4750
- **PostgreSQL**：localhost:6150

## 演示账号

| 用户名 | 密码 | 角色 |
| --- | --- | --- |
| `admin` | `123456` | 管理员 |
| `worker` | `123456` | 操作工 |
| `worker2` | `123456` | 操作工 |

登录页已预填 `admin` / `123456`。entrypoint 会建表并写入种子数据（窑场 **乌石岗焖烧坞**，窑号如 **坞东-甲 / 坞东-乙 / 河沿-丙**）。

## 主界面：焖烧时间轴

登录后进入全宽 **焖烧时间轴**（不再使用侧栏 + 双 CRUD 列表）：

1. **顶部窑剪影行**：每座炭窑以 SVG 剪影展示；点击某窑用 HTMX 局部刷新下方时间轴，并更新地址栏 `?clamp_id=`；「全部窑」取消筛选。
2. **纵向时间轴**：按开始时间倒序列出 `BurnShift`；每条卡片带窑号徽章（再点可开抽屉）、峰值温度、炭品与当前窑态。
3. **侧抽屉（非独立编辑页）**：「登记班次」写入新班次；点窑徽章打开操作抽屉，可标记「已出炭」（受峰值规则约束）。

## 业务规则

炭窑状态不可设为「已出炭」（`drawn`），除非该窑**最近一条** `BurnShift` 的 `peakTempC` 已记录且 **≥ 400℃**。

规则实现：`src/charclamp/domain/rules.py`

## 班次改写权限矩阵

时间轴卡片上的「改写」在侧抽屉完成，抽屉渲染与后台保存共用同一套规则（`shift_edit_perm`）：

| 角色 | 可改字段 | 范围 |
| --- | --- | --- |
| 操作工 | 仅备注 | 本人登记的**当天**班次 |
| 管理员 | 备注 / 峰值温度 / 开始时刻 / 炭品等级 | 未出炭窑上的班次 |
| 全员 | 只读 | 已出炭（`drawn`）窑上的班次 |

- 后台保存逐字段校验：操作工提交峰值/开始时刻等字段返回 **403**，不会部分生效。
- 并发改写同一班次走乐观锁（`version` 列）：仅一笔生效，其余返回 **409**。
- 种子数据覆盖：worker 的当日班（可改备注）、worker 的昨日班（非当天只读）、worker2 的当日班（他人班只读）、河沿-丙已出炭窑班（全员只读）。

## 快速启动

```bash
cd d:\work\document\bytecode\claudeCodePro\CharClamp\CharClamp-01
docker compose up --build
```

浏览器打开 http://localhost:4750

## 目录结构

```
CharClamp-01/
├── docker-compose.yml
├── Dockerfile
├── entrypoint.sh
└── src/charclamp/
    ├── main.py
    ├── domain/          # models + rules
    ├── infra/           # db + seed + security
    └── web/             # controllers + templates + static
```
