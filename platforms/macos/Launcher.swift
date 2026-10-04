// SPDX-License-Identifier: MIT
// This companion supervises its direct manager child, never the user's Codex.
import AppKit
import Darwin
import Foundation

enum LauncherFailure: Equatable {
    case pythonMissing, alreadyRunning, resourcesMissing, launchFailed
    case cleanupUnverified, managerFailed

    var title: String {
        switch self {
        case .pythonMissing: return "需要 Python 3.12 或更新版本"
        case .alreadyRunning: return "另一个额度条管理器正在运行"
        case .resourcesMissing: return "启动器文件不完整"
        case .launchFailed: return "未能启动额度条"
        case .cleanupUnverified: return "额度条清理尚未确认"
        case .managerFailed: return "额度条未能继续运行"
        }
    }

    var detail: String {
        switch self {
        case .pythonMissing:
            return "请安装 Python 3.12 或更新版本，再从菜单选择“启动额度条”。本启动器没有安装或更改解释器。"
        case .alreadyRunning:
            return "如果旧版正在终端运行，请在那个终端按 Ctrl-C，等待它结束后，再从这里选择“启动额度条”。不需要退出 Codex。本启动器不会停止其他管理器。"
        case .resourcesMissing:
            return "请重新安装完整的 codex-usage-bar.app，再试一次。"
        case .launchFailed:
            return "启动器无法运行随应用安装的管理器。请重新安装完整的应用后重试。Codex 没有被强制退出。"
        case .cleanupUnverified:
            return "管理器已经结束，但无法确认额度条已清理。请保存工作后正常退出 Codex，再通过本应用启动。没有强制退出 Codex。"
        case .managerFailed:
            return "没有确认额度条正在显示。可从菜单重新启动；若仍失败，请保存工作后正常退出 Codex，再通过本应用启动。没有强制退出 Codex。"
        }
    }
}

enum ManagerMessage: Equatable {
    case waitingForCodex, attaching, waitingForComposer
    case visible(Int, Int), detached, codexExited, cancelled
    case failure(LauncherFailure)

    // Only recognized, non-private manager messages enter the UI. Raw output is
    // neither displayed nor written to disk. Unknown lines are discarded.
    static func parse(_ line: String) -> ManagerMessage? {
        if line == "Python 3.12 or later is required. No interpreter has been installed or changed." {
            return .failure(.pythonMissing)
        }
        if line.hasPrefix("未完成：") {
            if line.hasPrefix("未完成：another_manager_is_running。") {
                return .failure(.alreadyRunning)
            }
            if line.hasPrefix("未完成：daily_renderer_cleanup_unverified。") {
                return .failure(.cleanupUnverified)
            }
            return .failure(.managerFailed)
        }
        if line == "信息栏清理未完全确认；未强制结束日常 Codex。请正常退出 Codex 后重新启动信息栏。" {
            return .failure(.cleanupUnverified)
        }
        if line == "日常 Codex 正在运行。请先保存工作并用 Cmd-Q 正常退出；本启动器会等待，然后使用原账号、历史和项目重新打开。不会强制结束任务。" {
            return .waitingForCodex
        }
        if line == "日常额度条管理器已启动。会在兼容的首页和会话输入框上方显示；支持多窗口与页面重载。Ctrl-C 仅关闭信息栏，Cmd-Q 退出 Codex。" {
            return .attaching
        }
        if line == "等待兼容的输入框或窗口恢复；不读取对话或输入正文。" {
            return .waitingForComposer
        }
        if line == "信息栏管理器已断开；日常 Codex 继续运行。要关闭本地调试端口，请正常退出 Codex（Cmd-Q）。" {
            return .detached
        }
        if line == "日常 Codex 已退出，信息栏管理器结束。" { return .codexExited }
        if line == "已取消等待；日常 Codex 未改变。" { return .cancelled }
        let pattern = #"^额度条已在 ([0-9]{1,4}) 个窗口显示；([0-9]{1,4}) 个窗口等待恢复。$"#
        guard let expression = try? NSRegularExpression(pattern: pattern),
              let match = expression.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)),
              let visibleRange = Range(match.range(at: 1), in: line),
              let waitingRange = Range(match.range(at: 2), in: line),
              let visible = Int(line[visibleRange]), let waiting = Int(line[waitingRange]),
              visible > 0, visible <= 512, waiting <= 512 else { return nil }
        return .visible(visible, waiting)
    }
}

