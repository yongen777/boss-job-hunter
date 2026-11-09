/* 扩展面板：填桥地址和配对码，看一眼连接状态和最近日志。 */

const $ = (id) => document.getElementById(id);

async function load() {
  const s = await chrome.storage.local.get(['bridgeUrl', 'token', 'status', 'log']);
  $('url').value = s.bridgeUrl || 'http://127.0.0.1:8898';
  $('token').value = s.token || '';
  renderStatus(s.status);
  renderLog(s.log || []);
}

function renderStatus(st) {
  const dot = $('dot');
  const text = $('text');
  if (!st) {
    dot.className = 'dot';
    text.textContent = '还没连接';
    return;
  }
  dot.className = 'dot ' + (st.ok ? 'on' : 'off');
  const when = st.at ? new Date(st.at).toLocaleTimeString('zh-CN', { hour12: false }) : '';
  let line = st.text || (st.ok ? '已连上' : '未连接');
  if (st.lastAction) {
    line += ` · 上次动作 ${st.lastAction} ${st.lastOk ? '成功' : '失败'} ${st.lastMs || 0}ms`;
  }
  text.textContent = `${line}${when ? '  (' + when + ')' : ''}`;
}

function renderLog(log) {
  const ul = $('log');
  ul.textContent = '';
  for (const item of log.slice(0, 40)) {
    const li = document.createElement('li');
    const t = document.createElement('span');
    t.className = 't';
    t.textContent = item.at;
    const m = document.createElement('span');
    m.className = 'm ' + (item.kind || 'info');
    m.textContent = item.text;
    li.append(t, m);
    ul.append(li);
  }
}

$('save').addEventListener('click', async () => {
  const bridgeUrl = ($('url').value || '').trim().replace(/\/+$/, '') || 'http://127.0.0.1:8898';
  const token = ($('token').value || '').trim();
  if (!token) {
    $('text').textContent = '配对码不能空着';
    return;
  }
  await chrome.storage.local.set({ bridgeUrl, token, enabled: true });
  chrome.runtime.sendMessage({ type: 'popup.start' });
  $('text').textContent = '已保存，正在连接…';
  setTimeout(load, 900);
});

$('pause').addEventListener('click', async () => {
  await chrome.storage.local.set({ enabled: false });
  chrome.runtime.sendMessage({ type: 'popup.stop' });
  $('text').textContent = '已暂停（保存可重新连上）';
  setTimeout(load, 500);
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.status || changes.log) load();
});

/* 自动配对：huntsman/browser.py 会把配对码挂在 URL 上打开这个页面
   （popup.html?token=xxx），一加载就存好并连上，不用再手填。
   存完立刻把配对码从地址栏抹掉，免得留在历史里。 */
async function pairFromUrl() {
  const q = new URLSearchParams(location.search);
  const token = (q.get('token') || '').trim();
  if (!/^[A-Za-z0-9_-]{16,}$/.test(token)) return false;
  const bridgeUrl = (q.get('bridge') || '').trim().replace(/\/+$/, '') || 'http://127.0.0.1:8898';
  await chrome.storage.local.set({ bridgeUrl, token, enabled: true });
  history.replaceState(null, '', location.pathname);
  $('text').textContent = '已自动配对，正在连接…';
  chrome.runtime.sendMessage({ type: 'popup.start' });
  return true;
}

pairFromUrl().catch(() => {}).finally(load);
setInterval(load, 2000);