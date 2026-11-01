# UnlockGo

Windows 文件占用检查与解除工具。将文件或文件夹拖入窗口，即可查看哪些程序正在使用它，并按需释放句柄、结束进程或处理文件。

程序完全在本机运行，不上传文件、进程信息或其他数据。

## 下载与运行

- 下载项目根目录中的 [UnlockGo.exe](UnlockGo.exe)。
- 双击运行，无需安装 Python 或其他运行环境。
- 这是便携版程序，可以复制到其他目录使用。
- 首次启动可能需要几秒，请耐心等待。

建议使用 Windows 10/11 64 位系统。部分系统进程需要管理员权限才能查看或处理。

## 主要功能

- 查找正在占用指定文件或文件夹的程序。
- 结束单个进程，或结束全部关联进程。
- 不结束程序，直接释放指定的文件句柄。
- 重命名、移动、复制文件或文件夹，并删除到回收站。
- 支持同时添加多个目标，以及扫描文件夹和子文件夹。
- 支持中文、英文界面切换，语言设置会自动保存。
- 所有操作均在本机完成，不联网上传数据。

## 使用方法

1. 双击 `UnlockGo.exe`。
2. 将文件或文件夹拖入窗口，或点击“添加文件/添加文件夹”。
3. 点击“开始扫描”。
4. 查看扫描结果，选择需要处理的程序或文件操作。

扫描文件夹时，默认只检查当前层级。如需检查子文件夹，请勾选“包含子文件夹”后重新扫描。

> “释放文件句柄”属于高级操作，可能影响正在运行的程序。操作前请先保存工作。

## 常见问题

### 扫描不到占用程序

部分系统进程或高权限进程需要管理员权限。可以点击“以管理员身份重启”后重新扫描。

### 结束进程安全吗？

不安全。结束进程可能导致未保存的数据丢失，请只处理你确认认识的程序。

### 为什么 Windows 提示“未知发布者”？

当前 EXE 尚未进行代码签名，Windows 可能显示安全提示。请只运行来自可信来源的程序。

### 为什么有些文件仍然无法解锁？

部分文件由系统、驱动程序、杀毒软件或其他用户会话占用，普通用户态程序无法强制解除。

## 开发与测试

从源码运行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

运行测试：

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m compileall app main.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 打包 EXE

安装构建依赖并执行打包脚本：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\scripts\build.ps1 -SkipInstall
```

构建产物位于 `dist\UnlockGo.exe`。如需发布新版本，可将该文件复制到项目根目录并替换 `UnlockGo.exe`。

## 更多文档

- [项目结构](docs/PROJECT_STRUCTURE.md)
- [技术实现说明](docs/TECHNICAL_DETAILS.md)
- [开源许可证](LICENSE)