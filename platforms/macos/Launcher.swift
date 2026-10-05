// SPDX-License-Identifier: MIT
// This companion supervises its direct manager child, never the user's Codex.
import AppKit
import Darwin
import Foundation

enum LauncherLocale: String, CaseIterable {
    case chinese = "zh-CN", english = "en"

    static func system(_ preferredLanguages: [String]) -> LauncherLocale {
        preferredLanguages.first?.lowercased().hasPrefix("zh") == true ? .chinese : .english
    }

    func text(_ chinese: String, _ english: String) -> String {
        self == .chinese ? chinese : english
    }
}

struct LauncherCopy: Equatable {
    let title: String
    let detail: String
}

enum LauncherFailure: Error, Equatable, CaseIterable {
    case pythonMissing, alreadyRunning, resourcesMissing, launchFailed
    case cleanupUnverified, managerFailed

    func copy(in locale: LauncherLocale) -> LauncherCopy {
        switch self {
        case .pythonMissing:
            return LauncherCopy(title: locale.text("需要 Python 3.12 或更新版本", "Python 3.12 or later is required"),
                detail: locale.text("请安装 Python 3.12 或更新版本，再从菜单选择“启动额度条”。本启动器没有安装或更改解释器。", "Install Python 3.12 or later, then choose Start Usage Bar from the menu. This launcher has not installed or changed your interpreter."))
        case .alreadyRunning:
            return LauncherCopy(title: locale.text("另一个额度条管理器正在运行", "Another usage bar manager is running"),
                detail: locale.text("如果旧版正在终端运行，请在那个终端按 Ctrl-C，等待它结束后，再从这里选择“启动额度条”。不需要退出 Codex。本启动器不会停止其他管理器。", "If an older version is running in a terminal, press Ctrl-C there and wait for it to finish, then choose Start Usage Bar here. You do not need to quit Codex. This launcher will not stop another manager."))
        case .resourcesMissing:
            return LauncherCopy(title: locale.text("启动器文件不完整", "Launcher files are incomplete"),
                detail: locale.text("请重新安装完整的 codex-usage-bar.app，再试一次。", "Reinstall the complete codex-usage-bar.app and try again."))
        case .launchFailed:
            return LauncherCopy(title: locale.text("未能启动额度条", "Could not start the usage bar"),
                detail: locale.text("启动器无法运行随应用安装的管理器。请重新安装完整的应用后重试。Codex 没有被强制退出。", "The launcher could not run its bundled manager. Reinstall the complete app and try again. Codex has not been forcibly closed."))
        case .cleanupUnverified:
            return LauncherCopy(title: locale.text("额度条清理尚未确认", "Usage bar cleanup is unconfirmed"),
                detail: locale.text("管理器已经结束，但无法确认额度条已清理。请保存工作后正常退出 Codex，再通过本应用启动。没有强制退出 Codex。", "The manager has exited, but removal of the usage bar could not be confirmed. Save your work, quit Codex normally, then open it through this app. Codex has not been forcibly closed."))
        case .managerFailed:
            return LauncherCopy(title: locale.text("额度条未能继续运行", "The usage bar could not continue"),
                detail: locale.text("没有确认额度条正在显示。可从菜单重新启动；若仍失败，请保存工作后正常退出 Codex，再通过本应用启动。没有强制退出 Codex。", "The usage bar is not confirmed visible. Try starting it again from the menu. If that fails, save your work, quit Codex normally, then open it through this app. Codex has not been forcibly closed."))
        }
    }
}

enum LauncherStatus: Equatable {
    case starting, waitingForCodex, attaching, waitingForComposer
    case visible(Int, Int), detached, codexExited, cancelled, stopped
    case stopping, stopFailed, waitingForCleanup
    case residentWaiting, needsLauncher, quotaRetrying, recovering(Int, LauncherFailure?)
    case failure(LauncherFailure, retry: Bool)

