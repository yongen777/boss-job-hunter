/* 选择器候选表。
 *
 * BOSS直聘 的 class 名会随版本变化，所以每个元素都写一组候选，
 * content.js 按顺序试，第一个有命中的就用。
 *
 * 这份表不靠猜：初次安装后用 `python cli.py probe` 在真实页面上跑一遍，
 * 把 counts 里计数为 0 的组按 probe 给出的实际 class 改掉。
 */
globalThis.__BJH_SELECTORS__ = {
  // ---- 搜索/推荐列表页 ----
  jobCard: [
    'li.job-card-wrapper',
    '.job-card-wrapper',
    '.job-list-box li',
    '.job-list-container li',
    'ul.job-list-box > li',
  ],
  jobName: ['.job-name', '.job-title .job-name', '.job-card-left .job-name', 'a .job-name'],
  jobSalary: ['.salary', '.job-salary', '.job-card-left .salary', '.red'],
  jobArea: ['.job-area', '.job-area-wrapper .job-area', '.job-card-left .job-area'],
  jobTags: ['.tag-list li', '.job-card-left .tag-list li', '.tags .tag-item', '.tag-list span'],
  jobCompany: ['.company-name', '.company-name a', '.company-info .company-name', 'h3.company-name'],
  jobCompanyInfo: ['.company-tag-list li', '.company-info', '.company-tags'],
  jobLink: ['a.job-card-left', 'a[href*="/job_detail/"]', '.job-card-box a'],

  // ---- 职位详情页 ----
  detailTitle: ['.job-banner .name', '.job-primary .name', 'h1', '.job-title'],
  detailSalary: ['.job-banner .salary', '.job-primary .salary', '.salary'],
  detailTags: ['.job-keyword-list li', '.job-keyword-list span', '.tag-all .tag-item'],
  detailDesc: ['.job-sec-text', '.job-detail-section .text', '.job-detail .text', '.job-sec .text'],
  detailCompany: ['.company-info .company-name', '.sider-company .company-name', '.company-info-box .name'],
  detailCompanyDesc: ['.company-info-box .company-info', '.company-intro', '.company-detail'],
  detailAddress: ['.location-address', '.job-location .location-address', '.address'],
  detailHRName: ['.boss-info .name', '.info-public .name', '.boss-name'],
  detailHRTitle: ['.boss-info .gray', '.info-public .gray', '.boss-title'],
  detailActiveTime: ['.boss-active-time', '.job-banner .boss-active-time'],

  // ---- 沟通入口 ----
  greetButton: [
    '.btn-startchat',
    '.op-btn-chat',
    '.job-op .btn',
    'a.btn.btn-startchat',
    '.job-banner .btn-startchat',
  ],
  // 聊天输入区（详情页右侧浮层 or 聊天页）
  chatInput: [
    '#chat-input',
    '.chat-input',
    'div[contenteditable="true"]',
    'textarea.input-area',
    '.chat-editor [contenteditable="true"]',
  ],
  chatSendButton: ['.btn-send', '.chat-op .btn-send', 'button.btn-send', '.send-message'],

  // ---- 聊天页 ----
  chatList: ['.chat-user-list li', '.user-list li', '.chat-user-list .chat-user', '.session-list li'],
  chatListItemName: ['.name', '.title', '.user-name', '.geek-name'],
  chatListItemCompany: ['.company-name', '.sub', '.company'],
  chatListItemPreview: ['.last-msg', '.text', '.msg-text', '.content'],
  chatUnreadBadge: ['.notice-badge', '.unread', '.badge', '.red-dot'],
  chatListItemId: ['[data-id]', '[data-uid]', '[data-geek-id]'],

  chatMessages: ['.chat-message-list li', '.message-list li', '.chat-record li', '.message-item'],
  chatMsgFromMe: ['.item-myself', '.message-myself', '.myself'],
  chatMsgText: ['.text', '.message-text', '.content span', '.card-inner'],

  // ---- 全局状态探针 ----
  loginRequired: ['.login-dialog', '.login-form', '.sign-form', '.boss-login-dialog'],
  captcha: [
    '.geetest_panel',
    '.geetest_widget',
    '#captcha',
    '.verify-wrapper',
    '.nc-container',
    '.captcha-box',
  ],
  riskNotice: ['.risk-tip', '.error-tip', '.dialog-error'],
};

/* 搜索/聊天页的固定路径（随站点改版可能需要更新） */
globalThis.__BJH_ROUTES__ = {
  jobSearch: 'https://www.zhipin.com/web/geek/job',
  jobDetail: 'https://www.zhipin.com/job_detail/',
  chat: 'https://www.zhipin.com/web/geek/chat',
  myResume: 'https://www.zhipin.com/web/geek/resume',
};