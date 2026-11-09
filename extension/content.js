/* 页面侧执行器。
 *
 * 这条 content script 只做原子 DOM 操作，不含业务编排：
 * 采集列表、读详情、点沟通、读会话、发消息、抓状态。
 * 「投给谁、什么时候投、投什么话术」全部由 Python 侧（cli.py）决定，
 * 这样限速、台账、日志都在一个地方，扩展保持无状态。
 */

(() => {
  // sw 在消息失败时会补注入本文件，重复注册监听器会让 sendMessage 收到多次响应
  if (globalThis.__BJH_READY__) return;
  globalThis.__BJH_READY__ = true;

  const SEL = globalThis.__BJH_SELECTORS__ || {};
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const MAX_TEXT = 6000;

  // ---------- 基础选择 ----------

  function q(list, root) {
    const scope = root || document;
    for (const s of list || []) {
      try {
        const els = Array.from(scope.querySelectorAll(s));
        if (els.length) return els;
      } catch (e) {
        /* 坏选择器直接跳过 */
      }
    }
    return [];
  }

  function q1(list, root) {
    const els = q(list, root);
    return els.length ? els[0] : null;
  }

  function txt(el, max) {
    if (!el) return '';
    const t = (el.innerText || el.textContent || '').replace(/\u00a0/g, ' ').trim();
    return max ? t.slice(0, max) : t;
  }

  // ---------- 风控 / 登录态 ----------

  function risk() {
    const cap = q1(SEL.captcha);
    if (cap && cap.offsetParent !== null) {
      return { blocked: true, kind: 'captcha', detail: txt(cap, 120) };
    }
    const login = q1(SEL.loginRequired);
    if (login && login.offsetParent !== null) {
      return { blocked: true, kind: 'login', detail: '页面弹出登录框' };
    }
    const body = (document.body && document.body.innerText) || '';
    if (/您的环境存在异常|环境异常|请完成验证|安全验证/.test(body)) {
      return { blocked: true, kind: 'risk', detail: body.match(/.{0,20}(您的环境存在异常|环境异常|请完成验证|安全验证).{0,20}/)[0] };
    }
    if (/登录后查看|请先登录/.test(body) && location.href.includes('login')) {
      return { blocked: true, kind: 'login', detail: '被重定向到登录页' };
    }
    return { blocked: false };
  }

  function requireClean() {
    const r = risk();
    if (r.blocked) {
      const err = new Error(`页面被拦住了：${r.kind} —— ${r.detail}`);
      err.code = r.kind;
      throw err;
    }
  }

  // ---------- 采集 ----------

  function readJobCard(el) {
    const linkEl = q1(SEL.jobLink, el) || el.querySelector('a[href*="job_detail"]');
    const href = linkEl ? linkEl.href : '';
    const m = href.match(/job_detail\/([^/?#.]+)/);
    return {
      jobId: m ? m[1] : '',
      title: txt(q1(SEL.jobName, el), 120),
      company: txt(q1(SEL.jobCompany, el), 80),
      salary: txt(q1(SEL.jobSalary, el), 40),
      area: txt(q1(SEL.jobArea, el), 60),
      tags: q(SEL.jobTags, el).map((x) => txt(x, 40)).filter(Boolean).slice(0, 12),
      href,
    };
  }

  async function loadMoreCards(limit) {
    let cards = q(SEL.jobCard);
    let lastLen = -1;
    for (let i = 0; i < 15; i++) {
      if (cards.length >= limit) break;
      const tail = cards[cards.length - 1];
      if (tail) {
        try { tail.scrollIntoView({ block: 'end', behavior: 'instant' }); } catch (e) {}
      }
      window.scrollTo(0, document.body.scrollHeight);
      // 有些版本列表在内部容器里滚，顺手也推一下
      const scroller = document.querySelector('.job-list-container, .job-list-box, #wrap .main');
      if (scroller) scroller.scrollTop = scroller.scrollHeight;
      await sleep(1000);
      const now = q(SEL.jobCard);
      if (now.length === cards.length && now.length === lastLen) break;
      lastLen = cards.length;
      cards = now;
    }
    return cards;
  }

  async function jobsCollect(params) {
    requireClean();
    const limit = Math.max(1, Math.min(params.limit || 30, 100));
    const cards = await loadMoreCards(limit);
    const seen = new Set();
    const out = [];
    for (const el of cards) {
      const j = readJobCard(el);
      if (!j.title) continue;
      const key = j.jobId || `${j.company}|${j.title}|${j.salary}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(j);
      if (out.length >= limit) break;
    }
    return {
      items: out,
      totalCards: cards.length,
      url: location.href,
      selectorHit: q(SEL.jobCard).length > 0,
    };
  }

  function jobRead() {
    requireClean();
    const desc = q1(SEL.detailDesc);
    return {
      url: location.href,
      title: txt(q1(SEL.detailTitle), 160),
      salary: txt(q1(SEL.detailSalary), 60),
      tags: q(SEL.detailTags).map((x) => txt(x, 40)).filter(Boolean).slice(0, 20),
      company: txt(q1(SEL.detailCompany), 100),
      companyDesc: txt(q1(SEL.detailCompanyDesc), 800),
      address: txt(q1(SEL.detailAddress), 160),
      hrName: txt(q1(SEL.detailHRName), 60),
      hrTitle: txt(q1(SEL.detailHRTitle), 80),
      activeTime: txt(q1(SEL.detailActiveTime), 60),
      desc: txt(desc, MAX_TEXT),
      descFound: !!desc,
    };
  }

  // ---------- 沟通 ----------

  async function greetClick() {
    requireClean();
    const btn = q1(SEL.greetButton);
    if (!btn) {
      return { ok: false, code: 'no-button', error: '没找到「立即沟通」按钮，选择器需要校准' };
    }
    const label = txt(btn, 40);
    if (/继续沟通|已沟通/.test(label)) {
      return { ok: true, already: true, label, note: '这个岗位之前聊过，按钮已经是「继续沟通」' };
    }
    btn.scrollIntoView({ block: 'center' });
    await sleep(120);
    btn.click();
    await sleep(900);
    const box = q1(SEL.chatInput);
    return { ok: true, already: false, label, chatOpened: !!box };
  }

  // ---------- 聊天页 ----------

  function chatList() {
    requireClean();
    const rows = q(SEL.chatList);
    const out = rows.map((li, i) => {
      const idEl = q1(SEL.chatListItemId, li);
      const badge = q1(SEL.chatUnreadBadge, li);
      return {
        index: i,
        name: txt(q1(SEL.chatListItemName, li), 60),
        company: txt(q1(SEL.chatListItemCompany, li), 60),
        preview: txt(q1(SEL.chatListItemPreview, li), 120),
        unread: badge ? txt(badge, 10) : '',
        dataId: idEl ? (idEl.getAttribute('data-id') || idEl.getAttribute('data-uid') || idEl.getAttribute('data-geek-id') || '') : '',
        rowText: txt(li, 200),
      };
    });
    return { items: out, total: rows.length, url: location.href };
  }

  async function chatOpen(params) {
    requireClean();
    const rows = q(SEL.chatList);
    if (!rows.length) throw Object.assign(new Error('会话列表是空的'), { code: 'empty-list' });
    let target = null;
    if (typeof params.index === 'number') {
      target = rows[params.index];
    } else if (params.name) {
      target = rows.find((li) => txt(li, 300).includes(params.name));
    }
    if (!target) throw Object.assign(new Error('没找到指定会话'), { code: 'not-found' });
    const before = txt(target, 300);
    target.scrollIntoView({ block: 'center' });
    await sleep(100);
    target.click();
    // 等消息区换人：等到对方的招呼语/姓名出现，最多 8 秒
    for (let i = 0; i < 16; i++) {
      await sleep(500);
      const msgs = q(SEL.chatMessages);
      if (msgs.length) return { ok: true, opened: before.slice(0, 60), messages: msgs.length };
    }
    return { ok: true, opened: before.slice(0, 60), messages: 0 };
  }

  function chatRead(params) {
    requireClean();
    const limit = Math.max(1, Math.min(params.limit || 40, 200));
    const msgs = q(SEL.chatMessages);
    const items = msgs.slice(-limit).map((li) => {
      const cls = li.className || '';
      let side = 'unknown';
      if (/myself|self|mine|right|geek-mine/i.test(cls)) side = 'me';
      else if (/friend|other|left|boss/i.test(cls)) side = 'them';
      return { side, text: txt(q1(SEL.chatMsgText, li) || li, 1500), cls: String(cls).slice(0, 80) };
    });
    return { items, total: msgs.length, url: location.href };
  }

  // ---------- 发消息（带回读验证） ----------

  function nativeSetValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function fillBox(box, value) {
    if (box.isContentEditable) {
      box.focus();
      box.textContent = '';
      const ok = document.execCommand && document.execCommand('insertText', false, value);
      if (!ok || !txt(box, 100000).includes(value.slice(0, 8))) {
        box.textContent = value;
        box.dispatchEvent(new InputEvent('input', {
          bubbles: true, cancelable: true, inputType: 'insertText', data: value,
        }));
      }
    } else {
      box.focus();
      nativeSetValue(box, value);
    }
  }

  async function chatSend(params) {
    requireClean();
    const text = (params.text || '').trim();
    if (!text) throw Object.assign(new Error('消息内容是空的'), { code: 'empty-text' });

    const box = q1(SEL.chatInput);
    if (!box) throw Object.assign(new Error('没找到输入框'), { code: 'no-input' });

    const before = q(SEL.chatMessages).length;

    fillBox(box, text);
    await sleep(250);

    // 回读：输入框里得真的有这段话
    const landed = txt(box, 100000).includes(text.slice(0, 10));
    if (!landed) {
      return { ok: false, code: 'fill-failed', error: '文字没进输入框，可能被前端拦了', typed: txt(box, 120) };
    }

    let sentBy = 'enter';
    const btn = q1(SEL.chatSendButton);
    if (btn && !btn.disabled) {
      btn.click();
      sentBy = 'button';
    } else {
      box.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true,
      }));
    }

    // 只认「会话里真的出现了」：消息条数增加 且 最后一条内容匹配
    for (let i = 0; i < 20; i++) {
      await sleep(400);
      const msgs = q(SEL.chatMessages);
      if (msgs.length > before) {
        const lastText = txt(msgs[msgs.length - 1], 2000);
        if (lastText.includes(text.slice(0, 10))) {
          return { ok: true, sentBy, verified: true, totalAfter: msgs.length };
        }
      }
      if (!txt(box, 100000).trim()) {
        // 输入框空了但没读到新气泡：可能已发出，标记不确定
        await sleep(700);
        break;
      }
    }
    return { ok: true, sentBy, verified: false, totalAfter: q(SEL.chatMessages).length,
             warning: '输入框已清空，但没能确认气泡出现 —— 请人工看一眼' };
  }

  // ---------- 探针 ----------

  async function waitFor(params) {
    const groups = params.group ? [params.group] : Object.keys(SEL);
    const timeout = Math.min(params.timeoutMs || 8000, 30000);
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      for (const g of groups) {
        if (q1(SEL[g])) return { ok: true, group: g, waitedMs: timeout - (deadline - Date.now()) };
      }
      await sleep(250);
    }
    return { ok: false, group: params.group || '*', error: '等超时了' };
  }

  function probeDump(params) {
    const cls = new Map();
    const push = (el) => {
      const c = el.getAttribute && el.getAttribute('class');
      if (!c) return;
      for (const k of String(c).split(/\s+/)) {
        if (k) cls.set(k, (cls.get(k) || 0) + 1);
      }
    };
    document.querySelectorAll('[class]').forEach(push);
    const classTop = [...cls.entries()].sort((a, b) => b[1] - a[1]).slice(0, 80);

    const hits = {};
    for (const [group, list] of Object.entries(SEL)) {
      hits[group] = (list || []).map((s) => {
        try { return { sel: s, count: document.querySelectorAll(s).length }; }
        catch (e) { return { sel: s, count: -1 }; }
      });
    }

    const detailLinks = Array.from(document.querySelectorAll('a[href*="job_detail"]'))
      .slice(0, 6).map((a) => a.getAttribute('href'));

    return {
      url: location.href,
      title: document.title,
      readyState: document.readyState,
      risk: risk(),
      counts: {
        links: document.querySelectorAll('a').length,
        buttons: document.querySelectorAll('button, .btn, a.btn').length,
        jobDetailLinks: document.querySelectorAll('a[href*="job_detail"]').length,
      },
      classTop,
      detailLinks,
      hits,
      textHead: (document.body ? document.body.innerText : '').slice(0, params.textChars || 1500),
    };
  }

  function stateGet() {
    return {
      url: location.href,
      title: document.title,
      risk: risk(),
      has: {
        jobCard: q(SEL.jobCard).length,
        chatList: q(SEL.chatList).length,
        chatInput: q(SEL.chatInput).length,
        greetButton: q(SEL.greetButton).length,
      },
    };
  }

  // ---------- 版式自检 ----------
  //
  // 站点改版时，选择器会安安静静地全部落空，然后代码跑到一半才发现没东西可点 ——
  // 更糟的是它可能「成功」返回一个空列表，看上去像"今天没有新岗位"。
  // 所以在动页面之前先点名：这一页该有的控件在不在。
  //
  // 两种「没命中」必须分开，不然要么误报要么漏报：
  //   页面还没加载完  -> not-ready     ，重试就好
  //   页面确实改版了  -> layout-changed，要重新校准 selectors.js
  // 靠 anchor 组来分辨：anchor 在 = 页面已经渲染出来了，此时缺的就是真缺。
  const LAYOUT = {
    'jobs.collect': { anchor: 'jobCard', need: ['jobCard', 'jobName', 'jobLink'] },
    'job.read': { anchor: 'detailTitle', need: ['detailTitle'] },
    'greet.click': { anchor: 'greetButton', need: ['greetButton'] },
    'chat.list': { anchor: 'chatList', need: ['chatList', 'chatListItemName'] },
    'chat.open': { anchor: 'chatList', need: ['chatList', 'chatListItemName'] },
    'chat.read': { anchor: 'chatMessages', need: ['chatMessages'] },
    'chat.send': { anchor: 'chatInput', need: ['chatInput', 'chatSendButton'] },
  };
  const ANCHOR_WAIT_MS = 6000;

  function layoutError(code, message) {
    const e = new Error(message);
    e.code = code;
    return e;
  }

  function hit(group) {
    return q(SEL[group]).length > 0;
  }

  async function waitAnchor(group, budgetMs) {
    const deadline = Date.now() + budgetMs;
    for (;;) {
      if (hit(group)) return true;
      if (Date.now() >= deadline) return false;
      await sleep(300);
    }
  }

  async function guardLayout(action) {
    const spec = LAYOUT[action];
    if (!spec) return; // 只读诊断类动作不设门：页面坏掉的时候更得能探
    if (!(await waitAnchor(spec.anchor, ANCHOR_WAIT_MS))) {
      if (document.readyState !== 'complete') {
        throw layoutError('not-ready', `页面还没加载完（readyState=${document.readyState}），${spec.anchor} 还没出来，稍后重试`);
      }
      throw layoutError(
        'layout-changed',
        `版式对不上：等了 ${ANCHOR_WAIT_MS / 1000} 秒，${spec.anchor} 一个都没命中 —— selectors.js 里的选择器要重新校准`
      );
    }
    const missing = spec.need.filter((g) => !hit(g));
    if (missing.length) {
      throw layoutError(
        'layout-changed',
        `版式对不上：${missing.join('、')} 没命中（${action} 要用这几个控件）—— selectors.js 里的选择器要重新校准`
      );
    }
  }

  // ---------- 路由 ----------

  const ACTIONS = {
    'probe.dump': probeDump,
    'state.get': stateGet,
    'jobs.collect': jobsCollect,
    'job.read': jobRead,
    'greet.click': greetClick,
    'chat.list': chatList,
    'chat.open': chatOpen,
    'chat.read': chatRead,
    'chat.send': chatSend,
    'wait.for': waitFor,
  };

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    const fn = ACTIONS[msg && msg.action];
    if (!fn) {
      sendResponse({ ok: false, code: 'unknown-action', error: `页面侧不认识这个动作：${msg && msg.action}` });
      return false;
    }
    Promise.resolve()
      .then(() => guardLayout(msg.action))
      .then(() => fn(msg.params || {}))
      .then((data) => {
        if (data && data.ok === false) sendResponse({ ok: false, code: data.code, error: data.error, data });
        else sendResponse({ ok: true, data });
      })
      .catch((e) => sendResponse({ ok: false, code: e.code || 'error', error: String(e.message || e) }));
    return true;
  });
})();