    func copy(in locale: LauncherLocale) -> LauncherCopy {
        switch self {
        case .starting:
            return LauncherCopy(title: locale.text("正在启动额度条…", "Starting usage bar…"),
                detail: locale.text("后台运行，不需要终端窗口", "Runs in the background; no terminal needed"))
        case .residentWaiting:
            return LauncherCopy(title: locale.text("常驻运行，等待 Codex…", "Resident mode: waiting for Codex…"),
                detail: locale.text("Codex 未打开；选择“打开 Codex”即可使用", "Codex is closed; choose Open Codex when you need it"))
        case .needsLauncher:
            return LauncherCopy(title: locale.text("当前 Codex 尚未启用额度条", "This Codex session has no usage bar connection"),
                detail: locale.text("正常退出 Codex 后，从这里选择“打开 Codex”", "Quit Codex normally, then choose Open Codex here"))
        case .quotaRetrying:
            return LauncherCopy(title: locale.text("额度数据恢复中…", "Recovering usage data…"),
                detail: locale.text("后台会自动重试，暂未取得最新额度", "Retrying in the background; current usage is not yet available"))
        case let .recovering(seconds, error):
            let reason = error.map { $0.copy(in: locale).title + " · " } ?? ""
            return LauncherCopy(title: locale.text("额度条恢复中…", "Recovering usage bar…"),
                detail: reason + locale.text("约 \(seconds) 秒后自动重试", "Retrying in about \(seconds) seconds"))
        case .waitingForCodex:
            return LauncherCopy(title: locale.text("等待 Codex 正常退出", "Waiting for Codex to quit normally"),
                detail: locale.text("保存工作后按 ⌘Q；将自动重新打开", "Save your work and press ⌘Q; Codex will reopen automatically"))
        case .attaching:
            return LauncherCopy(title: locale.text("正在连接额度条…", "Connecting usage bar…"),
                detail: locale.text("等待确认输入框上方显示", "Waiting to confirm the bar above the input box"))
        case .waitingForComposer:
            return LauncherCopy(title: locale.text("等待兼容的输入框", "Waiting for a compatible input box"),
                detail: locale.text("后台仍在尝试连接，尚未确认显示", "Still connecting in the background; the bar is not yet confirmed visible"))
        case let .visible(count, waiting):
            let windows = "\(count) " + (count == 1 ? "window" : "windows")
            let waitingWindows = "\(waiting) " + (waiting == 1 ? "window is" : "windows are")
            return LauncherCopy(title: locale.text("额度条已显示：\(count) 个窗口", "Usage bar visible in \(windows)"),
                detail: waiting > 0 ? locale.text("另有 \(waiting) 个窗口等待恢复", "\(waitingWindows) waiting to reconnect") :
                    locale.text("后台刷新中，不需要终端窗口", "Refreshing in the background; no terminal needed"))
        case .detached:
            return LauncherCopy(title: locale.text("正在结束额度条…", "Finishing usage bar…"),
                detail: locale.text("Codex 继续运行", "Codex is still running"))
        case .codexExited:
            return LauncherCopy(title: locale.text("Codex 已退出", "Codex has quit"),
                detail: locale.text("正在结束额度条管理器", "Finishing the usage bar manager"))
        case .cancelled:
            return LauncherCopy(title: locale.text("已取消等待", "Waiting cancelled"),
                detail: locale.text("Codex 未改变", "Codex is unchanged"))
        case .stopped:
            return LauncherCopy(title: locale.text("额度条已停止", "Usage bar stopped"),
                detail: locale.text("选择“启动额度条”可再次启用", "Choose Start Usage Bar to enable it again"))
        case .stopping:
            return LauncherCopy(title: locale.text("正在停止额度条…", "Stopping usage bar…"),
                detail: locale.text("等待管理器清理；Codex 不会被退出", "Waiting for manager cleanup; Codex will stay open"))
        case .stopFailed:
            return LauncherCopy(title: locale.text("暂未能停止额度条", "Could not request a stop"),
                detail: locale.text("管理器继续运行；可以再次选择停止", "The manager is still running; you can try Stop again"))
        case .waitingForCleanup:
            return LauncherCopy(title: locale.text("仍在等待安全退出", "Still waiting for a safe exit"),
                detail: locale.text("未强制结束任何进程", "No process has been forcibly stopped"))
        case let .failure(error, retry):
            return LauncherCopy(title: error.copy(in: locale).title,
                detail: retry ? locale.text("选择“启动额度条”可重试", "Choose Start Usage Bar to try again") :
                    locale.text("请查看提示后重试", "Review the message, then try again"))
        }
    }
}

enum LauncherAlert: Equatable {
    case waitingForCodex, stopFailed, waitingForCleanup, failure(LauncherFailure)

    func copy(in locale: LauncherLocale) -> LauncherCopy {
        switch self {
        case .waitingForCodex:
            return LauncherCopy(title: locale.text("需要正常重新打开 Codex", "Codex needs to reopen normally"),
                detail: locale.text("当前 Codex 尚未启用额度条连接。请先保存工作，再在 Codex 中按 ⌘Q 正常退出；本启动器会自动用原账号、历史和项目重新打开。不会替你结束运行任务。", "This Codex session does not have the usage bar connection enabled. Save your work, then press ⌘Q in Codex to quit normally. The launcher will reopen it with your existing account, history and projects. It will not end running tasks for you."))
        case .stopFailed:
            return LauncherCopy(title: locale.text("未能请求停止额度条", "Could not request the usage bar to stop"),
                detail: locale.text("停止请求没有成功送达。管理器继续运行，可稍后再次选择“停止额度条”。没有强制退出 Codex。", "The stop request could not be delivered. The manager is still running; try Stop Usage Bar again later. Codex has not been forcibly closed."))
        case .waitingForCleanup:
            return LauncherCopy(title: locale.text("额度条管理器仍在清理", "The usage bar manager is still cleaning up"),
                detail: locale.text("管理器尚未确认退出，启动器将继续等待，不会强制结束 Codex。你可以继续使用 Codex，稍后再查看菜单状态。", "The manager has not confirmed its exit. The launcher will keep waiting without forcibly closing Codex. You can keep using Codex and check the menu again later."))
        case let .failure(error): return error.copy(in: locale)
        }
    }
}

struct LauncherMenuCopy: Equatable {
    let statusBar, accessibility, show, start, stop, quit, acknowledge: String