// Buffer bytes until a complete UTF-8 line arrives. Bound memory even if a
// dependency writes unexpected data without a newline.
struct ManagerOutput {
    private var bytes = Data()
    private var droppingLine = false

    mutating func accept(_ data: Data) -> [ManagerMessage] {
        var messages: [ManagerMessage] = []
        for byte in data {
            if byte == 10 {
                if !droppingLine {
                    let line = String(decoding: bytes, as: UTF8.self)
                        .trimmingCharacters(in: .newlines)
                    if let message = ManagerMessage.parse(line) { messages.append(message) }
                }
                bytes.removeAll(keepingCapacity: true)
                droppingLine = false
            } else if !droppingLine {
                if bytes.count >= 8192 {
                    bytes.removeAll(keepingCapacity: true)
                    droppingLine = true
                } else {
                    bytes.append(byte)
                }
            }
        }
        return messages
    }

    mutating func finish() -> [ManagerMessage] { accept(Data([10])) }
}

func availableManagerOutput(_ handle: FileHandle) -> Data {
    // read(upToCount:) can wait for its full count or EOF on a pipe. The
    // long-lived manager emits short status lines, so consume available bytes.
    handle.availableData
}

func signalManagerStop(_ process: Process) -> Bool {
    guard process.isRunning else { return true }
    return Darwin.kill(process.processIdentifier, SIGTERM) == 0 || errno == ESRCH
}

