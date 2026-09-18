// Jarvis.exe — the THIN desktop shell (WebView2 window over the workspace).
//
// A REAL app with its own window (WebView2, the same runtime the old app used)
// that shows the workspace living on Linux. On open it greets you with the SAME
// splash as always —scripts/jarvis-shell-ui.html, rescued verbatim from
// f6ffabd8— with its constellation, its "Enter the workspace" and its dive.
//
// NOTHING native inside: no engine, no terminals of its own, no Rust. The
// lesson from 2026-08-06 is that the mess started when the app tried to be more
// than a window; this is 700 KB that only opens a window.
//
// Contract with the splash (what the UI expects from the shell, as in Tauri):
//   window.__build(n)          build number in the margin
//   window.__estado(msg, err)  progress while the engine is being prepared
//   window.__listo(url)        engine up → the button appears; navigates on its own
//
// Compiled with Windows' stock csc.exe + the WebView2 SDK DLLs EMBEDDED in the
// exe (a single file, no installer):
//   bash scripts/compilar-lanzador-windows.sh
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Pipes;
using System.Net;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

static class Programa
{
    [DllImport("user32.dll")]
    static extern bool SetProcessDpiAwarenessContext(IntPtr valor);

    // WebView profile (cookies, cache): the app's own, not the browser's.
    internal static string CarpetaApp()
    {
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Jarvis");
    }

    // The 2nd instance notifies the 1st via this named signal and exits.
    internal const string SenalMostrar = "JarvisShellMostrar";
    static Mutex unica;

    [STAThread]
    static void Main()
    {
        // SINGLE INSTANCE: with the ✕ sending the app to the tray, the next
        // double click opened a SECOND Jarvis.exe — two watchers stepping on
        // each other's engine recoveries (measured 2026-08-08: paired health
        // checks in the server log). The 2nd instance wakes the 1st and dies.
        bool nueva;
        unica = new Mutex(true, "JarvisShellUnica", out nueva);
        if (!nueva)
        {
            try
            {
                EventWaitHandle ev;
                if (EventWaitHandle.TryOpenExisting(SenalMostrar, out ev)) { ev.Set(); ev.Dispose(); }
            }
            catch { }
            return;
        }

        // The SDK DLLs live NEXT TO the exe, as in any installed app (and as
        // the old app had it). They are NOT embedded nor extracted at runtime:
        // an unsigned exe that spews DLLs to disk is the behavioral signature
        // of a dropper and Defender kills it on heuristics
        // (Trojan:Win32/Sabsik.FL.A!ml, seen 2026-08-06).
        try { SetProcessDpiAwarenessContext((IntPtr)(-4)); } catch { }  // PerMonitorV2

        Application.EnableVisualStyles();
        Correr();
    }

    // Split out and not inlined so the JIT doesn't touch WebView2 types before
    // the AssemblyResolve is registered.
    [MethodImpl(MethodImplOptions.NoInlining)]
    static void Correr() { Application.Run(new VentanaJarvis()); }
}

// Paths and wsl.exe flags for any machine: no hardcoded distro/user/repo.
//   JARVIS_WSL_DIR    Linux path to the clone (default: $HOME/jarvis-workspace)
//   JARVIS_WSL_DISTRO WSL distro name (default: whatever `wsl.exe` uses)
static class JarvisWsl
{
    static string repoLinux;
    static string repoUnc;
    static readonly object candado = new object();

    internal static string DistroFlag()
    {
        string d = Environment.GetEnvironmentVariable("JARVIS_WSL_DISTRO");
        if (string.IsNullOrEmpty(d)) return "";
        return "-d " + d.Trim() + " ";
    }

    // Linux path to the repo. Resolves $HOME via wsl when JARVIS_WSL_DIR is unset.
    internal static string RepoLinux()
    {
        lock (candado)
        {
            if (repoLinux != null) return repoLinux;
            string env = Environment.GetEnvironmentVariable("JARVIS_WSL_DIR");
            if (!string.IsNullOrEmpty(env))
            {
                repoLinux = env.Trim().TrimEnd('/');
                return repoLinux;
            }
            string home = Capturar("printf %s \"$HOME\"").Trim()
                .Replace("\r", "").Replace("\n", "");
            if (string.IsNullOrEmpty(home))
                throw new InvalidOperationException("couldn't resolve $HOME in WSL");
            repoLinux = home + "/jarvis-workspace";
            return repoLinux;
        }
    }

    // UNC via wslpath so we never hardcode \\wsl.localhost\Distro\home\...
    internal static string ArchivoUnc(string relativo)
    {
        lock (candado)
        {
            if (repoUnc == null)
            {
                string linux = RepoLinux();
                string unc = Capturar("wslpath -w " + ShQuote(linux)).Trim()
                    .Replace("\r", "").Replace("\n", "");
                if (string.IsNullOrEmpty(unc))
                    throw new InvalidOperationException("wslpath -w failed for " + linux);
                repoUnc = unc.TrimEnd('\\');
            }
            return repoUnc + "\\" + relativo.Replace('/', '\\');
        }
    }

    internal static string ShQuote(string s)
    {
        return "'" + s.Replace("'", "'\\''") + "'";
    }

    internal static void Ejecutar(string comando, int esperaMs)
    {
        var psi = new ProcessStartInfo();
        psi.FileName = "wsl.exe";
        psi.Arguments = DistroFlag() + "-- bash -lc \"" + comando.Replace("\"", "\\\"") + "\"";
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        using (var p = Process.Start(psi)) p.WaitForExit(esperaMs);
    }