    init(locale: LauncherLocale) {
        statusBar = locale.text("额度", "Usage")
        accessibility = locale.text("Codex 额度条", "Codex Usage Bar")
        show = locale.text("打开 Codex", "Open Codex")
        start = locale.text("启动额度条", "Start Usage Bar")
        stop = locale.text("停止额度条", "Stop Usage Bar")
        quit = locale.text("退出额度条", "Quit Usage Bar")
        acknowledge = locale.text("知道了", "OK")
    }
}

// Language and semantic status are independent of manager lifecycle state.
// Re-rendering this value cannot restart the manager or replay an alert.
struct LauncherPresentation {
    private(set) var locale: LauncherLocale
    var status: LauncherStatus = .starting
    var menu: LauncherMenuCopy { LauncherMenuCopy(locale: locale) }
    var current: LauncherCopy { status.copy(in: locale) }

    init(preferredLanguages: [String]) { locale = .system(preferredLanguages) }

    mutating func setLocale(_ next: LauncherLocale) -> Bool {
        guard locale != next else { return false }
        locale = next
        return true
    }
}

enum ManagerMessage: Equatable {
    case waitingForCodex, attaching, waitingForComposer
    case residentWaiting, needsLauncher, quotaRetrying
    case visible(Int, Int), detached, codexExited, cancelled
    case failure(LauncherFailure)
    case locale(LauncherLocale)

    // Only recognized, non-private manager messages enter the UI. Raw output is
    // neither displayed nor written to disk. Unknown lines are discarded.
    static func parse(_ line: String) -> ManagerMessage? {
        switch line {
        case "codex-usage-bar-resident:waiting": return .residentWaiting
        case "codex-usage-bar-resident:waiting-for-quit": return .waitingForCodex
        case "codex-usage-bar-resident:attached": return .attaching
        case "codex-usage-bar-resident:needs-launcher": return .needsLauncher
        case "codex-usage-bar-resident:quota-retrying": return .quotaRetrying
        default: break
        }
        if line == "codex-usage-bar-locale:zh-CN" { return .locale(.chinese) }
        if line == "codex-usage-bar-locale:en" { return .locale(.english) }
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

    var status: LauncherStatus? {
        switch self {
        case .residentWaiting: return .residentWaiting
        case .needsLauncher: return .needsLauncher
        case .quotaRetrying: return .quotaRetrying
        case .waitingForCodex: return .waitingForCodex
        case .attaching: return .attaching
        case .waitingForComposer: return .waitingForComposer
        case let .visible(count, waiting): return .visible(count, waiting)
        case .detached: return .detached
        case .codexExited: return .codexExited
        case .cancelled: return .cancelled
        case let .failure(error): return .failure(error, retry: false)
        case .locale: return nil
        }
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

func isResidentLaunch(_ arguments: [String]) -> Bool {
    arguments.contains("--resident")
}

func managerArguments(launchOnce: Bool) -> [String] {
    var arguments = ["--resident"]
    if launchOnce { arguments.append("--launch-once") }
    arguments += ["--supervisor-pid", String(ProcessInfo.processInfo.processIdentifier)]
    return arguments
}

// Own only the direct manager child. The same pipe/termination/timer wiring is
// exercised by offline tests with dummy processes; no saved PID is ever trusted.
final class ResidentManagerSupervisor {
    private(set) var child: Process?
    private(set) var isEnabled = false
    private(set) var retryPending = false
    private(set) var stopRequested = false
    private var outputPipe: Pipe?
    private var output = ManagerOutput()
    private var outputEnded = false
    private var exitObserved = false
    private var failure: LauncherFailure?
    private var retryTimer: Timer?
    private var pendingLaunchOnce = false
    private var retryCount = 0
    private var startedAt: Date?
    private let makeProcess: (Bool) throws -> Process
    private let timerScale: TimeInterval
    private let stableInterval: TimeInterval
    var onMessage: ((ManagerMessage) -> Void)?
    var onStarted: (() -> Void)?
    var onRetry: ((Int) -> Void)?
    var onFinished: ((LauncherFailure?, Bool) -> Void)?
    var onChange: (() -> Void)?

    init(timerScale: TimeInterval = 1, stableInterval: TimeInterval = 60,
         makeProcess: @escaping (Bool) throws -> Process) {
        self.makeProcess = makeProcess
        self.timerScale = timerScale
        self.stableInterval = stableInterval
    }

    static func retryDelay(_ attempt: Int) -> Int {
        [5, 10, 20, 40, 60][min(max(attempt, 0), 4)]
    }

    func start(launchOnce: Bool = false) {
        guard child == nil else { return }
        cancelRetry()
        pendingLaunchOnce = false
        retryCount = 0
        isEnabled = true
        launch(launchOnce: launchOnce)
    }

    // Menu and app re-open share this path. Activating an existing Codex never
    // grants permission to open another one. A cold open replaces only our own
    // observer after its safe shutdown; Stop/Quit cancels a queued open.
    @discardableResult
    func openCodex(activateExisting: () -> Bool) -> Bool {
        if activateExisting() {
            if child == nil { start() }
            return true
        }
        guard child != nil else {
            start(launchOnce: true)
            return true
        }
        guard stop() else { return false }
        pendingLaunchOnce = true
        return true
    }

    private func launch(launchOnce: Bool = false) {
        guard isEnabled, child == nil else { return }
        failure = nil
        stopRequested = false
        outputEnded = false
        exitObserved = false
        output = ManagerOutput()
        let process: Process
        do { process = try makeProcess(launchOnce) }
        catch {
            onFinished?(error as? LauncherFailure ?? .launchFailed, false)
            scheduleRetry()
            return
        }
        let pipe = Pipe()
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
                    handle.readabilityHandler = nil
                    self.outputEnded = true
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
                // Bound drainage if a faulty descendant retains stdout.
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
                    guard let self, self.child === process, self.exitObserved,
                          !self.outputEnded else { return }
                    self.outputEnded = true
                    self.failure = self.failure ?? .cleanupUnverified
                    self.consume(self.output.finish())
                    self.finishIfReady(process)
                }
            }
        }
        onStarted?()
        do {
            try process.run()
            startedAt = Date()
            try? pipe.fileHandleForWriting.close()
            onChange?()
        } catch {
            process.terminationHandler = nil
            closePipe()
            child = nil
            onFinished?(.launchFailed, false)
            scheduleRetry()
        }
    }

