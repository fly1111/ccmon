# ccmon — Claude Code 会话状态监视器

一眼看清所有正在运行的 Claude Code 会话：**哪个在跑、哪个卡在等你确认、哪个已停**。托盘图标 + 桌面宠物两种视图共享同一个引擎。

## 技术栈

- **Python 3.11**（`pyproject.toml` 要求 `>=3.11`）
- **PySide6** — 桌面宠物窗口（无边框半透明置顶）
- **pystray** — 系统托盘
- **winotify** — Windows 通知中心 toast
- **Pillow** — 图像处理（fallback sprite + AI 资源抠图）
- **mmx**（mmx CLI）— AI 立绘生成，需联网
- **pytest** — 测试，74 个 case 全过

## 项目结构

```
D:\vscodepro\ccmon\
├── pyproject.toml
├── README.md
├── CLAUDE.md                   ← 你正在读的这个
├── ccmon\
│   ├── models.py               # State 枚举 + Session dataclass
│   ├── registry.py             # 读 ~/.claude/sessions/<pid>.json
│   ├── transcript.py           # 反向读 transcript 拿"当前在干什么"
│   ├── state_machine.py        # 状态机 (pure)
│   ├── engine.py               # 1.5s 轮询 scanner + Tick 事件
│   ├── paths.py                # %LOCALAPPDATA%\ccmon + ~/.claude 路径
│   ├── ide_lock.py             # 读 ~/.claude/ide/*.lock 找 VS Code 窗口
│   ├── win\
│   │   ├── activate.py         # pid → 窗口 → 前台（核心跳转逻辑）
│   │   └── autostart.py        # HKCU\...\Run 注册
│   ├── notify\
│   │   ├── toast.py            # winotify 通知 + "跳转到窗口" 按钮
│   │   ├── sound.py            # winsound 系统声音
│   │   ├── webhook.py          # 通用 webhook (Bark/Telegram/Discord/企微)
│   │   └── dedupe.py           # Notifier 编排 + 60s 升级 + 冷却
│   ├── ui\
│   │   ├── app.py              # pystray 托盘入口
│   │   ├── tray.py             # Pillow 渲染托盘图标
│   │   └── pet\
│   │       ├── window.py       # PySide6 宠物窗口
│   │       ├── fallback_sprite.py  # Pillow 实时绘制 dalmatian
│   │       ├── sprite_loader.py    # 多风格 loader (项目 + 用户)
│   │       ├── run.py              # 宠物 entry (含 signal handler)
│   │       └── assets\             # 空 — 真正的 pet 风格在 D:\vscodepro\ccmon\assets\pet\
│   └── __main__.py             # ccmon / ccmon ps|tray|pet|both
├── assets\pet\                 # AI 生成的宠物立绘（项目随版本控制）
│   ├── .gitkeep
│   ├── peter\                  # male dalmatian (default)
│   └── peter2\                 # cute chibi (备选)
├── scripts\
│   ├── gen_pet_sprites.sh      # mmx 生成新风格 + 抠图
│   ├── remove_white_bg.py      # 通用抠图（chroma key + outline flood 兜底）
│   ├── install.py / uninstall.py  # autostart 注册/注销
│   └── install.py
├── tests\                      # 74 个 case
└── docs\                       # 暂空
```

## 核心架构原则

### 1. 状态来自 `~/.claude/sessions/<pid>.json`，**零 hook**

Claude Code 自己就在实时维护会话注册表，ccmon 只读不写。装 ccmon 之前就开着的会话照样能监控，ccmon 崩了重启后状态自动恢复。

### 2. `pid` 是稳定主键

`session_id` 在 `/resume` 时会被原地改写（见 `concurrentSessions.ts:101-103`）。DB 用 `(pid, started_at)` 联合主键。

### 3. 子 agent 天然不出现

`registerSession()` 开头 `if (getAgentId() != null) return false`，所以注册表里只有顶层会话。无需过滤。

### 4. 状态机：raw → enum