    internal static string Capturar(string comando)
    {
        var psi = new ProcessStartInfo();
        psi.FileName = "wsl.exe";
        psi.Arguments = DistroFlag() + "-- bash -lc \"" + comando.Replace("\"", "\\\"") + "\"";
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        using (var p = Process.Start(psi))
        {
            string salida = p.StandardOutput.ReadToEnd();
            if (!p.WaitForExit(60000)) { try { p.Kill(); } catch { } }
            return salida ?? "";
        }
    }
}

class VentanaJarvis : Form
{
    // Health goes over 127.0.0.1: the NAME localhost resolves ::1 first and
    // WSL doesn't listen there. The workspace DOES open via localhost — the
    // Radio needs that origin for YouTube embeds.
    const string Salud = "http://127.0.0.1:3000/api/health";
    const string Url = "http://localhost:3000";

    // ── Window WITHOUT native frame ──────────────────────────────────────
    // The controls (minimize/maximize/close), dragging and resizing live
    // INSIDE the app: frontend/shell/window-chrome.js draws them on the
    // workspace's own #jw-bar, as in the old app. Here is the native side of
    // that bridge.
    const int WM_NCLBUTTONDOWN = 0x00A1, WM_GETMINMAXINFO = 0x0024;
    const int WM_NCCALCSIZE = 0x0083, WM_ERASEBKGND = 0x0014, WM_EXITSIZEMOVE = 0x0232;
    const int HTCAPTION = 2;
    // Styles FormBorderStyle.None erases and the native gesture needs back.
    const int WS_MINIMIZEBOX = 0x00020000, WS_MAXIMIZEBOX = 0x00010000;
    const int WS_THICKFRAME = 0x00040000, WS_SYSMENU = 0x00080000;
    const int WS_CAPTION = 0x00C00000, WS_CLIPCHILDREN = 0x02000000;