    private func consume(_ messages: [ManagerMessage]) {
        for message in messages {
            if case let .failure(error) = message { failure = error }
            onMessage?(message)
        }
    }

    private func closePipe() {
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        try? outputPipe?.fileHandleForReading.close()
        try? outputPipe?.fileHandleForWriting.close()
        outputPipe = nil
    }

    private func finishIfReady(_ process: Process) {
        guard child === process, exitObserved, outputEnded else { return }
        closePipe()
        child = nil
        process.terminationHandler = nil
        if process.terminationReason == .uncaughtSignal {
            failure = failure ?? .cleanupUnverified
        } else if process.terminationStatus != 0 {
            failure = failure ?? .managerFailed
        }
        if let startedAt, Date().timeIntervalSince(startedAt) >= stableInterval {
            retryCount = 0
        }
        startedAt = nil
        let intentional = !isEnabled
        stopRequested = false
        let requestedLaunch = pendingLaunchOnce
        pendingLaunchOnce = false
        onFinished?(failure, intentional)
        if requestedLaunch, failure == nil { start(launchOnce: true) }
        // Automatic recovery always observes; the user gesture's one-use
        // permission is never carried into a replacement manager.
        if isEnabled, child == nil { scheduleRetry() }
        onChange?()
    }

    private func scheduleRetry() {
        guard isEnabled, child == nil, retryTimer == nil else { return }
        let delay = Self.retryDelay(retryCount)
        retryCount = min(retryCount + 1, 4)
        retryPending = true
        let timer = Timer(timeInterval: TimeInterval(delay) * timerScale, repeats: false) { [weak self] _ in
            guard let self else { return }
            self.retryTimer = nil
            self.retryPending = false
            guard self.isEnabled else { return }
            self.launch()
        }
        retryTimer = timer
        RunLoop.main.add(timer, forMode: .common)
        onRetry?(delay)
        onChange?()
    }

    private func cancelRetry() {
        retryTimer?.invalidate()
        retryTimer = nil
        retryPending = false
    }

    @discardableResult
    func stop() -> Bool {
        pendingLaunchOnce = false
        isEnabled = false
        cancelRetry()
        guard let child else {
            onChange?()
            return true
        }
        guard !stopRequested else { return true }
        guard signalManagerStop(child) else { return false }
        stopRequested = true
        onChange?()
        return true
    }
}

