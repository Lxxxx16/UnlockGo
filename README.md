# UnlockGo

## 项目结构

- `app/`：应用源代码，运行时资源统一放在 `app/assets/`。
- `tests/`：自动化测试。
- `design/`：原始设计素材，不参与程序打包。
- `docs/`：项目结构说明、国际化审计资料等。
- `scripts/`：构建和发布脚本。
- `main.py`：程序入口。
- `UnlockGo.exe`：当前可直接运行的 Windows 便携版程序，无需安装。
- `requirements.txt`：运行时依赖。
- `requirements-build.txt`：打包工具依赖。

完整目录说明见 [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)。

## File handle scanning

UnlockGo currently performs a user-mode, read-only Windows File handle scan:

1. `NtQuerySystemInformation(SystemExtendedHandleInformation=64)` reads the
   system handle table.
2. A temporary File probe determines the current `ObjectTypeIndex` instead of
   hard-coding an index that can differ between Windows versions.
3. `OpenProcess(PROCESS_DUP_HANDLE)` and `DuplicateHandle` copy candidate File
   handles into UnlockGo.
4. `GetFinalPathNameByHandleW` resolves each copied handle to its file or
   folder path.
5. The copied handles are closed after path resolution. The scan itself never
   closes a source process's original HANDLE.

Each matched path then receives one non-destructive lock probe. UnlockGo calls
`CreateFileW` with `DELETE | FILE_READ_ATTRIBUTES` while sharing read, write,
and delete access. It closes the temporary handle immediately and never changes
the file disposition. The target-level result is one of:

- `存在锁定`: Windows cannot grant the requested `DELETE` access, which may
  indicate a sharing lock, byte-range lock, or mapped-file conflict.
- `未发现锁定`: UnlockGo can acquire `DELETE` access; this probe found no current
  access lock.
- `无法确认`: permissions, a missing path, or another Windows error prevents a
  reliable answer.

The probe is target-level rather than per-process. When several processes have
handles to the same path, they share the same lock state because user mode
cannot safely test an individual process handle without closing it.

A process being listed does not prove that it is the lock owner. The
`候选来源` column records only how the candidate was discovered: the system
File handle table, Restart Manager, or the fallback `psutil` resource scan.
Multiple discovery channels for the same target and PID are merged into one row.
The `目标状态` column is a property of the target path, not of that process.
The `DELETE`-access probe is a general access-restriction signal, not a complete
preflight for every rename, move, or delete operation; Windows still returns the
authoritative error when the final operation runs.

When the raw handle scan succeeds, the slow full-process `psutil` resource pass
is skipped. Process details are loaded only for PIDs matched by a target File
handle, and Restart Manager results are still merged for file targets. If raw
handle enumeration fails, UnlockGo falls back to the legacy process-driven
`memory_maps()` and `open_files()` scan. Results are deduplicated by target path
and PID, while every original File HANDLE value remains available in the result
model and the table tooltip. Only `存在锁定` entries receive process termination
actions; `未发现锁定` and `无法确认` entries remain visible but cannot be ended
from the result table.

## Controlled source handle release

The result table also exposes an explicit `释放文件句柄` action for rows with a
visible File HANDLE and a readable Windows File ID. It closes one selected
source handle without terminating the owning process:

1. Require the scan-time volume serial number and File ID to match the current
   target path.
2. Re-enumerate the process's current File handles and require the same PID,
   HANDLE value, normalized target path, volume serial number, and File ID.
3. Verify the process name and start time before opening it with
   `PROCESS_DUP_HANDLE`.
4. Verify the opened process's creation time again with `GetProcessTimes` to
   reduce PID-reuse risk.
5. Preflight a non-closing duplicate of the source handle and compare its File
   identity, then call `DuplicateHandle` with `DUPLICATE_CLOSE_SOURCE` and
   verify the duplicated identity before closing the temporary duplicate.
6. Run the `DELETE`-access probe again so the UI reports whether the target is
   actually unlocked rather than assuming success.

This remains a best-effort user-mode operation. Windows has no atomic
"close this foreign handle only if it still names this file" primitive, so a
driver is still required for strict synchronization, protected processes, and
kernel-level locks. Closing a live application's handle can also make that
application fail, reopen the file, or crash. UnlockGo itself and critical
Windows processes are always rejected; high-risk processes require a separate
confirmation.

On the fast path, a memory-mapped file without a File handle and without a
Restart Manager registration is a known detection limitation. This tradeoff
keeps the common scan near the cost of reading the system handle table.

UnlockGo 是一个面向 Windows 的本地文件占用检查与解除工具。它把“查找是谁占用了文件”和“处理占用进程”收拢到一个简洁界面中，减少在任务管理器、资源监视器和命令行之间来回切换。

## 当前功能

