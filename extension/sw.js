/* 后台服务：跟本地桥保持长轮询，把命令派进标签页，把结果送回去。
 *
 * 这里刻意不做业务编排 —— 只做三件事：
 *   1. 连桥（长轮询 /api/poll，25 秒一轮，兼作 service worker 保活）
 *   2. 把命令变成标签页里的动作（导航 / 转发给 content script）
 *   3. 回结果（/api/result），顺手记一份状态给 popup 看
 */

const POLL_WAIT_SEC = 25;
const TAB_URLS = ['https://www.zhipin.com/*', 'https://zhipin.com/*'];
const HOME_URL = 'https://www.zhipin.com/web/geek/job';
const LOG_MAX = 60;

let looping = false;
let extId = null;

// ---------- 小工具 ----------

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function cfg() {
  const s = await chrome.storage.local.get(['bridgeUrl', 'token', 'enabled']);
  return {
    bridgeUrl: (s.bridgeUrl || 'http://127.0.0.1:8898').replace(/\/+$/, ''),
    token: s.token || '',
    enabled: s.enabled !== false,
  };
}

async function logLine(kind, text) {
  const s = await chrome.storage.local.get(['log']);
  const log = Array.isArray(s.log) ? s.log : [];
  log.unshift({ at: new Date().toISOString().slice(11, 19), kind, text: String(text).slice(0, 300) });
  await chrome.storage.local.set({ log: log.slice(0, LOG_MAX) });
}

async function setStatus(patch) {
  const s = await chrome.storage.local.get(['status']);
  const status = Object.assign({ ok: false, text: '未连接', at: Date.now() }, s.status || {}, patch);
  await chrome.storage.local.set({ status: Object.assign(status, { at: Date.now() }) });
}

// ---------- 标签页 ----------

async function findTab() {
  const tabs = await chrome.tabs.query({ url: TAB_URLS });
  if (!tabs.length) return null;
  const active = tabs.find((t) => t.active);
  return active || tabs[0];
}

function waitComplete(tabId, timeoutMs = 20000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      resolve(v);
    };
    const onUpdated = (id, info) => {
      if (id === tabId && info.status === 'complete') finish(true);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    chrome.tabs.onUpdated.addListener(onUpdated);
  });
}

async function ensureTab(url) {
  const target = url || HOME_URL;
  const existing = await findTab();
  if (existing) {
    if (url && !existing.url.startsWith(url.split('?')[0])) {
      await chrome.tabs.update(existing.id, { url: target, active: true });
      await waitComplete(existing.id);
      return existing.id;
    }
    await chrome.tabs.update(existing.id, { active: true });
    return existing.id;
  }
  const tab = await chrome.tabs.create({ url: target, active: true });
  await waitComplete(tab.id, 25000);
  return tab.id;
}

async function sendToPage(tabId, message, retries = 3) {
  for (let i = 0; i < retries; i++) {
    try {
      const res = await chrome.tabs.sendMessage(tabId, message);
      if (res) return res;
    } catch (e) {
      // content script 还没就绪（页面刚加载/刚刷新），补注入再试
      try {
        await chrome.scripting.executeScript({
          target: { tabId },
          files: ['selectors.js', 'content.js'],
        });
      } catch (e2) {
        /* 页面可能还在加载，等下一轮 */
      }
      await sleep(600);
    }
  }
  return { ok: false, code: 'no-response', error: '页面侧没有响应，可能还在加载或者页面被关了' };
}

// ---------- 动作派发 ----------

async function runCommand(cmd) {
  const action = cmd.action || '';
  const params = cmd.params || {};

  if (action === 'tab.info') {
    const tab = await findTab();
    return tab
      ? { ok: true, data: { id: tab.id, url: tab.url, title: tab.title, status: tab.status } }
      : { ok: true, data: null, note: '当前没有打开 zhipin.com 的标签页' };
  }

  if (action === 'navigate') {
    const url = params.url;
    if (!url) return { ok: false, code: 'bad-params', error: 'navigate 需要 url' };
    const tabId = await ensureTab(url);
    const tab = await chrome.tabs.get(tabId);
    // 页面里常有前端二次跳转，留一点缓冲
    await sleep(params.settleMs || 800);
    return { ok: true, data: { tabId, url: tab.url, title: tab.title } };
  }

  const tabId = await ensureTab(params.url || null);
  await sleep(params.settleMs || 0);
  return await sendToPage(tabId, { action, params });
}