    [DllImport("user32.dll")] static extern bool ReleaseCapture();
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr wp, IntPtr lp);
    [DllImport("user32.dll")] static extern IntPtr MonitorFromWindow(IntPtr h, int flags);
    [DllImport("user32.dll")] static extern bool GetMonitorInfo(IntPtr mon, ref MONITORINFO mi);
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr h, int attr, ref int val, int size);
    // The REAL window state according to Windows. In WM_NCCALCSIZE
    // `WindowState` is useless: that message arrives BEFORE WinForms updates
    // its property, so asking the class returns "Normal" mid-maximization.
    [DllImport("user32.dll")] static extern bool IsZoomed(IntPtr h);

    [StructLayout(LayoutKind.Sequential)] struct RECT { public int left, top, right, bottom; }
    [StructLayout(LayoutKind.Sequential)] struct MONITORINFO
    { public int cbSize; public RECT rcMonitor; public RECT rcWork; public int dwFlags; }
    [StructLayout(LayoutKind.Sequential)] struct POINT { public int x, y; }
    // What Windows sends in WM_NCCALCSIZE: rgrc0 is the rect that, on return,
    // becomes the CLIENT AREA.
    [StructLayout(LayoutKind.Sequential)] struct NCCALCSIZE_PARAMS
    { public RECT rgrc0, rgrc1, rgrc2; public IntPtr lppos; }
    [StructLayout(LayoutKind.Sequential)] struct MINMAXINFO
    { public POINT ptReserved, ptMaxSize, ptMaxPosition, ptMinTrackSize, ptMaxTrackSize; }

    // Zone name → native border that starts the resize (HTLEFT=10 … HTBOTTOMRIGHT=17)
    static int BordeDeZona(string z)
    {
        switch (z)
        {
            case "w": return 10; case "e": return 11; case "n": return 12;
            case "nw": return 13; case "ne": return 14; case "s": return 15;
            case "sw": return 16; case "se": return 17;
        }
        return 0;
    }

    Microsoft.Web.WebView2.WinForms.WebView2 web;
    readonly PresenciaDiscord presencia = new PresenciaDiscord();

    public VentanaJarvis()
    {
        Text = "Jarvis";
        BackColor = Color.FromArgb(8, 7, 13);       // --obs-0 from the splash
        FormBorderStyle = FormBorderStyle.None;     // the chrome is drawn by the app
        // Opens WINDOWED (1440×900 centered), like the old app. It's born
        // unmaximized on purpose: the welcome splash looks better contained
        // than filling the whole screen, and maximizing is one click away on
        // the button the app itself draws. User request, 2026-08-07.
        StartPosition = FormStartPosition.CenterScreen;
        // The size is clamped to the real working area: on a small screen —or
        // with Windows scaling at 125/150%— 1440×900 overflows and the window
        // would again look fullscreen, which is exactly what was meant to be
        // avoided. 90% leaves the desktop visible around it.
        Rectangle area = Screen.PrimaryScreen.WorkingArea;
        Size = new Size(Math.Min(1440, (int)(area.Width * 0.9)),
                        Math.Min(900, (int)(area.Height * 0.9)));
        MinimumSize = new Size(900, 600);

        Stream ico = Assembly.GetExecutingAssembly().GetManifestResourceStream("jarvis.ico");
        if (ico != null) Icon = new Icon(ico);
        MontarBandeja();   // needs the Icon already loaded
        MontarDespertador();

        web = new Microsoft.Web.WebView2.WinForms.WebView2();
        web.Dock = DockStyle.Fill;
        web.DefaultBackgroundColor = Color.FromArgb(8, 7, 13);
        Controls.Add(web);

        AplicarEsquinas();   // rounded when windowed, square when it fills everything

        Resize += delegate { AvisarEstado(); };
        // The maximize bound is recalculated when changing monitors (each one
        // has its working area, and the taskbar may be elsewhere).
        LocationChanged += delegate { AjustarLimiteMaximizado(); };
        Load += delegate { AjustarLimiteMaximizado(); };
        Load += AlCargar;
    }

    // ── Borderless window VISUALLY, but NORMAL to the system ─────────────
    // `FormBorderStyle.None` doesn't just remove the frame: it erases from the
    // HWND the styles Windows uses to decide whether a window can move, restore
    // and snap. Without WS_THICKFRAME|WS_MAXIMIZEBOX, the system's move loop
    // does NOT un-maximize when dragging — which is exactly the gesture this
    // file reimplemented by hand, with two window moves and no threshold.
    //
    // We give them back and the frame is erased VISUALLY in WM_NCCALCSIZE
    // (client area = whole window). To the user it stays borderless; to the OS
    // it's a common window, so it brings in for free: drag-to-restore with its
    // own threshold and position calculation, Aero Snap, shadow and animations.
    // Reference: melak47/BorderlessWindow and Chromium's widget_hwnd_utils.cc.
    protected override CreateParams CreateParams
    {
        get
        {
            CreateParams cp = base.CreateParams;
            cp.Style |= WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX
                      | WS_SYSMENU | WS_CAPTION | WS_CLIPCHILDREN;
            return cp;
        }
    }

    // Borderless, "maximize" covers the taskbar: the size must be pinned to
    // the WORK AREA of the monitor where the window is.
    protected override void WndProc(ref Message m)
    {
        // Client area = whole window: the frame exists for the OS but isn't
        // drawn. That's what keeps the window "borderless" with the native
        // styles in place.
        if (m.Msg == WM_NCCALCSIZE && m.WParam != IntPtr.Zero)
        {
            // MAXIMIZED must be clamped: with WS_THICKFRAME, Windows grows the
            // window by the frame thickness (measured here: 1936×1048 at
            // (-8,-8) for a 1920×1032 working area). If the client copied that
            // rect, the right-anchored icons would end up 8px OUTSIDE the
            // screen and the bottom edge would cover the taskbar. The client is
            // pinned to the working area; the leftover frame stays outside,
            // invisible.
            if (IsZoomed(Handle) && !enFullscreen)
            {
                var ncp = (NCCALCSIZE_PARAMS)Marshal.PtrToStructure(
                    m.LParam, typeof(NCCALCSIZE_PARAMS));
                IntPtr mon2 = MonitorFromWindow(Handle, 2 /* NEAREST */);
                var mi2 = new MONITORINFO();
                mi2.cbSize = Marshal.SizeOf(typeof(MONITORINFO));
                if (GetMonitorInfo(mon2, ref mi2))
                {
                    ncp.rgrc0 = mi2.rcWork;
                    Marshal.StructureToPtr(ncp, m.LParam, false);
                }
            }
            m.Result = IntPtr.Zero;
            return;
        }

        // WebView2 paints the background. Windows erasing it first is an extra
        // pass that shows as a flash when resizing. Returning 1 = "already
        // erased, do nothing" (same as Chromium does in OnEraseBkgnd, with the
        // comment "Needed to prevent resize flicker").
        if (m.Msg == WM_ERASEBKGND)
        {
            m.Result = (IntPtr)1;
            return;
        }

        // Done moving/resizing: the browser may have left :hover stuck on
        // whatever was under the cursor when the window jumped (during the
        // native gesture the page does NOT receive mouse events).
        if (m.Msg == WM_EXITSIZEMOVE) LimpiarHover();

        base.WndProc(ref m);
    }

    // Maximized = EXACTLY the working area of the monitor where the window is.
    // Done with `MaximizedBounds` (the WinForms API, which is respected) and
    // not by writing WM_GETMINMAXINFO by hand: with WS_THICKFRAME, Windows
    // fills that structure grown by the frame thickness and overwrote ours,
    // whether we wrote before or after base.WndProc.
    void AjustarLimiteMaximizado()
    {
        try { MaximizedBounds = Screen.FromHandle(Handle).WorkingArea; }
        catch { }
    }

    // ── Fullscreen ───────────────────────────────────────────────────────
    // Different from "maximized": maximized respects the working area (leaves
    // the taskbar visible, see WM_GETMINMAXINFO); fullscreen covers the ENTIRE
    // monitor. Since the window is already borderless, stretching the bounds is
    // enough — no need to touch native styles.
    bool enFullscreen;
    FormWindowState estadoPreFS = FormWindowState.Normal;
    Rectangle boundsPreFS;

    void AlternarFullscreen()
    {
        if (!enFullscreen)
        {
            // Un-maximize BEFORE stretching: entering fullscreen on top of a
            // maximized window left the taskbar BLACK (a Windows repaint
            // glitch). The state is saved to return.
            estadoPreFS = WindowState;
            if (WindowState == FormWindowState.Maximized) WindowState = FormWindowState.Normal;
            boundsPreFS = Bounds;
            Bounds = Screen.FromHandle(Handle).Bounds;      // the whole monitor
            enFullscreen = true;
        }
        else
        {
            // On exit: first the small bounds and ONLY THEN maximize if that's
            // how it came. The other way around, Windows restores the saved
            // placement and eats the maximize.
            enFullscreen = false;
            Bounds = boundsPreFS;
            WindowState = estadoPreFS;
        }
        AvisarEstado();
    }

    // ── System tray ──────────────────────────────────────────────────────
    // The ✕ does NOT kill the app: it sends it to the tray, alive. From there
    // it's resumed with a click, and the menu's "Close" is the only place that
    // truly shuts down. User request (2026-08-07).
    NotifyIcon bandeja;
    bool cerrandoDeVerdad;

    void MontarBandeja()
    {
        var menu = new ContextMenuStrip();
        menu.Items.Add("Open Jarvis", null, delegate { VolverDeBandeja(); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Close", null, delegate { ApagarTodo(); });

        bandeja = new NotifyIcon();
        bandeja.Icon = Icon;
        bandeja.Text = "Jarvis";
        bandeja.ContextMenuStrip = menu;
        // A click (or double) on the icon resumes; right-click opens the menu.
        bandeja.MouseUp += delegate (object s, MouseEventArgs ev)
        {
            if (ev.Button == MouseButtons.Left) VolverDeBandeja();
        };
        bandeja.Visible = false;
    }

    void IrABandeja()
    {
        // First notify the page, WHILE it can still be executed: it turns off
        // the radio and whatever would waste resources with the window hidden.
        // The terminals keep going: they live in tmux, on the server side.
        try
        {
            if (web != null && web.CoreWebView2 != null)
                web.CoreWebView2.ExecuteScriptAsync("window.__shellOculto && window.__shellOculto(true)");
        }
        catch { }
        if (bandeja != null) bandeja.Visible = true;
        // Just Hide(): a hidden window no longer shows in the taskbar, and
        // touching ShowInTaskbar on the fly makes WinForms RECREATE the handle
        // — with a WebView2 hosted inside that re-parents it and is asking for
        // trouble (measured: the old handle ended up dead).
        Hide();
    }

    void VolverDeBandeja()
    {
        Show();
        if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
        Activate();
        try
        {
            if (web != null && web.CoreWebView2 != null)
                web.CoreWebView2.ExecuteScriptAsync("window.__shellOculto && window.__shellOculto(false)");
        }
        catch { }
    }

    // The 2nd instance (double click with the app already alive, maybe in the
    // tray) doesn't open another window: it sends the named signal and dies —
    // here it's listened for to resume the one that already exists.
    EventWaitHandle despertador;
    void MontarDespertador()
    {
        try { despertador = new EventWaitHandle(false, EventResetMode.AutoReset, Programa.SenalMostrar); }
        catch { return; }
        var t = new Thread(delegate ()
        {
            while (true)
            {
                try { despertador.WaitOne(); } catch { return; }
                try { BeginInvoke((Action)VolverDeBandeja); } catch { return; }
            }
        });
        t.IsBackground = true;
        t.Start();
    }

    // Truly shut down: the app, the server and the distro. The user chose this
    // scope knowing it takes the agents' tmux sessions with it (2026-08-07).
    // The pkill first so uvicorn closes gracefully; the `wsl --shutdown`
    // afterwards sweeps up whatever's left.
    void ApagarTodo()
    {
        cerrandoDeVerdad = true;
        if (bandeja != null) bandeja.Visible = false;
        presencia.Parar();   // so the Discord card doesn't outlive the app
        SoltarAncla();   // so the anchor doesn't fight the --shutdown
        try { Wsl("pkill -f 'uvicorn plotspace' || true", 10000); } catch { }
        try { WslApagar(); } catch { }
        Close();
    }

    static void WslApagar()
    {
        var psi = new ProcessStartInfo();
        psi.FileName = "wsl.exe";
        psi.Arguments = "--shutdown";
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        using (var p = Process.Start(psi)) p.WaitForExit(20000);
    }

    // Alt+F4 and any other system close also go to the tray: the only path
    // that ends the process is the menu's "Close".
    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (!cerrandoDeVerdad && e.CloseReason == CloseReason.UserClosing)
        {
            e.Cancel = true;
            IrABandeja();
            return;
        }
        if (bandeja != null) { bandeja.Visible = false; bandeja.Dispose(); }
        base.OnFormClosing(e);
    }

    // On releasing the native gesture, the browser's :hover may have gotten
    // stuck on the button that was under the cursor when the window resized:
    // during Windows' move loop the page does NOT receive mouse events, so it
    // never finds out the cursor is no longer there.
    void LimpiarHover()
    {
        if (web == null || web.CoreWebView2 == null) return;
        try { web.CoreWebView2.ExecuteScriptAsync("window.__shellFinArrastre && window.__shellFinArrastre()"); }
        catch { }
    }

    // Windows 11 rounded corners: ONLY when windowed. Filling the whole screen
    // they must be squared — the rounding leaves four gaps through which the
    // DESKTOP shows, and with that it stops feeling fullscreen (reported by the
    // user, 2026-08-07). 1 = DONOTROUND · 2 = ROUND.
    void AplicarEsquinas()
    {
        int pref = (enFullscreen || WindowState == FormWindowState.Maximized) ? 1 : 2;
        try { DwmSetWindowAttribute(Handle, 33, ref pref, 4); } catch { }
    }

    // Last state reported to the frontend, to avoid spamming ExecuteScriptAsync
    // on every tick of a mouse resize: we only talk when something CHANGES.
    string ultimoEstado = "";

    void AvisarEstado()
    {
        string maxi = (WindowState == FormWindowState.Maximized) ? "true" : "false";
        string fs = enFullscreen ? "true" : "false";
        string estado = maxi + "|" + fs;
        if (estado == ultimoEstado) return;
        ultimoEstado = estado;
        AplicarEsquinas();
        if (web == null || web.CoreWebView2 == null) return;
        try
        {
            web.CoreWebView2.ExecuteScriptAsync(
                "window.__shellEstado && window.__shellEstado(" + maxi + "," + fs + ")");
            // compat with the old chrome contract
            web.CoreWebView2.ExecuteScriptAsync(
                "window.__shellMaximizado && window.__shellMaximizado(" + maxi + ")");
        }
        catch { }
    }


    // UI requests: move, resize and the three buttons.
    void AlMensaje(object o, Microsoft.Web.WebView2.Core.CoreWebView2WebMessageReceivedEventArgs e)
    {
        string m;
        try { m = e.TryGetWebMessageAsString(); } catch { return; }
        if (m == null) return;

        // Move: only forbidden in FULLSCREEN (moving it knocks it out of place).
        // Maximized is NOT touched here: with the native styles in place,
        // Windows' move loop un-maximizes it ON ITS OWN —with its drag
        // threshold, so a simple click no longer shrinks it— and calculates the
        // position itself. Doing it by hand was the cause of the flicker and
        // the cursor landing on an icon.
        //
        // The REAL cursor coordinates go in lParam: that's what the
        // WM_NCLBUTTONDOWN doc expects, and it avoids the jump from the
        // milliseconds the message takes to travel from JS to here.
        if (m == "drag")
        {
            if (enFullscreen) return;
            Point pc = Cursor.Position;
            IntPtr lp = (IntPtr)((pc.Y << 16) | (pc.X & 0xFFFF));
            ReleaseCapture();
            SendMessage(Handle, WM_NCLBUTTONDOWN, (IntPtr)HTCAPTION, lp);
        }
        else if (m.StartsWith("resize:"))
        {
            if (WindowState == FormWindowState.Maximized || enFullscreen) return;
            int borde = BordeDeZona(m.Substring(7));
            if (borde == 0) return;
            ReleaseCapture();
            SendMessage(Handle, WM_NCLBUTTONDOWN, (IntPtr)borde, IntPtr.Zero);
        }
        else if (m == "min") { WindowState = FormWindowState.Minimized; }
        else if (m == "max")
        {
            // "Smart" maximize, like the old app: being in fullscreen the
            // button EXITS it and leaves the window small (restored), not
            // maximized. Outside fullscreen, normal toggle.
            if (enFullscreen)
            {
                estadoPreFS = FormWindowState.Normal;
                AlternarFullscreen();
            }
            else
            {
                WindowState = WindowState == FormWindowState.Maximized
                    ? FormWindowState.Normal : FormWindowState.Maximized;
                AvisarEstado();
            }
        }
        else if (m == "fullscreen") { AlternarFullscreen(); }
        else if (m == "close") { IrABandeja(); }
    }

    // Bridge that EVERY page loaded in the app sees (splash and workspace): the
    // frontend talks to the shell through here and in a normal browser it
    // doesn't exist, so window-chrome.js turns itself off.
    const string Puente =
        "(function(){var wv=window.chrome&&window.chrome.webview;if(!wv)return;" +
        "window.__shell={min:function(){wv.postMessage('min')}," +
        "max:function(){wv.postMessage('max')},close:function(){wv.postMessage('close')}," +
        "drag:function(){wv.postMessage('drag')}," +
        "fullscreen:function(){wv.postMessage('fullscreen')}," +
        "resize:function(d){wv.postMessage('resize:'+d)}};" +
        // the splash brings its own invisible drag bar (.titlebar)
        "document.addEventListener('mousedown',function(e){if(e.button)return;" +
        "var t=e.target;if(t&&t.closest&&t.closest('.titlebar'))window.__shell.drag()});" +
        "document.addEventListener('dblclick',function(e){var t=e.target;" +
        "if(t&&t.closest&&t.closest('.titlebar'))window.__shell.max()});})()";

    async void AlCargar(object o, EventArgs e)
    {
        string datos = Path.Combine(Programa.CarpetaApp(), "WebView2");
        var entorno = await Microsoft.Web.WebView2.Core.CoreWebView2Environment
            .CreateAsync(null, datos, null);
        await web.EnsureCoreWebView2Async(entorno);

        var s = web.CoreWebView2.Settings;
        s.AreDefaultContextMenusEnabled = false;   // browser menu: no, this is an app
        s.IsStatusBarEnabled = false;
        // BROWSER accelerators are turned off: this is an app, not a tab. The
        // one that really bothered was F11 — WebView2 took it for ITS own
        // content fullscreen (which, with the window already borderless, changes
        // nothing visually) and competed with ours: you had to press it twice
        // for the WINDOW to go fullscreen. Turning them off doesn't touch DOM
        // events, so the workspace shortcuts (Ctrl+P, Ctrl+K, F11) still reach
        // the JS as always.
        s.AreBrowserAcceleratorKeysEnabled = false;

        web.CoreWebView2.WebMessageReceived += AlMensaje;
        await web.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(Puente);

        // Anchor BEFORE reading the repo / checking the engine: if the distro is
        // cold, the anchor already boots it (and unblocks wslpath / $HOME); if
        // it's warm, it prevents it from shutting down in the middle of the check.
        AnclarDistro();


        web.CoreWebView2.NavigateToString(Splash());
        await Task.Delay(150);                     // so the splash's <script> exists

        string build = Build();
        if (build != null) await Js("window.__build && window.__build(" + build + ")");

        if (!await Task.Run((Func<bool>)Responde) && !await LevantarYEsperar()) return;

        // Engine up: the splash completes the constellation and shows the button.
        // From there on the UI leads — the user enters whenever they want.
        await Js("window.__listo && window.__listo('" + Url + "')");
        IniciarVigilancia();
        presencia.Iniciar();   // "Playing Jarvis" on Discord (if Discord is running)
    }

    // ── Distro life anchor ───────────────────────────────────────────────
    // WSL shuts down the ENTIRE distro ~60s after its last wsl.exe client
    // disconnects. The engine this app launches ends up setsid'd (it is NOT a
    // client) and the wsl.exe that launch it exit immediately: without an
    // anchor, WSL buried the HEALTHY server every ~85s (journal 2026-08-08:
    // "The system will power off now!" in series, matching each watcher
    // relaunch) and the watcher revived it into the same trap — the eternal
    // loop of "The server is restarting". A sleeping wsl.exe client, alive as
    // long as the app lives, holds the distro (and the agents' tmux sessions).
    // Same trick as wsl-vpnkit. It dies on its own with the app (child) or with
    // SoltarAncla().
    Process ancla;

    void AnclarDistro()
    {
        try { if (ancla != null && !ancla.HasExited) return; } catch { }
        var psi = new ProcessStartInfo();
        psi.FileName = "wsl.exe";
        psi.Arguments = JarvisWsl.DistroFlag() + "--exec sleep infinity";
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        try { ancla = Process.Start(psi); } catch { ancla = null; }
    }

    void SoltarAncla()
    {
        try { if (ancla != null && !ancla.HasExited) ancla.Kill(); } catch { }
        ancla = null;
    }

    // Starts the engine and waits for it to respond, with TWO real attempts.
    // The previous one launched only once and then just watched the clock for
    // 90s: if that single attempt was lost (cold distro that took longer than
    // the warm-up), there was no second chance, only the error banner.
    async Task<bool> LevantarYEsperar()
    {
        for (int intento = 1; intento <= 2; intento++)
        {
            string rotulo = intento == 1 ? "Starting the engine in WSL… "
                                         : "The engine is slow — retrying… ";
            await Estado(rotulo.TrimEnd(' ', '…') + "…", false);
            await Task.Run((Action)LevantarServer);
            for (int i = 0; i < 60; i++)
            {
                if (await Task.Run((Func<bool>)Responde)) return true;
                await Estado(rotulo + (i + 1) + "s", false);
                await Task.Delay(1000);
            }
        }
        await Estado("Couldn't start the engine after two attempts.\n\n" +
            "Clone the repo in WSL to ~/jarvis-workspace (or set JARVIS_WSL_DIR).\n" +
            "Try manually:\n  bash ~/jarvis-workspace/scripts/reiniciar-server.sh\n\n" +
            "(log: ~/jarvis-workspace/data/lanzador.log)", true);
        return false;
    }

    // ── Engine watchdog ──────────────────────────────────────────────────
    // The startup check was ONE-OFF. If the engine went down with the app
    // ALREADY open (WSL crashed, the distro shut off, Windows restarted), the
    // window would stay forever on the "The server is restarting" screen the
    // frontend draws, waiting for a boot_id that was never going to arrive
    // because there was nobody on the other side. This timer is the way back:
    // it detects the crash and restarts the engine on its own.
    System.Windows.Forms.Timer vigia;
    bool recuperando;
    int fallos;

    void IniciarVigilancia()
    {
        if (vigia != null) return;
        vigia = new System.Windows.Forms.Timer();
        vigia.Interval = 5000;
        vigia.Tick += Vigilar;
        vigia.Start();
    }

    async void Vigilar(object o, EventArgs e)
    {
        if (recuperando || web == null || web.CoreWebView2 == null) return;
        AnclarDistro();   // if someone shut the distro down by hand, hold it again
        if (await Task.Run((Func<bool>)Responde)) { fallos = 0; return; }

        // 6 failures in a row (~30s) before lifting a finger. The threshold is
        // NOT paranoia: "Update now" restarts the server in-place (os.execv)
        // and leaves it down for several seconds. With a short threshold the
        // watcher got in the middle of a normal update, threw the splash on top
        // and called reiniciar-server.sh, which on finding the server ALREADY
        // back asked for ANOTHER restart. A real crash (WSL went away) still
        // recovers on its own in half a minute, which is what matters.
        if (++fallos < 6) return;

        // recuperando is set HERE, before the slow checks: the WSL one takes up
        // to ~8s and with the flag off another timer tick ran in parallel and
        // could fire a SECOND recovery on top.
        recuperando = true;
        try
        {
            // Last chance before intervening: if it just came back, we do nothing.
            if (await Task.Run((Func<bool>)Responde)) { fallos = 0; return; }
            // Second opinion from INSIDE the distro: if the server is healthy
            // and the broken thing is the Windows↔WSL bridge (proxy, Hyper-V
            // firewall, localhost IPv6), relaunching fixes nothing — and
            // bothering a live engine is exactly the bug this watchdog must not
            // cause.
            if (await Task.Run((Func<bool>)RespondeDesdeWsl)) { fallos = 0; return; }

            bool estaba = EnWorkspace();      // had they entered, or is it still on the splash?
            web.CoreWebView2.NavigateToString(Splash());
            await Task.Delay(150);
            string build = Build();
            if (build != null) await Js("window.__build && window.__build(" + build + ")");
            await Estado("The engine went down — starting it…", false);

            if (await LevantarYEsperar())
            {
                fallos = 0;
                if (estaba) web.CoreWebView2.Navigate(Url);   // return it where it was
                else await Js("window.__listo && window.__listo('" + Url + "')");
            }
        }
        catch { }
        finally { recuperando = false; }
    }

    // With NavigateToString the Source stays at about:blank; the workspace is http.
    bool EnWorkspace()
    {
        try { var u = web.Source; return u != null && u.Scheme.StartsWith("http"); }
        catch { return false; }
    }

    Task<string> Js(string codigo) { return web.CoreWebView2.ExecuteScriptAsync(codigo); }

    Task<string> Estado(string texto, bool esError)
    {
        return Js("window.__estado && window.__estado(decodeURIComponent('" +
                  Uri.EscapeDataString(texto) + "')," + (esError ? "true" : "false") + ")");
    }

    bool Responde()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(Salud);
            req.Proxy = null;       // no .NET proxy auto-detect: it's 127.0.0.1
            // 3s and not 1.5: the box suffers measured CPU starvation — under
            // load a healthy health check can take longer than 1.5s and counted
            // as a crash.
            req.Timeout = 3000;
            req.ReadWriteTimeout = 3000;
            using (var resp = (HttpWebResponse)req.GetResponse())
                return (int)resp.StatusCode == 200;
        }
        catch { return false; }
    }

    // Second opinion from INSIDE the distro: curl to the same endpoint. If this
    // returns 200, the engine is alive and the broken thing is the Windows↔WSL
    // bridge — relaunching fixes nothing. stdout captured (the common Wsl()
    // drops it).
    bool RespondeDesdeWsl()
    {
        try
        {
            var psi = new ProcessStartInfo();
            psi.FileName = "wsl.exe";
            psi.Arguments = JarvisWsl.DistroFlag() + "-- bash -lc \"curl -s -o /dev/null " +
                "-w '%{http_code}' --max-time 3 http://127.0.0.1:3000/api/health\"";
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            using (var p = Process.Start(psi))
            {
                string salida = p.StandardOutput.ReadToEnd();
                if (!p.WaitForExit(8000)) { try { p.Kill(); } catch { } return false; }
                return salida.Trim() == "200";
            }
        }
        catch { return false; }
    }

    // Starts the engine inside WSL. Two precautions that aren't decorative:
    //
    // 1) The distro may be COLD (fully off: Windows restarted, `wsl --shutdown`
    //    was run). Then the first `wsl.exe` starts nothing: all its time goes
    //    into booting the distro. Hence the separate warm-up, with its own long
    //    wait, BEFORE the command that matters.
    // 2) Everything is in try/catch. If `wsl.exe` isn't in the PATH or the
    //    distro doesn't respond, `Process.Start` THROWS — and before, that
    //    exception bubbled up unowned from Task.Run, leaving the window stuck
    //    on "Starting the engine…" without ever saying why.
    //
    // The log does NOT go to /tmp: in WSL /tmp is tmpfs and is wiped on EVERY
    // distro boot — exactly the case you need to debug. It goes to the repo's
    // data/ (gitignored), and in append mode to keep the earlier attempts.
    void LevantarServer()
    {
        try { Wsl("true", 90000); } catch { }          // wake the cold distro
        try
        {
            // JARVIS_WSL_DIR from Windows is visible inside WSL; otherwise $HOME/jarvis-workspace.
            Wsl("REPO=${JARVIS_WSL_DIR:-$HOME/jarvis-workspace}; " +
                "cd $REPO && mkdir -p data && { date '+== %F %T lanzado por Jarvis.exe'; } " +
                ">>data/lanzador.log 2>&1; cd $REPO && setsid nohup bash " +
                "scripts/reiniciar-server.sh >>data/lanzador.log 2>&1 </dev/null & exit 0", 20000);
        }
        catch { }
    }

    static void Wsl(string comando, int esperaMs)
    {
        JarvisWsl.Ejecutar(comando, esperaMs);
    }

    // The build number comes from the repo's VERSION, read through the WSL
    // share. If it can't be done (distro asleep, path moved) the margin stays
    // without a badge: the splash is born with that block hidden.
    static string Build()
    {
        try
        {
            string v = File.ReadAllText(JarvisWsl.ArchivoUnc("VERSION")).Trim();
            int corte = v.LastIndexOf('.');
            string n = corte > 0 ? v.Substring(corte + 1) : v;
            int _;
            return int.TryParse(n, out _) ? n : null;
        }
        catch { return null; }
    }

    static string Splash()
    {
        using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("ui.html"))
        using (var r = new StreamReader(s, Encoding.UTF8))
            return r.ReadToEnd();
    }
}

