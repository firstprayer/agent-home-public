import AppKit
import WebKit

@MainActor
final class AgentHomeApp: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var statusItem: NSStatusItem!
    private var server: Process?
    private let output = Pipe()
    private let lifetime = Pipe()
    private var buffer = Data()
    private var dashboard: URL?
    private var quitting = false
    private var failed = false
    private let logURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/Agent Home/server.log")

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Agent Home", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Show Agent Home", action: #selector(showWindow), keyEquivalent: "0")
        appMenu.addItem(withTitle: "Get Started", action: #selector(showGuide), keyEquivalent: "")
        appMenu.addItem(withTitle: "Settings…", action: #selector(showSettings), keyEquivalent: ",")
        appMenu.addItem(withTitle: "Open Service Configuration…", action: #selector(openConfiguration), keyEquivalent: "")
        appMenu.addItem(withTitle: "Show Log", action: #selector(showLog), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Agent Home", action: #selector(quit), keyEquivalent: "q")
        for item in appMenu.items { item.target = self }
        let rootItem = NSMenuItem()
        rootItem.submenu = appMenu
        menu.addItem(rootItem)
        let edit = NSMenu(title: "Edit")
        for (title, selector, key) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"),
                                      ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
                                      ("Select All", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(selector), keyEquivalent: key)
        }
        let editItem = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        editItem.submenu = edit
        menu.addItem(editItem)
        NSApp.mainMenu = menu
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(systemSymbolName: "house", accessibilityDescription: "Agent Home")
        statusItem.menu = appMenu.copy() as? NSMenu

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Agent Home"
        window.minSize = NSSize(width: 760, height: 500)
        window.isReleasedWhenClosed = false
        window.center()
        webView = WKWebView(frame: window.contentView!.bounds)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        window.contentView = webView
        let loading = NSTextField(labelWithString: "Starting Agent Home…")
        loading.sizeToFit()
        loading.frame.origin = NSPoint(x: 32, y: 32)
        webView.addSubview(loading)
        showWindow()
        startServer()
    }

    private func startServer() {
        do {
            try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(),
                withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let descriptor = Darwin.open(logURL.path, O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW, 0o600)
            guard descriptor >= 0 else { throw CocoaError(.fileWriteNoPermission) }
            let log = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: logURL.path)
            let process = Process()
            process.executableURL = Bundle.main.executableURL!.deletingLastPathComponent()
                .appendingPathComponent("agent-home-server")
            process.arguments = ["--desktop"]
            // Finder's working folder may be read-only, or the app may run from a DMG.
            process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
            process.standardInput = lifetime
            process.standardOutput = output
            process.standardError = log
            output.fileHandleForReading.readabilityHandler = { handle in
                let data = handle.availableData
                if data.isEmpty { handle.readabilityHandler = nil; return }
                DispatchQueue.main.async { self.receive(data) }
            }
            process.terminationHandler = { process in
                DispatchQueue.main.async {
                    self.output.fileHandleForReading.readabilityHandler = nil
                    if self.quitting { NSApp.reply(toApplicationShouldTerminate: true) }
                    else { self.fail("The background service stopped (exit \(process.terminationStatus)). Check the log. If another Agent Home service is running, stop it before opening this app.") }
                }
            }
            server = process
            try process.run()
            DispatchQueue.main.asyncAfter(deadline: .now() + 30) {
                if self.dashboard == nil && !self.quitting {
                    self.fail("The background service did not finish starting. Check the log for details.")
                }
            }
        } catch {
            fail("Cannot start the background service. Check that the application and your user folder are accessible.")
        }
    }

    private func receive(_ data: Data) {
        buffer.append(data)
        guard buffer.count < 65536 else { fail("Invalid startup response."); return }
        while let newline = buffer.firstIndex(of: 10) {
            let line = buffer.prefix(upTo: newline)
            buffer.removeSubrange(...newline)
            guard let message = try? JSONSerialization.jsonObject(with: line) as? [String: String],
                  message["event"] == "ready", let text = message["url"], let url = URL(string: text),
                  url.scheme == "http", url.host == "127.0.0.1", url.port != nil,
                  url.user == nil, url.password == nil, url.path == "/" else { continue }
            dashboard = url
            webView.subviews.filter { $0 is NSTextField }.forEach { $0.removeFromSuperview() }
            webView.load(URLRequest(url: url))
        }
    }

    private func fail(_ message: String) {
        guard !failed && !quitting else { return }
        failed = true
        let alert = NSAlert()
        alert.messageText = "Agent Home could not run"
        alert.informativeText = message
        alert.addButton(withTitle: "Quit")
        alert.addButton(withTitle: "Show Log")
        if alert.runModal() == .alertSecondButtonReturn { showLog() }
        NSApp.terminate(nil)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url, let base = dashboard else {
            decisionHandler(.cancel); return
        }
        if url.scheme == base.scheme && url.host == base.host && url.port == base.port {
            decisionHandler(.allow)
        } else {
            if navigationAction.navigationType == .linkActivated && ["https", "http"].contains(url.scheme) {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).domain == NSURLErrorDomain && (error as NSError).code == NSURLErrorCancelled { return }
        fail("The local dashboard could not load. Check the log for details.")
    }

    @objc func showWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    @objc func showAbout() {
        NSApp.orderFrontStandardAboutPanel(options: [.credits: NSAttributedString(string: "Talk to your Codex agent, anywhere.\nGNU AGPLv3. No warranty. By firstprayer.\nSource and license: https://github.com/firstprayer/agent-home-public")])
        NSApp.activate(ignoringOtherApps: true)
    }
    private func showPage(_ fragment: String) {
        showWindow()
        guard let dashboard = dashboard, var components = URLComponents(url: dashboard, resolvingAgainstBaseURL: false) else { return }
        components.fragment = fragment
        if let url = components.url { webView.load(URLRequest(url: url)) }
    }
    @objc func showSettings() { showPage("settings") }
    @objc func showGuide() { showPage("guide") }
    @objc func showLog() { NSWorkspace.shared.activateFileViewerSelecting([logURL]) }
    @objc func openConfiguration() {
        let path = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/agent-communication/service.toml")
        NSWorkspace.shared.open(path)
    }
    @objc func quit() { NSApp.terminate(nil) }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow(); return true
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        quitting = true
        guard let server = server, server.isRunning else { return .terminateNow }
        server.terminate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) {
            if server.isRunning { Darwin.kill(server.processIdentifier, SIGKILL) }
        }
        return .terminateLater
    }
}

@main
enum Launcher {
    @MainActor static func main() {
        let application = NSApplication.shared
        let delegate = AgentHomeApp()
        application.delegate = delegate
        application.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { application.run() }
    }
}