| Claude Code 写 | `State` | 含义 |
|---|---|---|
| `status="busy"` | `RUNNING` | 正在干活 |
| `status="waiting"` + `waitingFor="approve <Tool>"` | `NEEDS_APPROVAL` | ★ 卡在权限确认 |
| `status="waiting"` + `waitingFor="input needed"` | `NEEDS_INPUT` | 等你说话 |
| `status="waiting"` + `waitingFor="dialog open"` | `DIALOG` | 弹窗打开 |
| `status="idle"` | `IDLE` | 空闲 |
| 文件在，进程没了 | `CRASHED` | 崩溃/被杀 |
| 文件消失 | `EXITED` | 正常退出 |

`status` 缺失时（`BG_SESSIONS` 特性关）= `UNKNOWN`，**绝不能当 IDLE**。

## 宠物资源管理

### 查找顺序

`sprite_loader._asset_dirs_for(style)` 返回 `[project_dir, user_dir]`：
1. `<project>/assets/pet/<style>/` — git 跟踪，clone 下来就有
2. `<%LOCALAPPDATA%>/ccmon/assets/pet/<style>/` — 用户本地覆盖

用户可以丢同名文件到 user 目录来覆盖项目的版本。

### 风格命名规范

每个风格一个子目录，里面 5 张 `<state>_alpha.png`（状态名：`happy`/`anxious`/`sad`/`sleepy`/`alert`）。

```
assets/pet/peter/
├── happy.png        # 原图 (mmx 输出)
├── happy_alpha.png  # 抠图后 (实际加载)
├── anxious.png / anxious_alpha.png
├── sad.png     / sad_alpha.png
├── sleepy.png  / sleepy_alpha.png
└── alert.png   / alert_alpha.png
```

加新风格：`bash scripts/gen_pet_sprites.sh <name> "<描述>"` 然后在宠物右键菜单选中。

### 当前风格

- **peter** — male dalmatian，stocky build、thick neck、broad chest
- **peter2** — cute chibi，原版（用户保留旧版作为对照）
- `_builtin` — Pillow 实时绘制的 dalmatian，有呼吸/眨眼/摇尾动画，CPU 接近 0

## AI 立绘生成流程

1. `bash scripts/gen_pet_sprites.sh <style> "[描述]"` 调用 mmx
2. prompt 含 "plain solid bright neon green background (#00FF00 chroma key green screen)" —— **绿幕比白幕好抠**
3. 5 个状态各生成 1 张 512×512 参考图，subject-ref 锁角色
4. `scripts/remove_white_bg.py` 抠图：自动检测边框颜色，**绿幕→chroma key，白底→outline flood**
5. mmx 右下角有 logo 戳 —— 抠图前先裁掉 60×60 像素

```bash
bash scripts/gen_pet_sprites.sh peter "Strong male dalmatian, stocky muscular build, thick neck"
```

## 用户偏好

- **UI 文字用中文**，代码注释用英文（参考 `dingyue` 风格）
- **托盘/宠物仅 Windows**（Linux/macOS 暂不支持）；**CLI 跨平台**（`ccmon ps` + webhook 在 Ubuntu/Debian 上可跑，零 GUI 依赖）
- **宠物 peter 命名**：用户自己起的名字，不要改
- **不在 toast 按钮上下太多功夫**（winotify pipe 回调在 Windows 上不稳定），优先 hover 气泡 + 宠物本体交互
- 资源放工程目录（git 跟踪），不放 `%LOCALAPPDATA%`

## 重要 bug 教训

### QPainter try/finally

`paintEvent` 内 `painter = QPainter(self)` 后必须保证 `painter.end()` 一定调用。`if/else` 里的早 return 路径、异常路径（QImage 损坏、字体找不到 glyph 等）都会让 painter 残留。Qt 每次后续 paintEvent 都会日志报 `QBackingStore::endPaint() called with active painter`。修法：

```python
painter = QPainter(self)
try:
    # ... all draw calls
finally:
    painter.end()
```

### 新加 Qt 类必须补 import

在 `paintEvent` 加新的 Qt 绘制调用（`QColor` / `QFont` / `QEasingCurve` / `QPen` 等）时，**必须**在 `from PySide6.QtGui import (...)` 或 `from PySide6.QtCore import (...)` 列表里加上。否则 `NameError` 在 paintEvent 静默抛出，被 try/finally 吞掉，绘制失败但没明显错误（只有 stderr 的 `Error calling Python override of QWidget::paintEvent()` 提示）。