final class UsageBarLauncher: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private let statusLabel = NSMenuItem(title: "正在启动…", action: nil, keyEquivalent: "")
    private let detailLabel = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private var startItem: NSMenuItem!
    private var stopItem: NSMenuItem!
    private var showItem: NSMenuItem!
    private var child: Process?
    private var outputPipe: Pipe?
    private var output = ManagerOutput()
    private var outputEnded = false
    private var exitObserved = false
    private var stopRequested = false
    private var quitRequested = false
    private var failure: LauncherFailure?
    private var failureAlertShown = false
    private var waitingAlertShown = false
    private var stopTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "额度"
        statusItem.button?.setAccessibilityLabel("Codex 额度条")
        let menu = NSMenu()
        menu.autoenablesItems = false
        menu.delegate = self
        statusLabel.isEnabled = false
        detailLabel.isEnabled = false
        menu.addItem(statusLabel)
        menu.addItem(detailLabel)
        menu.addItem(.separator())
        showItem = addItem("显示 Codex", action: #selector(showCodex), to: menu)
        startItem = addItem("启动额度条", action: #selector(startManager), to: menu)
        stopItem = addItem("停止额度条", action: #selector(stopManager), to: menu)
        menu.addItem(.separator())
        addItem("退出额度条", action: #selector(quitLauncher), to: menu)
        statusItem.menu = menu
        startManager()
    }

    @discardableResult
    private func addItem(_ title: String, action: Selector, to menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        menu.addItem(item)
        return item
    }

    private func setStatus(_ title: String, detail: String) {
        statusLabel.title = title
        detailLabel.title = detail
        statusItem.button?.toolTip = title + "\n" + detail
        refreshActions()
    }

    private func refreshActions() {
        // Keep Start unavailable until termination and final pipe drainage finish.
        startItem?.isEnabled = child == nil && !quitRequested
        stopItem?.isEnabled = child?.isRunning == true && !stopRequested
        showItem?.isEnabled = runningCodex() != nil
    }

    func menuWillOpen(_ menu: NSMenu) { refreshActions() }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        // A repeated launch keeps the companion in the background.
        return false
    }

    private func bundledScript() -> URL? {
        guard let resources = Bundle.main.resourceURL else { return nil }
        let root = resources.appendingPathComponent("codex-usage-bar", isDirectory: true)
        let script = root.appendingPathComponent("platforms/macos/Start.command")
        guard script.standardizedFileURL == script.resolvingSymlinksInPath(),
              let values = try? script.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey]),
              values.isRegularFile == true, values.isSymbolicLink != true else { return nil }
        return script
    }

    @objc private func startManager() {
        guard child == nil, !quitRequested else { return }
        failure = nil
        failureAlertShown = false
        waitingAlertShown = false
        stopRequested = false
        outputEnded = false
        exitObserved = false
        output = ManagerOutput()
        guard let script = bundledScript() else {
            presentFailure(.resourcesMissing)
            return
        }
        let process = Process()
        let pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-f", script.path]
        process.currentDirectoryURL = script.deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let allowed = ["HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"]
        var environment: [String: String] = [:]
        for key in allowed { environment[key] = ProcessInfo.processInfo.environment[key] }
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        process.environment = environment
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = pipe
        process.standardError = pipe
        child = process
        outputPipe = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self, weak process] handle in
            let data = availableManagerOutput(handle)
            DispatchQueue.main.async { [weak self, weak process] in
                guard let self, let process, self.child === process else { return }
                if data.isEmpty {
                    self.outputEnded = true
                    handle.readabilityHandler = nil
                    self.consume(self.output.finish())
                    self.finishIfReady(process)
                } else {
                    self.consume(self.output.accept(data))
                }
            }
        }
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async { [weak self] in
                guard let self, self.child === process else { return }
                self.exitObserved = true
                self.finishIfReady(process)
                // A misbehaving descendant must not keep the menu stuck by
                // retaining stdout. Normal manager subprocesses use /dev/null.
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
                    guard let self, self.child === process, self.exitObserved else { return }
                    if !self.outputEnded {
                        self.outputEnded = true
                        self.failure = self.failure ?? .cleanupUnverified
                        self.consume(self.output.finish())
                        self.finishIfReady(process)
                    }
                }
            }
        }
        setStatus("正在启动额度条…", detail: "后台运行，不需要终端窗口")
        do {
            try process.run()
            // The child owns a duplicate. Closing ours allows reliable EOF.
            try? pipe.fileHandleForWriting.close()
            refreshActions()
        } catch {
            pipe.fileHandleForReading.readabilityHandler = nil
            process.terminationHandler = nil
            try? pipe.fileHandleForReading.close()
            try? pipe.fileHandleForWriting.close()
            outputPipe = nil
            child = nil
            presentFailure(.launchFailed)
        }
    }

    private func consume(_ messages: [ManagerMessage]) {
        for message in messages {
            if case let .failure(error) = message {
                failure = error
                setStatus(error.title, detail: "请查看提示后重试")
                continue
            }
            guard !stopRequested, failure == nil else { continue }
            switch message {
            case .waitingForCodex:
                setStatus("等待 Codex 正常退出", detail: "保存工作后按 ⌘Q；将自动重新打开")
                if !waitingAlertShown {
                    waitingAlertShown = true
                    showAlert(title: "需要正常重新打开 Codex", detail: "当前 Codex 尚未启用额度条连接。请先保存工作，再在 Codex 中按 ⌘Q 正常退出；本启动器会自动用原账号、历史和项目重新打开。不会替你结束运行任务。")
                }
            case .attaching:
                setStatus("正在连接额度条…", detail: "等待确认输入框上方显示")
            case .waitingForComposer:
                setStatus("等待兼容的输入框", detail: "后台仍在尝试连接，尚未确认显示")
            case let .visible(count, waiting):
                setStatus("额度条已显示：\(count) 个窗口", detail: waiting > 0 ? "另有 \(waiting) 个窗口等待恢复" : "后台刷新中，不需要终端窗口")
            case .detached:
                setStatus("正在结束额度条…", detail: "Codex 继续运行")
            case .codexExited:
                setStatus("Codex 已退出", detail: "正在结束额度条管理器")
            case .cancelled:
                setStatus("已取消等待", detail: "Codex 未改变")
            case .failure: break
            }
        }
    }

    private func finishIfReady(_ process: Process) {
        guard child === process, exitObserved, outputEnded else { return }
        stopTimer?.invalidate()
        stopTimer = nil
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        try? outputPipe?.fileHandleForReading.close()
        outputPipe = nil
        child = nil
        process.terminationHandler = nil
        if process.terminationReason == .uncaughtSignal {
            failure = failure ?? .cleanupUnverified
        } else if process.terminationStatus != 0 {
            failure = failure ?? .managerFailed
        }
        if let error = failure {
            if quitRequested {
                quitRequested = false
                NSApp.reply(toApplicationShouldTerminate: false)
            }
            presentFailure(error)
        } else {
            setStatus("额度条已停止", detail: "选择“启动额度条”可再次启用")
            if quitRequested { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        stopRequested = false
        refreshActions()
    }

    @objc private func stopManager() { requestStop() }

    private func requestStop() {
        guard let process = child else { return }
        setStatus("正在停止额度条…", detail: "等待管理器清理；Codex 不会被退出")
        if !stopRequested {
            // Darwin's Process.terminate() also signals the child's process
            // group, interrupting its in-flight ps/lsof checks. Signal only the
            // positive PID of our retained Process after its liveness check;
            // never use saved PIDs, negative PIDs or a process-group signal.
            if !signalManagerStop(process) {
                if quitRequested {
                    quitRequested = false
                }
                setStatus("暂未能停止额度条", detail: "管理器继续运行；可以再次选择停止")
                showAlert(title: "未能请求停止额度条", detail: "停止请求没有成功送达。管理器继续运行，可稍后再次选择“停止额度条”。没有强制退出 Codex。")
                return
            }
            stopRequested = true
        }
        refreshActions()
        stopTimer?.invalidate()
        stopTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: false) { [weak self, weak process] _ in
            guard let self, let process, self.child === process else { return }
            if self.quitRequested {
                self.quitRequested = false
                NSApp.reply(toApplicationShouldTerminate: false)
            }
            self.setStatus("仍在等待安全退出", detail: "未强制结束任何进程")
            self.showAlert(title: "额度条管理器仍在清理", detail: "管理器尚未确认退出，启动器将继续等待，不会强制结束 Codex。你可以继续使用 Codex，稍后再查看菜单状态。")
        }
    }

    @objc private func quitLauncher() { NSApp.terminate(nil) }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard child != nil else { return .terminateNow }
        quitRequested = true
        requestStop()
        return quitRequested ? .terminateLater : .terminateCancel
    }

    private func runningCodex() -> NSRunningApplication? {
        let expected = URL(fileURLWithPath: "/Applications/Codex.app").resolvingSymlinksInPath()
        return NSWorkspace.shared.runningApplications.first {
            !$0.isTerminated && $0.bundleURL?.resolvingSymlinksInPath() == expected
        }
    }

    @objc private func showCodex() {
        // Activate an existing original application only. Cold-starting it here
        // would omit the manager's debugging flags and undermine the launcher.
        guard let codex = runningCodex() else { return }
        codex.activate(options: [])
    }

    private func presentFailure(_ error: LauncherFailure) {
        failure = error
        setStatus(error.title, detail: "选择“启动额度条”可重试")
        guard !failureAlertShown else { return }
        failureAlertShown = true
        showAlert(title: error.title, detail: error.detail)
    }

    private func showAlert(title: String, detail: String) {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = detail
        alert.addButton(withTitle: "知道了")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }
}