// ── Discord Rich Presence ("Playing Jarvis") ─────────────────────────────────
// Port of the old app's presence.rs (5a0bccaf, deleted in dc707f4f): Discord's
// pipe (discord-ipc-N) lives in Windows and WSL can't reach it, so the IPC
// client runs here, in the launcher. The backend is polled
// (GET /api/system/presence, which builds bilingual details/state + agent
// count) every ~15s and the Activity is pushed. The "how long" timer is set by
// the app start (inicioEpoch), stable across updates. If Discord isn't running,
// it retries silently: it NEVER breaks the shell.
class PresenciaDiscord
{
    // The "Jarvis" App from the Discord Developer Portal: from there come the
    // visible name ("Jarvis") and the art assets (the icon). Without that App,
    // Discord shows nothing. The asset keys are decided by the backend
    // (large_image / small_image in the JSON) → they are not hardcoded here.
    const string AppId = "1524623263798525982";

    // Discord throttles updates (~1 every 15s). With 15s we're just right and
    // without spamming; also set_activity is only sent if something CHANGED.
    const int PollMs = 15000;
    const string UrlPresence = "http://127.0.0.1:3000/api/system/presence";

    Thread hilo;
    volatile bool parar;
    NamedPipeClientStream pipe;
    string ultimaFirma;   // signature of the last send: don't burn the rate limit
    long inicioEpoch;
    readonly JavaScriptSerializer json = new JavaScriptSerializer();