final class UsageBarLauncher: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private let statusLabel = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let detailLabel = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private var startItem: NSMenuItem!
    private var stopItem: NSMenuItem!
    private var showItem: NSMenuItem!
    private var quitItem: NSMenuItem!
    private var presentation = LauncherPresentation(preferredLanguages: Locale.preferredLanguages)
    private var presentedAlert: (kind: LauncherAlert, alert: NSAlert)?
    private lazy var supervisor = ResidentManagerSupervisor { [weak self] launchOnce in
        guard let script = self?.bundledScript() else { throw LauncherFailure.resourcesMissing }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-f", script.path] + managerArguments(launchOnce: launchOnce)
        process.currentDirectoryURL = script.deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let allowed = ["HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"]
        var environment: [String: String] = [:]
        for key in allowed { environment[key] = ProcessInfo.processInfo.environment[key] }
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        process.environment = environment
        return process
    }
    private var quitRequested = false
    private var failureAlertShown = false
    private var recoveryFailure: LauncherFailure?
    private var stopTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let menu = NSMenu()
        menu.autoenablesItems = false
        menu.delegate = self
        statusLabel.isEnabled = false
        detailLabel.isEnabled = false
        menu.addItem(statusLabel)
        menu.addItem(detailLabel)
        menu.addItem(.separator())
        showItem = addItem("", action: #selector(showCodex), to: menu)
        startItem = addItem("", action: #selector(startManager), to: menu)
        stopItem = addItem("", action: #selector(stopManager), to: menu)
        menu.addItem(.separator())
        quitItem = addItem("", action: #selector(quitLauncher), to: menu)
        statusItem.menu = menu
        supervisor.onMessage = { [weak self] message in self?.consume(message) }
        supervisor.onStarted = { [weak self] in self?.setStatus(.starting) }
        supervisor.onRetry = { [weak self] delay in
            guard let self else { return }
            self.setStatus(.recovering(delay, self.recoveryFailure))
        }
        supervisor.onChange = { [weak self] in self?.refreshActions() }
        supervisor.onFinished = { [weak self] error, intentional in
            self?.managerFinished(error: error, intentional: intentional)
        }
        refreshLanguage()
        // Login/recovery only observes. A manual app launch is an explicit
        // request to open Codex once; later Codex quits remain respected.
        if isResidentLaunch(ProcessInfo.processInfo.arguments) {
            startManager()
        } else {
            showCodex()
        }
    }

    @discardableResult
    private func addItem(_ title: String, action: Selector, to menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        menu.addItem(item)
        return item
    }

    private func setStatus(_ status: LauncherStatus) {
        presentation.status = status
        renderStatus()
    }

    private func renderStatus() {
        let copy = presentation.current
        statusLabel.title = copy.title
        detailLabel.title = copy.detail
        statusItem.button?.toolTip = copy.title + "\n" + copy.detail
        refreshActions()
    }

    private func refreshLanguage() {
        let copy = presentation.menu
        statusItem.button?.title = copy.statusBar
        statusItem.button?.setAccessibilityLabel(copy.accessibility)
        showItem?.title = copy.show
        startItem?.title = copy.start
        stopItem?.title = copy.stop
        quitItem?.title = copy.quit
        renderStatus()
        if let current = presentedAlert {
            let alertCopy = current.kind.copy(in: presentation.locale)
            current.alert.messageText = alertCopy.title
            current.alert.informativeText = alertCopy.detail
            current.alert.buttons.first?.title = copy.acknowledge
            current.alert.layout()
        }
    }

    private func refreshActions() {
        // Keep Start unavailable until termination and final pipe drainage finish.
        startItem?.isEnabled = supervisor.child == nil && !quitRequested
        stopItem?.isEnabled = (supervisor.isEnabled || supervisor.child != nil) && !supervisor.stopRequested
        showItem?.isEnabled = !quitRequested
    }

    func menuWillOpen(_ menu: NSMenu) { refreshActions() }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        // A double-click is the same explicit intent as the Open Codex menu.
        showCodex()
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
        guard supervisor.child == nil, !quitRequested else { return }
        failureAlertShown = false
        supervisor.start()
    }

    private func consume(_ message: ManagerMessage) {
        if case let .locale(locale) = message {
            if presentation.setLocale(locale) { refreshLanguage() }
            return
        }
        guard !supervisor.stopRequested else { return }
        if let status = message.status { setStatus(status) }
        // Background retries only update the menu. Modal warnings here would
        // steal focus on each reconnect and can turn a crash into an alert loop.
    }

    private func managerFinished(error: LauncherFailure?, intentional: Bool) {
        stopTimer?.invalidate()
        stopTimer = nil
        recoveryFailure = error
        guard intentional else { return } // supervisor publishes its next retry
        if let error {
            if quitRequested {
                quitRequested = false
                NSApp.reply(toApplicationShouldTerminate: false)
            }
            presentFailure(error)
        } else {
            setStatus(.stopped)
            if quitRequested { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        refreshActions()
    }

    @objc private func stopManager() { requestStop() }

    private func requestStop() {
        // Stop cancels the pending timer even when there is no child to signal.
        if !supervisor.stop() {
            if quitRequested { quitRequested = false }
            setStatus(.stopFailed)
            showAlert(.stopFailed)
            return
        }
        guard supervisor.child != nil else {
            setStatus(.stopped)
            return
        }
        waitForManagerCleanup()
    }

    private func waitForManagerCleanup() {
        guard let process = supervisor.child else { return }
        setStatus(.stopping)
        stopTimer?.invalidate()
        stopTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: false) { [weak self, weak process] _ in
            guard let self, let process, self.supervisor.child === process else { return }
            if self.quitRequested {
                self.quitRequested = false
                NSApp.reply(toApplicationShouldTerminate: false)
            }
            self.setStatus(.waitingForCleanup)
            self.showAlert(.waitingForCleanup)
        }
    }

    @objc private func quitLauncher() { NSApp.terminate(nil) }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard supervisor.child != nil else {
            supervisor.stop()
            return .terminateNow
        }
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
        guard !quitRequested else { return }
        let requested = supervisor.openCodex { [weak self] in
            guard let codex = self?.runningCodex() else { return false }
            codex.activate(options: [])
            return true
        }
        if !requested {
            setStatus(.stopFailed)
            showAlert(.stopFailed)
        } else if supervisor.stopRequested {
            waitForManagerCleanup()
        }
    }

    private func presentFailure(_ error: LauncherFailure) {
        setStatus(.failure(error, retry: true))
        guard !failureAlertShown else { return }
        failureAlertShown = true
        showAlert(.failure(error))
    }

    private func showAlert(_ kind: LauncherAlert) {
        let copy = kind.copy(in: presentation.locale)
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = copy.title
        alert.informativeText = copy.detail
        alert.addButton(withTitle: presentation.menu.acknowledge)
        presentedAlert = (kind, alert)
        defer { presentedAlert = nil }
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }
}

#if LAUNCHER_TESTS
// Compile with -D LAUNCHER_TESTS for offline protocol/buffering checks. This
// branch never creates NSApplication, a real manager or a Codex process.
var checks = 0
func check(_ condition: @autoclosure () -> Bool) {
    precondition(condition(), "Launcher protocol check failed")
    checks += 1
}
check(ManagerMessage.parse("codex-usage-bar-locale:zh-CN") == .locale(.chinese))
check(ManagerMessage.parse("codex-usage-bar-locale:en") == .locale(.english))
for value in ["zh", "zh-cn", "en-GB", "EN", "", " en", "en ", "en\u{0}",
              "en:private", "en\nprivate", "zh-CN/private"] {
    check(ManagerMessage.parse("codex-usage-bar-locale:" + value) == nil)
}
check(ManagerMessage.parse(" codex-usage-bar-locale:en") == nil)
check(LauncherLocale.system(["zh-Hans-CN", "en"]) == .chinese)
check(LauncherLocale.system(["zh-Hant-TW"]) == .chinese)
check(LauncherLocale.system(["ZH-HK"]) == .chinese)
check(LauncherLocale.system(["en-GB", "zh-Hans"]) == .english)
check(LauncherLocale.system(["fr-FR", "zh"]) == .english)
check(LauncherLocale.system([]) == .english)
var presentation = LauncherPresentation(preferredLanguages: ["zh-CN"])
check(presentation.menu == LauncherMenuCopy(locale: .chinese))
check(presentation.menu.statusBar == "额度")
check(presentation.current.title == "正在启动额度条…")
presentation.status = .visible(2, 1)
check(presentation.setLocale(.english))
check(presentation.status == .visible(2, 1))
check(presentation.menu == LauncherMenuCopy(locale: .english))
check(presentation.menu.statusBar == "Usage")
check(presentation.menu.show == "Open Codex")
check(presentation.menu.start == "Start Usage Bar")
check(presentation.menu.stop == "Stop Usage Bar")
check(presentation.menu.quit == "Quit Usage Bar")
check(presentation.menu.accessibility == "Codex Usage Bar")
check(presentation.menu.acknowledge == "OK")
check(presentation.current == LauncherCopy(title: "Usage bar visible in 2 windows", detail: "1 window is waiting to reconnect"))
check(!presentation.setLocale(.english))
check(presentation.status == .visible(2, 1))
let states: [LauncherStatus] = [.starting, .waitingForCodex, .attaching, .waitingForComposer,
    .visible(1, 0), .visible(2, 3), .detached, .codexExited, .cancelled, .stopped,
    .stopping, .stopFailed, .waitingForCleanup, .residentWaiting, .needsLauncher,
    .quotaRetrying, .recovering(5, nil), .recovering(10, .pythonMissing), .failure(.cleanupUnverified, retry: false),
    .failure(.managerFailed, retry: true)]
for status in states {
    presentation.status = status
    _ = presentation.setLocale(.english)
    let english = presentation.current
    check(!english.title.isEmpty && !english.detail.isEmpty)
    _ = presentation.setLocale(.chinese)
    check(presentation.status == status)
    check(presentation.current != english)
}
for failure in LauncherFailure.allCases {
    let chinese = LauncherAlert.failure(failure).copy(in: .chinese)
    let english = LauncherAlert.failure(failure).copy(in: .english)
    check(!chinese.title.isEmpty && !chinese.detail.isEmpty)
    check(!english.title.isEmpty && !english.detail.isEmpty && chinese != english)
}
for alert in [LauncherAlert.waitingForCodex, .stopFailed, .waitingForCleanup] {
    check(alert.copy(in: .chinese) != alert.copy(in: .english))
}
var localeOutput = ManagerOutput()
let localeLine = Data("codex-usage-bar-locale:en\n".utf8)
check(localeOutput.accept(localeLine.prefix(11)).isEmpty)
check(localeOutput.accept(localeLine.dropFirst(11)) == [.locale(.english)])
check(localeOutput.accept(Data("codex-usage-bar-locale:en-GB\nPRIVATE\n".utf8)).isEmpty)
check(localeOutput.accept(Data("codex-usage-bar-locale:zh-CN".utf8)).isEmpty)
check(localeOutput.finish() == [.locale(.chinese)])
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
// Serialized protocol -> the exact semantic UI mapping used by the app.
let residentStates: [(String, LauncherStatus)] = [
    ("waiting", .residentWaiting), ("waiting-for-quit", .waitingForCodex),
    ("attached", .attaching), ("needs-launcher", .needsLauncher), ("quota-retrying", .quotaRetrying)
]
for (wire, expected) in residentStates {
    var decoder = ManagerOutput()
    let serialized = Data(("codex-usage-bar-resident:" + wire + "\n").utf8)
    check(decoder.accept(serialized.prefix(13)).isEmpty)
    let messages = decoder.accept(serialized.dropFirst(13))
    check(messages.count == 1)
    check(messages.first?.status == expected)
    presentation.status = messages.first!.status!
    check(!presentation.current.title.isEmpty)
}
for suffix in ["", "attached:private", " attached", "attached ", "unknown"] {
    check(ManagerMessage.parse("codex-usage-bar-resident:" + suffix) == nil)
}

// Exercise the actual production Process -> pipe -> termination -> Timer ->
// retry path. Time scaling shortens delays, not the scheduler or callbacks.
func spinUntil(_ predicate: () -> Bool, timeout: TimeInterval = 4) -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while !predicate() && Date() < deadline {
        RunLoop.main.run(until: Date().addingTimeInterval(0.005))
    }
    return predicate()
}
func spinFor(_ duration: TimeInterval) {
    let deadline = Date().addingTimeInterval(duration)
    while Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.005)) }
}
func dummyProcess(_ script: String) -> Process {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    process.arguments = ["-I", "-u", "-c", script]
    return process
}
var launches = 0
var retryDelays: [Int] = []
var lifecycleMessages: [ManagerMessage] = []
var completions: [(LauncherFailure?, Bool)] = []
let crashSupervisor = ResidentManagerSupervisor(timerScale: 0.005) { _ in
    launches += 1
    return dummyProcess("import sys\nprint('codex-usage-bar-resident:attached')\nsys.exit(7)")
}
crashSupervisor.onMessage = { message in
    lifecycleMessages.append(message)
    if let status = message.status { presentation.status = status }
}
crashSupervisor.onFinished = { error, intentional in completions.append((error, intentional)) }
crashSupervisor.onRetry = { delay in
    retryDelays.append(delay)
    if retryDelays.count == 6 { check(crashSupervisor.stop()) }
}
crashSupervisor.start()
check(spinUntil { retryDelays.count == 6 })
check(launches == 6)
check(retryDelays == [5, 10, 20, 40, 60, 60])
check(completions.count == 6 && completions.allSatisfy { $0.0 == .managerFailed && !$0.1 })
check(lifecycleMessages == Array(repeating: .attaching, count: 6))
check(presentation.status == .attaching)
check(crashSupervisor.child == nil && !crashSupervisor.retryPending && !crashSupervisor.isEnabled)
spinFor(0.4)
check(launches == 6) // Stop cancels the installed 60-second retry timer.
// A deliberate re-open of a stopped companion resets backoff and starts again.
crashSupervisor.onRetry = { delay in
    retryDelays.append(delay)
    check(crashSupervisor.stop())
}
crashSupervisor.start()
check(spinUntil { retryDelays.count == 7 })
check(launches == 7 && retryDelays.last == 5)

