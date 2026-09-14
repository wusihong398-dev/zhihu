using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace TOTOD.WebView2Publisher;

internal record Account(string id, string display_name, string answer_publish_mode);
internal record PublishTask(string id, string account_id, string question_title, string question_url, string content);
internal record Result(bool success, string? published_url = null, string? error_message = null);

internal sealed class MainForm : Form
{
    const string AppVersion = "0.18.2";
    readonly string appDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "TOTODWebView2Publisher");
    HttpClient http = new() { Timeout = TimeSpan.FromSeconds(40) };
    readonly TextBox server = new() { Text = "https://totod.cn", Width = 230 };
    readonly TextBox token = new() { PasswordChar = '●', Width = 320 };
    readonly ComboBox accounts = new() { DropDownStyle = ComboBoxStyle.DropDownList, Width = 350 };
    readonly Button connect = new() { Text = "保存并连接" };
    readonly Button login = new() { Text = "登录并自动监听", AutoSize = true };
    readonly Button stop = new() { Text = "停止监听", Enabled = false, AutoSize = true };
    readonly Label status = new() { AutoSize = true, ForeColor = Color.SeaGreen, Text = "请先连接服务器" };
    readonly TextBox log = new() { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Bottom, Height = 125 };
    readonly Panel browserHost = new() { Dock = DockStyle.Fill };
    WebView2 browser = new() { Dock = DockStyle.Fill };
    readonly System.Windows.Forms.Timer poll = new() { Interval = 1800 };
    List<Account> accountItems = [];
    string activeAccount = "";
    string browserAccount = "";
    bool busy;

    public MainForm()
    {
        Text = $"TOTOD Edge 内嵌发布客户端 v{AppVersion}";
        Width = 1280; Height = 860;
        Directory.CreateDirectory(appDir);
        var top = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 94, Padding = new Padding(8), AutoSize = false };
        top.Controls.AddRange([new Label { Text = "服务器", AutoSize = true, Margin = new Padding(3, 8, 3, 3) }, server,
            new Label { Text = "设备密钥", AutoSize = true, Margin = new Padding(12, 8, 3, 3) }, token, connect,
            new Label { Text = "知乎账号", AutoSize = true, Margin = new Padding(3, 13, 3, 3) }, accounts, login, stop, status]);
        browserHost.Controls.Add(browser);
        Controls.Add(browserHost); Controls.Add(log); Controls.Add(top);
        connect.Click += async (_, _) => await ConnectAsync();
        login.Click += async (_, _) => await LoginAsync();
        stop.Click += (_, _) => StopListening("已停止监听");
        poll.Tick += async (_, _) => await PollAsync();
        LoadConfig();
    }

    void LoadConfig()
    {
        try {
            var newConfig = Path.Combine(appDir, "config.json");
            var oldConfig = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "TOTODPublisher", "config.json");
            var configPath = File.Exists(newConfig) ? newConfig : oldConfig;
            var cfg = JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(configPath))!;
            server.Text = cfg.GetValueOrDefault("server", server.Text); token.Text = cfg.GetValueOrDefault("token", "");
            if (token.Text.Length > 0) Shown += async (_, _) => await ConnectAsync();
        } catch { }
    }

    void ConfigureRequest()
    {
        http.Dispose();
        http = new HttpClient { Timeout = TimeSpan.FromSeconds(40) };
        http.BaseAddress = new Uri(server.Text.Trim().TrimEnd('/') + "/api/");
        http.DefaultRequestHeaders.Add("X-TOTOD-Device-Token", token.Text.Trim());
        http.DefaultRequestHeaders.UserAgent.ParseAdd($"TOTOD-WebView2-Publisher/{AppVersion}");
    }

    async Task ConnectAsync()
    {
        try {
            ConfigureRequest();
            Directory.CreateDirectory(appDir);
            File.WriteAllText(Path.Combine(appDir, "config.json"), JsonSerializer.Serialize(new { server = server.Text.Trim(), token = token.Text.Trim() }));
            accountItems = await http.GetFromJsonAsync<List<Account>>("local-publisher/client/accounts") ?? [];
            accounts.DataSource = accountItems; accounts.DisplayMember = nameof(Account.display_name);
            status.Text = $"已连接，共 {accountItems.Count} 个账号"; AddLog(status.Text);
        } catch (Exception ex) { Fail("连接失败：" + ex.Message); }
    }

    Account? Selected() => accounts.SelectedItem as Account;

    async Task LoginAsync()
    {
        var account = Selected();
        if (account is null) { MessageBox.Show("请先选择知乎账号"); return; }
        if (account.answer_publish_mode != "local") { MessageBox.Show("该账号尚未设置为 Windows 本地发布"); return; }
        try {
            poll.Stop(); activeAccount = account.id;
            if (browser.CoreWebView2 is not null && browserAccount != account.id) {
                browserHost.Controls.Remove(browser);
                browser.Dispose();
                browser = new WebView2 { Dock = DockStyle.Fill };
                browserHost.Controls.Add(browser);
            }
            if (browser.CoreWebView2 is null) {
                var profile = Path.Combine(appDir, "profiles", account.id);
                Directory.CreateDirectory(profile);
                var env = await CoreWebView2Environment.CreateAsync(null, profile);
                await browser.EnsureCoreWebView2Async(env);
            }
            browserAccount = account.id;
            browser.CoreWebView2.Settings.AreDevToolsEnabled = false;
            browser.CoreWebView2.Navigate("https://www.zhihu.com/");
            status.Text = "请在内嵌 Edge 中登录知乎；检测到登录后自动监听";
            AddLog($"已打开账号：{account.display_name}");
            poll.Start(); stop.Enabled = true; login.Enabled = false;
        } catch (Exception ex) { Fail("内嵌 Edge 启动失败：" + ex.Message); }
    }

    async Task<bool> LoggedInAsync()
    {
        if (browser.CoreWebView2 is null) return false;
        var cookies = await browser.CoreWebView2.CookieManager.GetCookiesAsync("https://www.zhihu.com/");
        return cookies.Any(c => c.Name == "z_c0");
    }

    async Task PollAsync()
    {
        if (busy || string.IsNullOrEmpty(activeAccount) || browser.CoreWebView2 is null) return;
        busy = true;
        try {
            if (!await LoggedInAsync()) { status.Text = "等待知乎登录…"; return; }
            status.Text = "运行中：正在等待发布任务";
            var task = await http.PostAsync($"local-publisher/client/tasks/claim?account_id={Uri.EscapeDataString(activeAccount)}", null);
            task.EnsureSuccessStatusCode();
            var item = await task.Content.ReadFromJsonAsync<PublishTask>();
            if (item is null) return;
            AddLog("领取任务：" + item.question_title); status.Text = "正在发布：" + item.question_title;
            Result result;
            try { result = new(true, await PublishAsync(item)); }
            catch (Exception ex) { result = new(false, error_message: ex.Message); AddLog("发布失败：" + ex.Message); }
            var response = await http.PostAsJsonAsync($"local-publisher/client/tasks/{item.id}/result", result);
            response.EnsureSuccessStatusCode();
            if (!result.success && (result.error_message?.Contains("40362") == true || result.error_message?.Contains("10001") == true))
                StopListening("已停止：知乎拒绝当前 WebView2 会话");
        } catch (Exception ex) { Fail("任务请求失败：" + ex.Message); }
        finally { busy = false; }
    }

    Task WaitNavigationAsync()
    {
        var tcs = new TaskCompletionSource<bool>();
        void Done(object? s, CoreWebView2NavigationCompletedEventArgs e) { browser.NavigationCompleted -= Done; tcs.TrySetResult(true); }
        browser.NavigationCompleted += Done;
        return tcs.Task.WaitAsync(TimeSpan.FromSeconds(60));
    }

    async Task<string> ScriptStringAsync(string script)
    {
        var raw = await browser.CoreWebView2.ExecuteScriptAsync(script);
        return JsonSerializer.Deserialize<string>(raw) ?? "";
    }

    async Task<string> PublishAsync(PublishTask task)
    {
        var nav = WaitNavigationAsync(); browser.CoreWebView2.Navigate(task.question_url); await nav; await Task.Delay(1800);
        var body = await ScriptStringAsync("document.body?.innerText || ''");
        if (body.Contains("40362") || body.Contains("暂时限制本次访问")) throw new Exception("知乎风控 40362：WebView2 会话被限制");
        var content = JsonSerializer.Serialize(task.content);
        var openResult = await ScriptStringAsync("""
        (() => {
          const visible = e => {
            if (!e) return false;
            const s = getComputedStyle(e), r = e.getBoundingClientRect();
            return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0 && r.width > 2 && r.height > 2;
          };
          const text = e => (e.innerText || e.textContent || e.getAttribute('aria-label') || '').replace(/\s+/g, '');
          const editorSelectors = [
            '.AnswerForm-editor .ProseMirror[contenteditable="true"]',
            '.AnswerForm .ProseMirror[contenteditable="true"]',
            '.AnswerForm [contenteditable="true"][role="textbox"]',
            '.AnswerForm [contenteditable="true"]'
          ];
          if (editorSelectors.some(s => [...document.querySelectorAll(s)].some(visible))) return 'editor-ready';
          window.scrollTo({top: 0, behavior: 'instant'});
          const labels = ['写回答', '添加回答', '回答问题', '参与回答'];
          const candidates = [...document.querySelectorAll('button,[role="button"],a')].filter(visible);
          const button = candidates.find(e => labels.includes(text(e))) ||
                         candidates.find(e => labels.some(label => text(e).includes(label)));
          if (!button) return 'write-missing';
          if (button.disabled || button.getAttribute('aria-disabled') === 'true') return 'write-disabled';
          button.scrollIntoView({block: 'center', inline: 'center'});
          button.click();
          return 'write-clicked';
        })()
        """);
        if (openResult == "write-missing") throw new Exception("未找到页面上可见的“写回答”按钮");
        if (openResult == "write-disabled") throw new Exception("页面上的“写回答”按钮不可用，账号可能已回答或没有回答权限");
        if (openResult == "write-clicked") AddLog("已点击“写回答”，正在等待回答编辑器");

        var fill = "missing";
        for (var i = 0; i < 30 && fill != "filled"; i++) {
            if (i > 0) await Task.Delay(400);
            fill = await ScriptStringAsync("""
            (() => {
              const visible = e => {
                if (!e) return false;
                const s = getComputedStyle(e), r = e.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0 && r.width > 20 && r.height > 20;
              };
              const selectors = [
                '.AnswerForm-editor .ProseMirror[contenteditable="true"]',
                '.AnswerForm .ProseMirror[contenteditable="true"]',
                '.AnswerForm [contenteditable="true"][role="textbox"]',
                '.AnswerForm [contenteditable="true"]'
              ];
              let editor = selectors.flatMap(s => [...document.querySelectorAll(s)]).find(visible);
              if (!editor) return 'missing';
              editor.scrollIntoView({block: 'center'});
              editor.focus();
              const selection = window.getSelection(), range = document.createRange();
              range.selectNodeContents(editor); selection.removeAllRanges(); selection.addRange(range);
              let inserted = false;
              try { inserted = document.execCommand('insertText', false, CONTENT); } catch (_) {}
              if (!inserted || !(editor.innerText || '').trim()) editor.innerText = CONTENT;
              editor.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: CONTENT}));
              editor.dispatchEvent(new Event('change', {bubbles: true}));
              return (editor.innerText || '').trim() ? 'filled' : 'empty';
            })()
            """.Replace("CONTENT", content));
        }
        if (fill != "filled") throw new Exception("已点击“写回答”，但知乎回答编辑器没有正常打开");
        AddLog("回答内容已填写，正在点击“发布回答”");
        await Task.Delay(1200);

        var questionMatch = System.Text.RegularExpressions.Regex.Match(task.question_url, @"/question/(\d+)");
        var questionId = questionMatch.Success ? questionMatch.Groups[1].Value : "";
        var publishResponse = new TaskCompletionSource<(int Status, string Body)>(TaskCreationOptions.RunContinuationsAsynchronously);
        async void CapturePublishResponse(object? sender, CoreWebView2WebResourceResponseReceivedEventArgs args)
        {
            if (!string.Equals(args.Request.Method, "POST", StringComparison.OrdinalIgnoreCase) ||
                !args.Request.Uri.Contains("/answers", StringComparison.OrdinalIgnoreCase)) return;
            try {
                using var stream = await args.Response.GetContentAsync();
                using var reader = new StreamReader(stream);
                publishResponse.TrySetResult((args.Response.StatusCode, await reader.ReadToEndAsync()));
            } catch { }
        }
        browser.CoreWebView2.WebResourceResponseReceived += CapturePublishResponse;
        try {
            var clicked = await ScriptStringAsync("""
            (() => {
              const visible = e => {
                if (!e) return false;
                const s = getComputedStyle(e), r = e.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0 && r.width > 2 && r.height > 2;
              };
              const text = e => (e.innerText || e.textContent || '').replace(/\s+/g, '');
              const editor = [
                '.AnswerForm-editor .ProseMirror[contenteditable="true"]',
                '.AnswerForm .ProseMirror[contenteditable="true"]',
                '.AnswerForm [contenteditable="true"]'
              ].flatMap(s => [...document.querySelectorAll(s)]).find(visible);
              if (!editor) return 'editor-missing';
              const form = editor.closest('.AnswerForm') || editor.parentElement?.parentElement?.parentElement;
              if (!form) return 'form-missing';
              const buttons = [...form.querySelectorAll('button,[role="button"]')].filter(visible);
              const preferred = ['发布回答', '提交回答'];
              const button = buttons.find(e => preferred.includes(text(e))) || buttons.find(e => text(e) === '发布');
              if (!button) return 'publish-missing';
              if (button.disabled || button.getAttribute('aria-disabled') === 'true') return 'publish-disabled';
              button.scrollIntoView({block: 'center', inline: 'center'});
              button.click();
              return 'clicked';
            })()
            """);
            if (clicked == "publish-disabled") throw new Exception("“发布回答”按钮不可用，请检查回答内容");
            if (clicked != "clicked") throw new Exception("回答编辑器已打开，但未找到表单内可用的“发布回答”按钮");

            for (var i=0;i<40;i++) {
                await Task.Delay(500);
                var url = browser.Source?.ToString() ?? "";
                var m = System.Text.RegularExpressions.Regex.Match(url, @"/question/\d+/answer/(\d+)");
                if (m.Success) { AddLog("发布成功：" + url); return url; }
                if (publishResponse.Task.IsCompletedSuccessfully) {
                    var captured = await publishResponse.Task;
                    if (captured.Status >= 400) {
                        if (captured.Body.Contains("10001")) throw new Exception("知乎 10001：请求参数异常，当前 WebView2 会话被拒绝");
                        if (captured.Body.Contains("40362")) throw new Exception("知乎风控 40362：当前 WebView2 会话被限制");
                        throw new Exception($"知乎拒绝发布（HTTP {captured.Status}）：{captured.Body[..Math.Min(captured.Body.Length, 300)]}");
                    }
                    var id = System.Text.RegularExpressions.Regex.Match(captured.Body, "\\\"id\\\"\\s*:\\s*\\\"?(\\d+)");
                    if (id.Success && !string.IsNullOrEmpty(questionId)) {
                        var publishedUrl = $"https://www.zhihu.com/question/{questionId}/answer/{id.Groups[1].Value}";
                        AddLog("发布成功：" + publishedUrl); return publishedUrl;
                    }
                }
                body = await ScriptStringAsync("document.body?.innerText || ''");
                if (body.Contains("10001")) throw new Exception("知乎 10001：请求参数异常，当前 WebView2 会话被拒绝");
                if (body.Contains("40362")) throw new Exception("知乎风控 40362：当前 WebView2 会话被限制");
            }
            throw new Exception("知乎未返回回答链接，无法确认发布成功");
        } finally {
            browser.CoreWebView2.WebResourceResponseReceived -= CapturePublishResponse;
        }
    }

    void StopListening(string message) { poll.Stop(); activeAccount = ""; stop.Enabled = false; login.Enabled = true; status.Text = message; AddLog(message); }
    void Fail(string message) { status.Text = message; AddLog(message); }
    void AddLog(string message) => log.AppendText($"[{DateTime.Now:HH:mm:ss}] {message}{Environment.NewLine}");
}

internal static class Program
{
    [STAThread] static void Main() { ApplicationConfiguration.Initialize(); Application.Run(new MainForm()); }
}
