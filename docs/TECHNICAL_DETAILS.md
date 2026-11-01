# UnlockGo 技术实现说明

本文介绍文件占用检测和句柄释放的实现方式。普通用户请返回 [README](../README.md) 查看使用说明。

## 文件占用检测

UnlockGo 以只读方式枚举 Windows 系统中的 File 句柄：

1. 通过 `NtQuerySystemInformation(SystemExtendedHandleInformation)` 读取系统句柄表。
2. 动态识别当前系统的 File 对象类型，避免依赖固定的类型编号。
3. 使用 `OpenProcess(PROCESS_DUP_HANDLE)` 和 `DuplicateHandle` 复制候选句柄。
4. 使用 `GetFinalPathNameByHandleW` 将句柄解析为目标路径。
5. 路径解析完成后立即关闭复制出的句柄，不会关闭原进程持有的 HANDLE。

程序还会合并 Windows Restart Manager 和 `psutil` 的查找结果。同一目标和 PID 的重复结果会被合并。

## 目标锁定状态

找到候选路径后，程序使用 `CreateFileW(DELETE | FILE_READ_ATTRIBUTES)` 做一次非破坏性探测：

- `存在锁定`：Windows 无法授予 `DELETE` 访问权限。
- `未发现锁定`：当前探测未发现访问锁。
- `无法确认`：权限、路径或其他 Windows 错误导致无法可靠判断。

该状态针对目标路径，而不是针对单个进程。列表中的候选进程不等于锁定的唯一来源，最终操作结果仍以 Windows 返回的错误为准。

## 释放文件句柄

当结果中包含可读取的 File HANDLE 和 File ID 时，可以尝试释放单个源句柄，而不结束所属进程。

释放前会依次校验：

1. 扫描时的卷序列号和 File ID 与当前目标路径一致。
2. 当前进程、HANDLE、路径和 File ID 与扫描结果一致。
3. 进程名称、PID 和启动时间未被替换。
4. 复制句柄后再次确认文件身份。
5. 使用 `DuplicateHandle(DUPLICATE_CLOSE_SOURCE)` 关闭源句柄。
6. 再次执行锁定探测，确认目标是否真正解锁。

## 已知限制

- 受保护进程、系统关键进程、内核驱动和内核级锁可能无法处理。
- 其他用户会话中的占用可能无法完整检测。
- 关闭正在运行程序的句柄可能导致该程序报错、重新占用文件或崩溃。
- 用户态不存在“仅在句柄仍指向该文件时原子关闭它”的 API，因此无法完全消除极端时间窗口内的竞争。
- UnlockGo 自身和 Windows 关键进程始终禁止结束或释放句柄。