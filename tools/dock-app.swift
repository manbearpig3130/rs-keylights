// dock-app.swift: rs-keylights.app's own window (built by tools/dock-app.sh), the page in WebKit (as Safari has it)
// apart from the browser. It starts the server with its Resources/server.sh (start | check | stop), shows the page
// once the server answers (the first start reads the songs a while), and stops the server as it quits, giving the
// Launchkey back; a server already started in a Terminal is shown and left running. Closing the window leaves the
// server on (the Launchkey and the game's menu go on working); clicking the Dock icon shows it again.
import AppKit
import WebKit

@MainActor
final class App: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate,
                 WKDownloadDelegate {
    let script = Bundle.main.path(forResource: "server", ofType: "sh")!
    let page = URL(string: Bundle.main.object(forInfoDictionaryKey: "RSKeylightsPage") as? String ??
                   "http://127.0.0.1:8040/")!
    let shell = DispatchQueue(label: "server.sh")
    let session = URLSession(configuration: .ephemeral)
    let zooms: [CGFloat] = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2]
    var window: NSWindow!
    var web: WKWebView!
    var ours = false  // the server is this app's: checked on while it runs, stopped as the app quits
    var loaded = false  // the page asked for (not the note shown while waiting for the server)
    var probing = false
    var stopping = false
    var ticks = 0
    var saving: [WKDownload: URL] = [:]
    var terminate: DispatchSourceSignal?

    func applicationDidFinishLaunching(_ note: Notification) {
        NSApp.mainMenu = menu()
        makeWindow()
        waiting()
        signal(SIGTERM, SIG_IGN)  // `kill`: quit as ⌘Q does, stopping the server
        terminate = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        terminate?.setEventHandler { NSApp.terminate(nil) }
        terminate?.resume()
        server("start") { ok, said in
            guard ok else { return self.fail("rs-keylights didn't start", said) }
            self.ours = said != "elsewhere"
            Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in MainActor.assumeIsolated { self.tick() } }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if stopping { return .terminateCancel }  // (already stopping the server: it quits once that's done)
        guard ours else { return .terminateNow }
        stopping = true
        window.orderOut(nil)
        server("stop") { _, _ in NSApp.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }

    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { true }

    // MARK: The server

    /// server.sh COMMAND on a thread of its own, then `done` with whether it succeeded and what it printed.
    func server(_ command: String, then done: @escaping @MainActor (Bool, String) -> Void) {
        let script = self.script
        shell.async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/sh")
            process.arguments = [script, command]
            let out = Pipe()
            process.standardOutput = out
            process.standardError = out
            process.standardInput = FileHandle.nullDevice
            var ok = false, said = ""
            do {
                try process.run()
                said = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
                process.waitUntilExit()
                ok = process.terminationStatus == 0
            } catch {
                said = error.localizedDescription
            }
            let trimmed = said.trimmingCharacters(in: .whitespacesAndNewlines)
            DispatchQueue.main.async { done(ok, trimmed) }
        }
    }

    /// Every half second: the page asked for once the server answers; every 3 s, whether the server still runs.
    func tick() {
        ticks += 1
        if !loaded && !probing { probe() }
        guard ours, !stopping, ticks % 6 == 0 else { return }
        server("check") { _, stopped in
            guard !stopped.isEmpty, self.ours, !self.stopping else { return }
            self.ours = false  // (nothing to stop as it quits)
            if stopped == "gone" { NSApp.terminate(nil) } else { self.fail("rs-keylights stopped", stopped) }
        }
    }

    func probe() {
        probing = true
        let request = URLRequest(url: page, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 2)
        session.dataTask(with: request) { _, response, _ in
            let answered = response != nil
            DispatchQueue.main.async {
                self.probing = false
                guard answered, !self.loaded else { return }
                self.loaded = true
                self.web.load(URLRequest(url: self.page))
            }
        }.resume()
    }

    func waiting() {
        web.loadHTMLString("""
            <!doctype html><meta charset="utf-8"><meta name="color-scheme" content="light dark">
            <style>html { height: 100%; display: grid; place-items: center; font: 15px -apple-system, sans-serif;
            color: #888; text-align: center }</style>
            <p>Starting rs-keylights&hellip;<br>Its first start reads the songs, which takes a while longer.</p>
            """, baseURL: nil)
    }

    func fail(_ title: String, _ message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = title
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }

    // MARK: The window

    func makeWindow() {
        let config = WKWebViewConfiguration()
        if #available(macOS 12.3, *) { config.preferences.isElementFullscreenEnabled = true }  // the page's Full screen
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1280, height: 860), configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        if #available(macOS 13.3, *) { web.isInspectable = true }  // Safari's Develop menu can look into it
        let zoom = UserDefaults.standard.double(forKey: "zoom")
        if zoom > 0 { web.pageZoom = zoom }
        window = NSWindow(contentRect: web.frame, styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "rs-keylights"
        window.contentView = web
        window.contentMinSize = NSSize(width: 480, height: 360)
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.collectionBehavior.insert(.fullScreenPrimary)
        window.delegate = self
        if !window.setFrameUsingName("rs-keylights") { window.center() }
        window.setFrameAutosaveName("rs-keylights")
        window.makeKeyAndOrderFront(nil)
    }

    func menu() -> NSMenu {
        let bar = NSMenu()
        @discardableResult
        func submenu(_ title: String, _ items: [NSMenuItem]) -> NSMenu {
            let menu = NSMenu(title: title)
            items.forEach(menu.addItem)
            let holder = NSMenuItem()
            holder.submenu = menu
            bar.addItem(holder)
            return menu
        }
        func item(_ title: String, _ action: Selector, _ key: String = "", _ also: NSEvent.ModifierFlags = [],
                  mine: Bool = false) -> NSMenuItem {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
            item.keyEquivalentModifierMask = also.union(.command)
            if mine { item.target = self }
            return item
        }
        submenu("rs-keylights", [
            item("About rs-keylights", #selector(NSApplication.orderFrontStandardAboutPanel(_:))),
            .separator(),
            item("Hide rs-keylights", #selector(NSApplication.hide(_:)), "h"),
            item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", .option),
            item("Show All", #selector(NSApplication.unhideAllApplications(_:))),
            .separator(),
            item("Quit rs-keylights", #selector(NSApplication.terminate(_:)), "q"),
        ])
        submenu("Edit", [
            item("Undo", Selector(("undo:")), "z"),
            item("Redo", Selector(("redo:")), "z", .shift),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"),
            item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"),
            item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        let bigger = item("Zoom In", #selector(zoomIn(_:)), "+", mine: true)  // ⌘+ as well as ⌘=
        bigger.isHidden = true
        bigger.allowsKeyEquivalentWhenHidden = true
        submenu("View", [
            item("Reload Page", #selector(reloadPage(_:)), "r", mine: true),
            .separator(),
            item("Actual Size", #selector(actualSize(_:)), "0", mine: true),
            item("Zoom In", #selector(zoomIn(_:)), "=", mine: true),
            bigger,
            item("Zoom Out", #selector(zoomOut(_:)), "-", mine: true),
            .separator(),
            item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", .control),
        ])
        NSApp.windowsMenu = submenu("Window", [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Zoom", #selector(NSWindow.performZoom(_:))),
            .separator(),
            item("Close", #selector(NSWindow.performClose(_:)), "w"),
        ])
        return bar
    }

    @objc func reloadPage(_ sender: Any?) { if loaded { web.reload() } }
    @objc func actualSize(_ sender: Any?) { zoom(1) }
    @objc func zoomIn(_ sender: Any?) { zoom(zooms.first { $0 > web.pageZoom + 0.01 } ?? web.pageZoom) }
    @objc func zoomOut(_ sender: Any?) { zoom(zooms.last { $0 < web.pageZoom - 0.01 } ?? web.pageZoom) }

    func zoom(_ to: CGFloat) {
        web.pageZoom = to
        UserDefaults.standard.set(Double(to), forKey: "zoom")
    }

    // MARK: The page

    /// The server's own pages (and the note while waiting): anything else opens in the browser.
    func here(_ url: URL) -> Bool {
        !["http", "https"].contains(url.scheme ?? "") || (url.host == page.host && url.port == page.port)
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        if action.shouldPerformDownload { return .download }  // the MIDI and WAV exports (a link's download)
        if let to = action.request.url, !here(to), action.navigationType == .linkActivated {
            NSWorkspace.shared.open(to)
            return .cancel
        }
        return .allow
    }

    func webView(_ webView: WKWebView,
                 decidePolicyFor response: WKNavigationResponse) async -> WKNavigationResponsePolicy {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")
        return disposition?.lowercased().hasPrefix("attachment") == true || !response.canShowMIMEType ?
            .download : .allow
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    /// The server gone while the page shows (restarted in a Terminal, say): the note again until it answers.
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        lost(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { lost(error) }

    func lost(_ error: Error) {
        let error = error as NSError  // (a download's navigation ends with WebKit's own error: not this)
        guard loaded, error.domain == NSURLErrorDomain, error.code != NSURLErrorCancelled else { return }
        loaded = false
        waiting()
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if loaded { webView.reload() } else { waiting() }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let to = action.request.url {  // (a link to a new window: here, or the browser)
            if here(to) { webView.load(action.request) } else { NSWorkspace.shared.open(to) }
        }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo) async {
        let alert = NSAlert()
        alert.messageText = message
        await alert.beginSheetModal(for: window)
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo) async -> Bool {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        return await alert.beginSheetModal(for: window) == .alertFirstButtonReturn
    }

    // MARK: Downloads, to the Downloads folder (a name taken gets " 2", " 3"…)

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String) async -> URL? {
        let folder = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        let name = (suggestedFilename as NSString).lastPathComponent
        let stem = ((name.isEmpty ? "download" : name) as NSString).deletingPathExtension
        let ext = (name as NSString).pathExtension
        var to = folder.appendingPathComponent(name.isEmpty ? "download" : name)
        var n = 2
        while FileManager.default.fileExists(atPath: to.path) {
            to = folder.appendingPathComponent(ext.isEmpty ? "\(stem) \(n)" : "\(stem) \(n).\(ext)")
            n += 1
        }
        saving[download] = to
        return to
    }

    func downloadDidFinish(_ download: WKDownload) {
        guard let to = saving.removeValue(forKey: download) else { return }  // the Dock's Downloads bounces
        DistributedNotificationCenter.default().post(name: .init("com.apple.DownloadFileFinished"), object: to.path)
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        saving.removeValue(forKey: download)
        NSAlert(error: error).beginSheetModal(for: window)
    }
}

MainActor.assumeIsolated {  // (the main thread)
    let app = NSApplication.shared
    let delegate = App()  // (the app's delegate is weak: held here while it runs)
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.run()
}