加新功能时的检查清单：grep 文件里所有 `Q[A-Z]\w+` 类的使用，确认每个都在 import 列表里。

### winotify pipe 冲突

Notifier 的 Listener 绑到固定命名 pipe，旧进程残留会冲突。**用 per-pid AUMID**：

```python
aumid = f"ccmon.callbacks.{os.getpid()}"
registry = Registry(app_id=aumid, force_override=True)
```

### pystray Windows 后端

`pystray.Menu(callable)` 在 Windows 上 `__bool__` 检测失败，菜单会被认为空：

```python
# ✗ 错误
tray_icon = pystray.Icon(..., menu=pystray.Menu(lambda i: _menu(i, ...)))

# ✓ 正确
tray_icon = pystray.Icon(..., menu=pystray.Menu(*_menu(...)))
```

### Ctrl+C 退出

Qt 在 Windows 下拦截 SIGINT，Python signal handler 不会触发。**用 Win32 SetConsoleCtrlHandler + os._exit**：

```python
@ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
def _win_console_handler(ctrl_type):
    os._exit(0)
    return True
ctypes.windll.kernel32.SetConsoleCtrlHandler(_win_console_handler, True)
```

`os._exit` 是 C-level 立即终止，绕过所有 daemon 线程。

### `_run_pet` 漏调 `engine.start()`

只 `engine = Engine()` 不 start scanner，pet 收不到 Tick。**显式调**：

```python
engine = Engine(interval=1.5)
engine.start()  # ← 不能漏
return run_pet(engine)
```

## 宠物互动

宠物对用户的操作有反应，按"用户能感知的现象"列出：

| 互动 | 触发 | 现象 |
|---|---|---|
| Walk-around | 鼠标停 5s | 猫走过去看，回到原位停 3s |
| 单击跳 | 左键单击 | 跳到最紧急的 session 窗口 |
| 双击菜单 | 左键双击 | 打开菜单（切换形象、隐藏） |
| 摸头 | 左键长按 500ms+ | ❤️ 浮在猫头上，松开消失 |
| 激光笔 | 鼠标快速移动（>800 px/s） | 红点跟随鼠标，停下 1s 消失 |
| 睡眠 | 5 分钟不动 | 猫变 70% 透明 + Zzz |
| 自动避让 | 前景窗口覆盖 >50% | 猫跳到屏幕对角（300ms ease-out-cubic） |
| 跟 active window | Alt+Tab / 点其他 app | 猫朝新窗口漂 30%（subtle） |
| 数字键跳 | 1-9 键 | 跳到 priority 第 N 个 session |
| Esc | Esc 键 | 关气泡 |

状态对应的颜色（托盘图标 + 宠物色调）：
- 红色感叹号：等待授权（你最该看的）
- 橙：进程异常退出
- 黄问号：等待输入 / 弹窗
- 蓝：运行中
- 灰：空闲

完整用户使用说明见 README.md"## 桌面宠物 → ### 互动"。

## 常用命令

```bash
# 安装
uv venv --python 3.11 .venv
uv pip install -e ".[dev,tray,pet]"

# 跑
./.venv/Scripts/python.exe -m ccmon ps              # 命令行
./.venv/Scripts/python.exe -m ccmon both           # 托盘 + 宠物

# 测试
./.venv/Scripts/python.exe -m pytest -q             # 74 个 case

# 自启
./.venv/Scripts/python.exe -m scripts.install      # 写入 HKCU\...\Run
./.venv/Scripts/python.exe -m scripts.uninstall

# AI 立绘
bash scripts/gen_pet_sprites.sh <style> "[描述]"

# 调试：测跳转
./.venv/Scripts/python.exe -c "
from ccmon.win.activate import jump_to_session
jump_to_session(28860, r'D:\ComfyUI')
"
```

## Git

推送到 gitee：`git@gitee.com:fan-linya/ccmon.git`

```bash
git add -A && git commit -m "..." && git push
```

用户配置：fly65 / fly1111。