// A clean but unsolicited exit must also restart in resident mode.
var zeroLaunches = 0
var zeroRetries = 0
let zeroSupervisor = ResidentManagerSupervisor(timerScale: 0.002) { _ in
    zeroLaunches += 1
    return dummyProcess("print('codex-usage-bar-resident:waiting')")
}
zeroSupervisor.onRetry = { _ in
    zeroRetries += 1
    if zeroRetries == 2 { check(zeroSupervisor.stop()) }
}
zeroSupervisor.start()
check(spinUntil { zeroRetries == 2 })
check(zeroLaunches == 2)

// A manager that survives the stability window resets its next crash delay.
var stableLaunches = 0
var stableDelays: [Int] = []
let stableSupervisor = ResidentManagerSupervisor(timerScale: 0.002, stableInterval: 0.5) { _ in
    stableLaunches += 1
    return dummyProcess(stableLaunches == 3 ? "import time; time.sleep(0.6)" : "raise SystemExit(3)")
}
stableSupervisor.onRetry = { delay in
    stableDelays.append(delay)
    if stableDelays.count == 3 { check(stableSupervisor.stop()) }
}
stableSupervisor.start()
check(spinUntil { stableDelays.count == 3 })
check(stableDelays == [5, 10, 5])

// Stop an alive manager through the real direct-PID signal path; its handled
// SIGTERM exits normally and must never schedule another launch.
var aliveLaunches = 0
var aliveReady = false
var aliveFinished = false
var aliveRetries = 0
let aliveSupervisor = ResidentManagerSupervisor(timerScale: 0.002) { _ in
    aliveLaunches += 1
    return dummyProcess("import signal,sys,time\nsignal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\nprint('codex-usage-bar-resident:attached')\ntime.sleep(4)")
}
aliveSupervisor.onMessage = { if $0 == .attaching { aliveReady = true } }
aliveSupervisor.onFinished = { error, intentional in
    check(error == nil && intentional)
    aliveFinished = true
}
aliveSupervisor.onRetry = { _ in aliveRetries += 1 }
aliveSupervisor.start()
check(spinUntil { aliveReady })
check(aliveSupervisor.stop())
check(spinUntil { aliveFinished })
spinFor(0.08)
check(aliveLaunches == 1 && aliveRetries == 0)
check(aliveSupervisor.child == nil && !aliveSupervisor.isEnabled)