    public void Iniciar()
    {
        if (hilo != null) return;
        inicioEpoch = (long)(DateTime.UtcNow -
            new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
        hilo = new Thread(Bucle);
        hilo.IsBackground = true;
        hilo.Start();
    }

    public void Parar()
    {
        parar = true;
        CerrarPipe();
    }

    void Bucle()
    {
        while (!parar)
        {
            try { Tick(); }
            catch { CerrarPipe(); }   // any hiccup → reconnect next time
            Thread.Sleep(PollMs);
        }
    }

    void Tick()
    {
        Dictionary<string, object> datos = TraerPresence();
        if (datos == null) return;                 // engine down: nothing to show
        if (pipe == null && !Conectar()) return;   // Discord closed: silence

        string firma = Campo(datos, "details") + "|" + Campo(datos, "state") + "|" +
                       Campo(datos, "large_image") + "|" + Campo(datos, "small_image") + "|" +
                       Campo(datos, "small_text");
        if (firma == ultimaFirma) return;
        if (MandarActividad(datos)) { ultimaFirma = firma; }
        else { CerrarPipe(); ultimaFirma = null; } // Discord restarted: reconnect
    }

    Dictionary<string, object> TraerPresence()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(UrlPresence);
            req.Proxy = null;
            req.Timeout = 3000;
            req.ReadWriteTimeout = 3000;
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var r = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
            {
                return json.Deserialize<Dictionary<string, object>>(r.ReadToEnd());
            }
        }
        catch (WebException)
        {
            return null;
        }
        catch { return null; }
    }

    // Discord listens on discord-ipc-0…9 (several clients = several slots).
    bool Conectar()
    {
        for (int i = 0; i < 10; i++)
        {
            var p = new NamedPipeClientStream(".", "discord-ipc-" + i, PipeDirection.InOut);
            try { p.Connect(200); }
            catch { p.Dispose(); continue; }
            pipe = p;
            try
            {
                Mandar(0, "{\"v\":1,\"client_id\":\"" + AppId + "\"}");  // handshake
                if (LeerFrame(2000) != null) return true;                // READY
            }
            catch { }
            CerrarPipe();
        }
        return false;
    }

    bool MandarActividad(Dictionary<string, object> datos)
    {
        var actividad = new Dictionary<string, object>();
        actividad["details"] = Campo(datos, "details");
        actividad["state"] = Campo(datos, "state");
        var tiempos = new Dictionary<string, object>();
        tiempos["start"] = inicioEpoch;
        actividad["timestamps"] = tiempos;
        var assets = new Dictionary<string, object>();
        assets["large_image"] = Campo(datos, "large_image");
        string hover = Campo(datos, "large_text");
        assets["large_text"] = hover.Length > 0 ? hover : "Jarvis";
        // The status dot (small_*) is optional: the backend sends "" when the
        // dot assets weren't uploaded — then it's not added.
        string punto = Campo(datos, "small_image");
        if (punto.Length > 0)
        {
            assets["small_image"] = punto;
            assets["small_text"] = Campo(datos, "small_text");
        }
        actividad["assets"] = assets;

        var args = new Dictionary<string, object>();
        args["pid"] = Process.GetCurrentProcess().Id;
        args["activity"] = actividad;
        var msg = new Dictionary<string, object>();
        msg["cmd"] = "SET_ACTIVITY";
        msg["args"] = args;
        msg["nonce"] = Guid.NewGuid().ToString();

        try
        {
            Mandar(1, json.Serialize(msg));
            LeerFrame(2000);   // the response is drained and discarded (don't fill the pipe)
            return true;
        }
        catch { return false; }
    }

    // IPC frame: int32 LE opcode + int32 LE length + UTF-8 JSON.
    void Mandar(int op, string cuerpo)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(cuerpo);
        byte[] frame = new byte[8 + bytes.Length];
        BitConverter.GetBytes(op).CopyTo(frame, 0);
        BitConverter.GetBytes(bytes.Length).CopyTo(frame, 4);
        bytes.CopyTo(frame, 8);
        pipe.Write(frame, 0, frame.Length);
        pipe.Flush();
    }

    string LeerFrame(int esperaMs)
    {
        byte[] cab = LeerExacto(8, esperaMs);
        if (cab == null) return null;
        int largo = BitConverter.ToInt32(cab, 4);
        if (largo < 0 || largo > 65536) return null;
        byte[] cuerpo = LeerExacto(largo, esperaMs);
        return cuerpo == null ? null : Encoding.UTF8.GetString(cuerpo);
    }

    // Read with a real timeout: PipeStream doesn't support ReadTimeout, so it
    // waits on ReadAsync — a mute Discord can't hang the thread forever.
    byte[] LeerExacto(int n, int esperaMs)
    {
        byte[] buf = new byte[n];
        int leidos = 0;
        while (leidos < n)
        {
            Task<int> t;
            try { t = pipe.ReadAsync(buf, leidos, n - leidos); }
            catch { return null; }
            if (!t.Wait(esperaMs)) return null;
            if (t.Result <= 0) return null;
            leidos += t.Result;
        }
        return buf;
    }

    void CerrarPipe()
    {
        try { if (pipe != null) pipe.Dispose(); } catch { }
        pipe = null;
    }

    static string Campo(Dictionary<string, object> d, string k)
    {
        object v;
        return (d != null && d.TryGetValue(k, out v) && v != null) ? v.ToString() : "";
    }
}