#if LAUNCHER_TESTS
// Compile with -D LAUNCHER_TESTS for offline protocol/buffering checks. This
// branch never creates NSApplication, a manager or a Codex process.
var checks = 0
func check(_ condition: @autoclosure () -> Bool) {
    precondition(condition(), "Launcher protocol check failed")
    checks += 1
}
check(ManagerMessage.parse("额度条已在 2 个窗口显示；1 个窗口等待恢复。") == .visible(2, 1))
check(ManagerMessage.parse("额度条已在 0 个窗口显示；0 个窗口等待恢复。") == nil)
check(ManagerMessage.parse("额度条已在 9999 个窗口显示；0 个窗口等待恢复。") == nil)
check(ManagerMessage.parse("额度条已在 2 个窗口显示；9999 个窗口等待恢复。") == nil)
check(ManagerMessage.parse("额度条已在 2 个窗口显示；1 个窗口等待恢复。secret") == nil)
check(ManagerMessage.parse("未知输出，包括账号资料") == nil)
check(ManagerMessage.parse("未完成：another_manager_is_running。详情") == .failure(.alreadyRunning))
check(ManagerMessage.parse("未完成：daily_renderer_cleanup_unverified。详情") == .failure(.cleanupUnverified))
check(ManagerMessage.parse("未完成：unknown_private_data。") == .failure(.managerFailed))
check(ManagerMessage.parse("Python 3.12 or later is required. No interpreter has been installed or changed.") == .failure(.pythonMissing))
check(ManagerMessage.parse("等待兼容的输入框或窗口恢复；不读取对话或输入正文。") == .waitingForComposer)
check(ManagerMessage.parse("已取消等待；日常 Codex 未改变。") == .cancelled)
check(ManagerMessage.parse("日常 Codex 已退出，信息栏管理器结束。") == .codexExited)
var output = ManagerOutput()
let sample = Data("额度条已在 1 个窗口显示；0 个窗口等待恢复。\n".utf8)
check(output.accept(sample.prefix(7)).isEmpty)
check(output.accept(sample.dropFirst(7)) == [.visible(1, 0)])
check(output.accept(Data(repeating: 65, count: 9000)).isEmpty)
check(output.accept(sample).isEmpty)
check(output.accept(sample) == [.visible(1, 0)])
check(output.accept(Data("已取消等待；日常 Codex 未改变。".utf8)).isEmpty)
check(output.finish() == [.cancelled])
check(output.accept(Data("unknown\nunknown\n".utf8)).isEmpty)
// A short status must arrive while the write end remains open. Delayed EOF
// bounds failure time if a blocking full-count read is accidentally restored.
let statusPipe = Pipe()
let eofDeadlineReached = DispatchSemaphore(value: 0)
let shortStatus = Data("等待兼容的输入框或窗口恢复；不读取对话或输入正文。\n".utf8)
statusPipe.fileHandleForWriting.write(shortStatus)
DispatchQueue.global().asyncAfter(deadline: .now() + 1) {
    eofDeadlineReached.signal()
    try? statusPipe.fileHandleForWriting.close()
}
let available = availableManagerOutput(statusPipe.fileHandleForReading)
check(available == shortStatus)
check(eofDeadlineReached.wait(timeout: .now()) == .timedOut)
var liveOutput = ManagerOutput()
check(liveOutput.accept(available) == [.waitingForComposer])
try? statusPipe.fileHandleForReading.close()
try? statusPipe.fileHandleForWriting.close()
// A dummy manager catches SIGTERM while its same-group child stays alive until
// explicit cleanup. Foundation Process.terminate() fails this check on Darwin;
// our direct positive-PID signal must not interrupt descendant ps/lsof checks.
let signalFixture = #"""
import json, signal, subprocess, sys, time
grandchild = r'''
import signal, time
def stop(signum, frame):
    print('TERM', flush=True)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
print('READY', flush=True)
time.sleep(5)
'''
received = []
signal.signal(signal.SIGTERM, lambda signum, frame: received.append(signum))
child = subprocess.Popen([sys.executable, '-I', '-c', grandchild],
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
assert child.stdout.readline().strip() == 'READY'
print('READY', flush=True)
deadline = time.monotonic() + 3
while not received and time.monotonic() < deadline:
    time.sleep(.01)
time.sleep(.1)
grandchild_stopped = child.poll() is not None
if not grandchild_stopped:
    child.terminate()
child.communicate(timeout=2)
print(json.dumps({'parentTerminated': received == [signal.SIGTERM],
                 'grandchildStoppedBeforeCleanup': grandchild_stopped}), flush=True)
"""#
let fixture = Process()
let fixtureOutput = Pipe()
fixture.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
fixture.arguments = ["-I", "-c", signalFixture]
fixture.standardInput = FileHandle.nullDevice
fixture.standardOutput = fixtureOutput
fixture.standardError = FileHandle.nullDevice
try fixture.run()
check(fixtureOutput.fileHandleForReading.availableData == Data("READY\n".utf8))
check(signalManagerStop(fixture))
let fixtureData = fixtureOutput.fileHandleForReading.readDataToEndOfFile()
fixture.waitUntilExit()
check(fixture.terminationReason == .exit && fixture.terminationStatus == 0)
let fixtureResult = try JSONSerialization.jsonObject(with: fixtureData) as? [String: Bool]
check(fixtureResult?["parentTerminated"] == true)
check(fixtureResult?["grandchildStoppedBeforeCleanup"] == false)
print("Native launcher: \(checks) offline checks passed; no application launched.")
#else
let application = NSApplication.shared
let delegate = UsageBarLauncher()
application.delegate = delegate
application.run()
#endif