// Launch failures use the same bounded scheduler without a modal alert loop.
var failedAttempts = 0
var launchErrors: [LauncherFailure] = []
let missingSupervisor = ResidentManagerSupervisor(timerScale: 0.002) { _ in
    failedAttempts += 1
    throw LauncherFailure.resourcesMissing
}
missingSupervisor.onFinished = { error, intentional in
    check(!intentional)
    if let error { launchErrors.append(error) }
}
missingSupervisor.onRetry = { _ in check(missingSupervisor.stop()) }
missingSupervisor.start()
spinFor(0.04)
check(failedAttempts == 1 && launchErrors == [.resourcesMissing])
check(!missingSupervisor.retryPending)
// Production startup intent and argument serialization: login never grants a
// launch, while explicit opens grant one permission that automatic retry drops.
check(isResidentLaunch(["launcher", "--resident"]))
check(!isResidentLaunch(["launcher"]))
check(!isResidentLaunch(["launcher", "-psn_0_1234"]))
check(!managerArguments(launchOnce: false).contains("--launch-once"))
check(managerArguments(launchOnce: true).filter { $0 == "--launch-once" }.count == 1)
check(managerArguments(launchOnce: false).last == String(ProcessInfo.processInfo.processIdentifier))
var intentFlags: [Bool] = []
var intentWireStates: [LauncherStatus] = []
var intentRetries = 0
let intentSupervisor = ResidentManagerSupervisor(timerScale: 0.002) { launchOnce in
    intentFlags.append(launchOnce)
    let process = dummyProcess("import sys\nprint('codex-usage-bar-resident:' + ('attached' if '--launch-once' in sys.argv else 'waiting'))\nraise SystemExit(7)")
    process.arguments! += managerArguments(launchOnce: launchOnce)
    return process
}
intentSupervisor.onMessage = { if let status = $0.status { intentWireStates.append(status) } }
intentSupervisor.onRetry = { _ in
    intentRetries += 1
    if intentRetries == 2 { check(intentSupervisor.stop()) }
}
check(intentSupervisor.openCodex { false })
check(spinUntil { intentRetries == 2 })
check(intentFlags == [true, false])
check(intentWireStates == [.attaching, .residentWaiting])