// ---------- 桥通信 ----------

async function postJson(url, token, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Bridge-Auth': token },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

async function reportResult(base, token, id, result) {
  try {
    // code 单独带上：上层要靠它区分「验证码拦住了」和「单纯没找到元素」，
    // 混在 error 文本里就没法做程序判断了。
    await postJson(`${base}/api/result`, token, {
      id,
      ok: result.ok === true,
      code: result.ok === true ? null : (result.code || 'error'),
      data: result.data !== undefined ? result.data : null,
      error: result.ok === true ? null : (result.error || result.code || '未知错误'),
    });
  } catch (e) {
    await logLine('warn', `结果回传失败：${e.message}`);
  }
}

/* 自动领取配对码：桥只放行「空 Origin」和 chrome-extension:// 两种来源，
   网页里的 fetch 必带 Origin，会被桥 403 掉，所以这条路不给站点留口子。 */
async function claimToken(base) {
  try {
    const r = await fetch(`${base}/api/token`, { cache: 'no-store' });
    if (!r.ok) return null;
    const j = await r.json();
    const token = (j && j.token) || '';
    if (!/^[A-Za-z0-9_-]{16,}$/.test(token)) return null;
    await chrome.storage.local.set({ bridgeUrl: base, token, enabled: true });
    await logLine('info', '已从桥领到配对码');
    return token;
  } catch (e) {
    return null;
  }
}

async function pollLoop() {
  if (looping) return;
  looping = true;
  await logLine('info', '轮询已启动');
  while (looping) {
    const c = await cfg();
    if (!c.enabled || !c.token) {
      if (c.enabled && !c.token) {
        // 没配对码时先去桥那儿领一个，领到就接着走正常流程
        if (await claimToken(c.bridgeUrl)) continue;
      }
      await setStatus({ ok: false, text: c.token ? '已暂停' : '还没填 token' });
      await sleep(3000);
      continue;
    }
    let res;
    try {
      const tab = await findTab().catch(() => null);
      const r = await fetch(`${c.bridgeUrl}/api/poll`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Bridge-Auth': c.token },
        body: JSON.stringify({ wait: POLL_WAIT_SEC, ext_id: extId, url: tab ? tab.url : null }),
      });
      if (r.status === 401) {
        await setStatus({ ok: false, text: 'token 不对' });
        await logLine('error', '桥返回 401：token 不匹配');
        await sleep(5000);
        continue;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      res = await r.json();
      await setStatus({ ok: true, text: '已连上桥' });
    } catch (e) {
      await setStatus({ ok: false, text: `连不上桥：${e.message}` });
      await sleep(3000);
      continue;
    }

    if (!res || !res.id) continue;

    const t0 = Date.now();
    let result;
    try {
      result = await runCommand(res);
    } catch (e) {
      result = { ok: false, code: 'exception', error: String(e.message || e) };
    }
    await logLine(
      result.ok ? 'ok' : 'error',
      `${res.action} ${result.ok ? '成功' : '失败'} ${Date.now() - t0}ms${result.ok ? '' : ' · ' + (result.error || '')}`
    );
    await reportResult(c.bridgeUrl, c.token, res.id, result);
    await setStatus({ lastAction: res.action, lastOk: result.ok === true, lastMs: Date.now() - t0 });
  }
  await logLine('info', '轮询已停止');
}

function startLoop() {
  if (!looping) pollLoop().catch((e) => logLine('error', `轮询崩溃：${e.message}`));
}

// ---------- 生命周期 ----------

chrome.runtime.onInstalled.addListener(async () => {
  extId = crypto.randomUUID().slice(0, 8);
  await chrome.storage.local.set({ extId });
  chrome.alarms.create('keepalive', { periodInMinutes: 0.5 });
  startLoop();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create('keepalive', { periodInMinutes: 0.5 });
  startLoop();
});

chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === 'keepalive') startLoop();
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === 'popup.start') {
    startLoop();
    sendResponse({ ok: true });
    return false;
  }
  if (msg && msg.type === 'popup.stop') {
    looping = false;
    sendResponse({ ok: true });
    return false;
  }
  return false;
});

// service worker 每次被唤醒都跑一遍，looping 是内存标记所以不会重复起循环
startLoop();