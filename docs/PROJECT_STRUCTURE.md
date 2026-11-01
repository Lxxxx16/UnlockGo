# UnlockGo 项目结构

## 目录总览

```text
UnlockGo/
├─ main.py                     # 程序入口，只负责启动应用
├─ UnlockGo.exe                # 当前便携版发布程序（无需安装）
├─ requirements.txt            # 运行时依赖
├─ requirements-build.txt      # PyInstaller 等构建依赖
├─ README.md                   # 使用、开发和打包说明
├─ app/                        # 应用源代码
│  ├─ application.py           # QApplication 初始化、启动参数处理
│  ├─ main_window.py           # 主窗口和界面交互
│  ├─ widgets.py               # 自定义界面组件
│  ├─ theme.py                 # Qt 样式表
│  ├─ icon.py                  # 应用图标加载
│  ├─ i18n.py                  # 中英文翻译和语言持久化
│  ├─ models.py                # 数据模型
│  ├─ scanner.py               # 目标扫描
│  ├─ workers.py               # 后台线程任务
│  ├─ file_operations.py       # 文件操作底层逻辑
│  ├─ file_actions.py          # 文件业务动作
│  ├─ process_actions.py       # 进程业务动作
│  ├─ platform_utils.py        # 平台和权限工具
│  ├─ windows_handles.py       # Windows 句柄枚举
│  ├─ windows_handle_release.py# 受控释放文件句柄
│  ├─ windows_lock_probe.py    # 文件锁定探测
│  ├─ windows_restart_manager.py # Restart Manager 集成
│  └─ assets/                  # 必须随程序打包的运行时资源
│     ├─ check.svg
│     ├─ unlockgo.png
│     └─ unlockgo.ico
├─ tests/                      # 自动化测试，保持与 app 模块对应
├─ scripts/                    # 构建和发布脚本
│  └─ build.ps1                # 一键生成 dist/UnlockGo.exe
├─ design/                     # 原始设计素材，不参与运行时打包
│  └─ lock.png
├─ docs/                       # 项目文档和审计资料
│  ├─ PROJECT_STRUCTURE.md
│  └─ i18n/
│     ├─ chinese_strings.txt
│     └─ i18n_entries.txt
├─ .gitignore                  # Git 忽略规则
├─ .idea/                      # PyCharm 本地配置，不属于业务代码
├─ .venv/                      # 本地 Python 虚拟环境
└─ __pycache__/                # Python 自动生成缓存
```

## 文件放置规则

1. 根目录只保留入口、依赖、README、当前便携版发布程序和 Git 配置，不再堆放素材或临时分析文件。
2. 业务代码统一放在 `app/`，测试统一放在 `tests/`。
3. `app/assets/` 只放运行时真正需要加载的文件；移动该目录中的资源时必须同步修改打包命令和资源路径测试。
4. 原始图标、设计稿和未处理素材放在 `design/`，不要直接覆盖运行时图标。
5. 国际化提取结果、审计快照等项目资料放在 `docs/i18n/`。
6. 新增测试命名为 `tests/test_<模块名>.py`，并优先覆盖对应的 `app/` 模块。
7. `.venv/`、`.idea/`、`__pycache__/`、`build/`、`dist/` 和日志文件属于本地或生成内容，不纳入发布资源。
8. 构建命令统一放入 `scripts/`，打包依赖放入 `requirements-build.txt`。

## 重新打包

```powershell
.\scripts\build.ps1 -SkipInstall
```

脚本产物输出到 `dist/UnlockGo.exe`。构建成功并完成运行验证后，如需发布新版本，可手动将产物复制或移动为根目录下的 `UnlockGo.exe`；当前可直接运行的版本位于项目根目录。

## 图标文件关系

- `design/lock.png`：用户提供的原始设计图，仅作素材保留。
- `app/assets/unlockgo.png`：透明背景、正方形运行时图标。
- `app/assets/unlockgo.ico`：Windows 可执行文件图标，包含多个尺寸。

重新生成图标时，以 `design/lock.png` 为输入，输出到 `app/assets/`，并运行完整测试确认图标仍可加载。