// Same menu/reopen path while an observer is alive: wait for its normal cleanup,
// restart with one launch permission, then remain observing after the app quits.
var replacementFlags: [Bool] = []
var replacementStatuses: [LauncherStatus] = []
var replacementFinishes = 0
let replacementSupervisor = ResidentManagerSupervisor(timerScale: 0.002) { launchOnce in
    replacementFlags.append(launchOnce)
    let process = dummyProcess("import signal,sys,time\nsignal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\nprint('codex-usage-bar-resident:' + ('attached' if '--launch-once' in sys.argv else 'waiting'))\ntime.sleep(4)")
    process.arguments! += managerArguments(launchOnce: launchOnce)
    return process
}
replacementSupervisor.onMessage = { if let status = $0.status { replacementStatuses.append(status) } }
replacementSupervisor.onFinished = { error, intentional in
    check(error == nil && intentional)
    replacementFinishes += 1
}
replacementSupervisor.start() // Start Usage Bar only restores the observer.
check(spinUntil { replacementStatuses == [.residentWaiting] })
var activations = 0
check(replacementSupervisor.openCodex { activations += 1; return true })
spinFor(0.04)
check(activations == 1 && replacementFlags == [false]) // existing app: activate only
check(replacementSupervisor.openCodex { false }) // menu/reopen with Codex closed
check(spinUntil { replacementStatuses == [.residentWaiting, .attaching] })
check(replacementFlags == [false, true] && replacementFinishes == 1)
check(replacementSupervisor.stop())
check(spinUntil { replacementFinishes == 2 })

// A Stop gesture after an explicit open but before cleanup cancels that request.
replacementStatuses = []
replacementSupervisor.start()
check(spinUntil { replacementStatuses == [.residentWaiting] })
check(replacementSupervisor.openCodex { false })
check(replacementSupervisor.stop())
check(spinUntil { replacementFinishes == 3 })
spinFor(0.05)
check(replacementFlags == [false, true, false])
check(replacementSupervisor.child == nil && !replacementSupervisor.isEnabled)

// An explicit open cannot bypass unconfirmed manager cleanup.
var unsafeFlags: [Bool] = []
var unsafeReady = false
var unsafeFinished = false
let unsafeSupervisor = ResidentManagerSupervisor(timerScale: 0.002) { launchOnce in
    unsafeFlags.append(launchOnce)
    return dummyProcess("import signal,sys,time\nsignal.signal(signal.SIGTERM, lambda *_: sys.exit(9))\nprint('codex-usage-bar-resident:waiting')\ntime.sleep(4)")
}
unsafeSupervisor.onMessage = { if $0 == .residentWaiting { unsafeReady = true } }
unsafeSupervisor.onFinished = { error, intentional in
    check(error == .managerFailed && intentional)
    unsafeFinished = true
}
unsafeSupervisor.start()
check(spinUntil { unsafeReady })
check(unsafeSupervisor.openCodex { false })
check(spinUntil { unsafeFinished })
spinFor(0.05)
check(unsafeFlags == [false] && unsafeSupervisor.child == nil)
print("Native launcher: \(checks) offline checks passed; no application launched.")
#else
let application = NSApplication.shared
let delegate = UsageBarLauncher()
application.delegate = delegate
application.run()
#endif