- 拖入或选择文件、文件夹，支持一次添加多个目标。
- 新添加的目标先进入待扫描列表，可继续追加，确认后点击“开始扫描”。
- 支持文件夹扫描，并可选择是否包含子文件夹。
- 通过系统 File 句柄表从目标路径反向定位候选进程，并合并 Restart Manager 结果；显示进程名、PID、句柄、候选来源、目标状态和程序路径。
- 候选来源用于说明进程从哪里被发现，不代表该进程一定是锁定者；同一目标、同一 PID 的多来源结果会合并展示。
- 对每个命中路径执行一次 `CreateFileW(DELETE)` 非破坏性锁定探测，区分“存在锁定”“未发现锁定”和“无法确认”。该状态针对目标路径，不是单个进程的锁定结论。
- 仅对“存在锁定”的候选进程提供结束操作，支持结束单个进程、结束全部关联进程，以及结束进程后将目标移入 Windows 回收站；最终重命名、移动或删除结果以 Windows 返回的错误为准。
- 对扫描到可见 File 句柄的候选，可在“更多操作”中选择“释放文件句柄”，仅关闭一个已复验的源句柄而不结束进程；成功后自动重扫并显示目标是否真正解锁。
- 支持对文件或文件夹执行重命名、移动、复制和删除到回收站。
- “目标列表”显示完整路径；最近一次移动可撤销，移动错位置时可直接删除到回收站。
- 提供扫描取消、重新扫描和清空目标等操作。
- 对 UnlockGo 自身、Windows 关键进程和高风险进程进行保护或二次确认。
- 普通权限启动；遇到系统进程不可见时，可点击右下角“以管理员身份重启”，当前目标列表会保留。
- 支持中文/英文界面切换，语言选择会自动保存。
- 完全本地运行，无广告，不上传文件与进程信息。

## 使用方式

当前发布程序是项目根目录下的 `UnlockGo.exe`。它无需安装 Python 或其他运行时文件，可直接双击运行，也可以复制到其他目录使用。源码开发和打包流程见下文。

1. 将文件或文件夹拖入窗口，或者点击“添加文件/添加文件夹”。
2. 可以继续拖入更多目标；重复目标会自动去重。
3. 确认目标数量后，点击“开始扫描”。
4. 在结果表中选择需要处理的进程，或使用“结束全部关联进程”。
5. 需要重新选择时，点击“清空”；扫描完成后可点击“重新扫描”。

扫描文件夹时，默认只检查当前层级。勾选“包含子文件夹”后重新扫描，才会检查整个目录树。

## 开发与测试

```powershell
.\.venv\Scripts\python.exe main.py
```

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m compileall app main.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 技术方案

- 界面：PySide6 / Qt
- 进程信息与结束进程：psutil
- 占用查询：Windows 系统 File 句柄表反向定位，配合 `CreateFileW(DELETE)` 锁定探测与 Restart Manager 结果
- 后台任务：QThread，避免扫描阻塞界面
- 发布：PyInstaller 单文件 EXE

## 已知限制

- 内核驱动、系统关键进程或权限更高的进程可能无法完整检查，建议以管理员身份运行；权限不足本身不会再阻止操作，已确认的占用仍会拦截，最终共享冲突由 Windows 校验。
- 锁定探测是目标级判断：同一路径的多个候选进程共享一个状态，用户态无法在不关闭原始句柄的情况下逐一验证某个进程是否就是阻塞者。
- Restart Manager 不能覆盖所有独占访问和驱动级占用，结果会结合系统句柄表和其他检查方式补充。
- 用户态句柄释放仍属于尽力而为操作：无法可靠覆盖受保护进程、杀毒软件自我保护、内核锁或其他用户会话中的占用；关闭正在运行程序的句柄也可能导致该程序报错或重新占用文件。
- 只有扫描时、当前目标路径和待关闭句柄的卷序列号与 File ID 完全一致时才会释放；无法读取 File ID 的行会禁用该操作。
- Windows 没有“只在句柄仍指向此文件时原子关闭它”的用户态 API，因此释放前会预检复制并多次校验 PID、启动时间、句柄值、路径和 File ID，但仍不能完全消除极短时间窗口内的竞争。
- 高风险进程在结束或释放句柄前会再次确认；Windows 关键进程和 UnlockGo 自身不允许执行这两类操作。

## 后续计划

1. 增强管理员权限下的占用检测与结果解释。
2. 完善打包、版本信息和 GitHub 发布流程。

## 应用图标

窗口标题栏、任务栏和界面品牌位使用 `app/assets/unlockgo.png`，Windows 可执行文件图标使用 `app/assets/unlockgo.ico`。`design/lock.png` 保留为原始设计素材，不会被构建过程修改。

## 打包 Windows EXE

首次打包时安装构建依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

然后执行一键构建脚本：

```powershell
.\scripts\build.ps1 -SkipInstall
```

如果 PowerShell 执行策略阻止脚本运行，可使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1 -SkipInstall
```

脚本生成的构建产物位于 `dist\UnlockGo.exe`，而不是自动覆盖根目录下的当前发布文件。脚本会自动使用 `app/assets/unlockgo.ico`，并将 `app/assets` 打入单文件 EXE。

构建成功并完成运行验证后，如需将新版本作为当前便携版发布，可手动将 `dist\UnlockGo.exe` 复制或移动为项目根目录下的 `UnlockGo.exe`。
