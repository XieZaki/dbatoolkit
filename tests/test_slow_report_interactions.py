from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE_INTERACTION_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(0, 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)];
assert.ok(scripts.length > 0, 'the slow report must contain its inline script');
const script = scripts[scripts.length - 1][1];

class FakeClassList {
  constructor(values = []) { this.values = new Set(values); }
  add(...values) { values.forEach((value) => this.values.add(value)); }
  remove(...values) { values.forEach((value) => this.values.delete(value)); }
  contains(value) { return this.values.has(value); }
  toggle(value, force) {
    const enabled = force === undefined ? !this.contains(value) : Boolean(force);
    if (enabled) this.add(value); else this.remove(value);
    return enabled;
  }
}

const SIMPLE = /^([a-z]+)?(?:\.([\w-]+))?(?:\[([\w-]+)(?:(\^?=)"([^"]*)")?\])?$/;
function matchesSimple(node, part) {
  const match = SIMPLE.exec(part);
  assert.ok(match, `unsupported selector part: ${part}`);
  const [, tag, cls, attr, operator, expected] = match;
  if (tag && node.tagName !== tag.toUpperCase()) return false;
  if (cls && !node.classList.contains(cls)) return false;
  if (attr) {
    const value = node.getAttribute(attr);
    if (value === null) return false;
    if (operator === '^=' && !value.startsWith(expected)) return false;
    if (operator === '=' && value !== expected) return false;
  }
  return true;
}
function matchesSelector(node, selector) {
  if (selector.includes(',')) return selector.split(',').some((item) => matchesSelector(node, item));
  const parts = selector.trim().split(/\s+/);
  if (!matchesSimple(node, parts[parts.length - 1])) return false;
  let index = parts.length - 2;
  for (let parent = node.parentElement; parent && index >= 0; parent = parent.parentElement) {
    if (matchesSimple(parent, parts[index])) index -= 1;
  }
  return index < 0;
}

const documentListeners = new Map();
function fire(target, type, props = {}) {
  const event = {type, target, preventDefault() {}, ...props};
  for (let node = target; node; node = node.parentElement) {
    (node.listeners.get(type) || []).forEach((callback) => callback(event));
  }
  (documentListeners.get(type) || []).forEach((callback) => callback(event));
}

class FakeElement {
  constructor(tag, {id, attrs = {}, classes = [], text = '', open = false} = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = new Map(Object.entries(attrs));
    if (id) this.attributes.set('id', id);
    this.classList = new FakeClassList(classes);
    this.listeners = new Map();
    this.children = [];
    this.parentElement = null;
    this.ownText = text;
    this.style = {};
    this.disabled = false;
    this.hidden = false;
    this.open = open;
    this.value = '';
  }
  get id() { return this.getAttribute('id') || ''; }
  get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(''); }
  set textContent(value) {
    this.ownText = String(value);
    this.children.forEach((child) => { child.parentElement = null; });
    this.children = [];
  }
  append(...children) { children.forEach((child) => this.appendChild(child)); return this; }
  appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
  removeChild(child) { this.children = this.children.filter((item) => item !== child); child.parentElement = null; return child; }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  addEventListener(name, callback) {
    const callbacks = this.listeners.get(name) || [];
    callbacks.push(callback);
    this.listeners.set(name, callbacks);
  }
  dispatchEvent(event) { fire(this, event.type); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  matches(selector) { return matchesSelector(this, selector); }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) { if (node.matches(selector)) return node; }
    return null;
  }
  querySelectorAll(selector) { return this.descendants().filter((node) => node.matches(selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  cloneNode(deep) {
    const copy = new FakeElement(this.tagName, {text: this.ownText, open: this.open, classes: [...this.classList.values]});
    copy.attributes = new Map(this.attributes);
    if (deep) this.children.forEach((child) => copy.appendChild(child.cloneNode(true)));
    return copy;
  }
  focus() { document.activeElement = this; }
}
const el = (tag, options, ...children) => new FakeElement(tag, options).append(...children);

const toast = el('div', {id: 'toast'});
const toggle = el('button', {id: 'toggleDetails'}, el('span'));
const copySummary = el('button', {id: 'copySummary', text: '复制摘要', attrs: {'data-copy': 'summary text'}});
const printButton = el('button', {id: 'printReport'});
const crumb = el('b', {id: 'crumbTitle', text: '诊断总览'});
const tab = (page) => el('a', {attrs: {href: `#${page}`, 'data-tab': page}});
const tabOverview = tab('page-overview');
tabOverview.classList.add('active');
const tabSessions = tab('page-sessions');
const tabSql = tab('page-sql');
const openRecovery = el('button', {id: 'openRecovery'});
const blockingLink = el('a', {attrs: {href: '#blocking-chain'}});
const sqlLink = el('a', {attrs: {href: '#current-request-002'}});
const priorityItem = el('button', {classes: ['priority-item'], attrs: {'data-open-session': '52'}}, el('span', {text: '52'}));
const overview = el('section', {id: 'page-overview', classes: ['page', 'active'], attrs: {'data-page-title': '诊断总览'}},
  blockingLink, sqlLink, openRecovery, priorityItem);

const sqlRow = (index, text) => el('tr', {attrs: {'data-sql-index': String(index)}}, el('td', {text}), el('td', {text: '00:00:05'}));
const sqlRows = [sqlRow(0, 'UPDATE alpha_rows'), sqlRow(1, 'SELECT beta_rows')];
const sqlTable = el('table', {attrs: {'data-sql-table': ''}}, el('tbody', {}, ...sqlRows));
const sqlSearch = el('input', {id: 'sqlSearch'});
const sqlDetails = [0, 1].map((index) => el('div', {classes: ['sql-detail'], attrs: {'data-sql-detail': String(index)}},
  el('section', {id: `current-request-00${index + 1}`})));
const sqlPage = el('section', {id: 'page-sql', classes: ['page'], attrs: {'data-page-title': '当前执行 SQL'}},
  sqlSearch, sqlTable, el('section', {id: 'current-requests'}, ...sqlDetails));

const evidenceDetails = el('details', {attrs: {'data-expandable': ''}}, el('section', {id: 'blocking-chain'}));
const recoveryCopy = el('button', {text: '复制文本', attrs: {'data-copy': 'manual reference text'}});
const recovery = el('section', {id: 'immediate-recovery'},
  el('details', {}, el('div', {id: 'reference-command-001'}, recoveryCopy)));
const blocking = el('section', {id: 'page-blocking', classes: ['page'], attrs: {'data-page-title': '阻塞分析'}},
  evidenceDetails, recovery);

const planItems = [0, 1].map((index) => el('button', {attrs: {'data-plan-select': String(index)}}));
const planPanels = [0, 1].map((index) => el('div', {attrs: {'data-plan-panel': String(index)}}));
const plans = el('section', {id: 'page-plans', classes: ['page'], attrs: {'data-page-title': '执行计划'}},
  ...planItems, ...planPanels);

const sessionRow = (sid, user, kind, index) => el('tr', {attrs: {
  'data-session': sid, 'data-filter': kind, 'data-detail-index': String(index)}},
  el('td', {text: sid}), el('td', {text: user}));
const rows = [sessionRow('51', ' monitor ', 'blocking', 0), sessionRow('52', 'analyst', 'blocked', 1)];
const table = el('table', {id: 'sessionTable', attrs: {'data-session-search': 'true'}}, el('tbody', {}, ...rows));
const search = el('input', {id: 'sessionSearch'});
const filter = el('select', {id: 'sessionFilter'});
filter.value = 'all';
const filteredCount = el('span', {id: 'filteredCount'});
const noResults = el('div', {id: 'noResults'});
const detailLink = el('a', {attrs: {href: '#current-request-001'}, text: '查看当前执行 SQL'});
const sessionDetails = el('div', {classes: ['session-details']},
  el('div', {attrs: {'data-session-detail': '0'}}, el('dl', {id: 'detail-anchor-51'}, el('dd', {text: 'alpha source'})), detailLink),
  el('div', {attrs: {'data-session-detail': '1'}}, el('dd', {text: 'beta source'})));
const appendix = el('details', {id: 'session-appendix', attrs: {'data-expandable': ''}});
const sessions = el('section', {id: 'page-sessions', classes: ['page'], attrs: {'data-page-title': '活跃会话'}},
  filteredCount, search, filter, table, noResults, sessionDetails, appendix);
const overlay = el('div', {id: 'overlay'});
const drawerTitle = el('h3', {id: 'drawerTitle'});
const drawerBody = el('div', {id: 'drawerBody'});
const drawerClose = el('button', {id: 'drawerClose'});
const drawer = el('aside', {id: 'sessionDrawer'}, drawerTitle, drawerClose, drawerBody);
const recoveryBody = el('div', {id: 'recoveryBody'});
const recoveryClose = el('button', {id: 'recoveryClose'});
const modal = el('div', {id: 'recoveryModal'}, recoveryClose, recoveryBody);
[overlay, drawer, modal].forEach((node) => { node.hidden = true; });
const body = el('body', {}, toast, toggle, copySummary, printButton, crumb, tabOverview, tabSessions, tabSql,
  overview, sqlPage, blocking, plans, sessions, overlay, drawer, modal);
const root = el('html', {}, body);

const document = {
  selectedText: '',
  activeElement: null,
  documentElement: root,
  body,
  getElementById(id) { return [root, ...root.descendants()].find((node) => node.id === id) || null; },
  querySelectorAll(selector) { return root.descendants().filter((node) => node.matches(selector)); },
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
  addEventListener(name, callback) {
    const callbacks = documentListeners.get(name) || [];
    callbacks.push(callback);
    documentListeners.set(name, callbacks);
  },
  createElement(tag) {
    const node = new FakeElement(tag);
    if (tag === 'textarea') node.select = () => { document.selectedText = node.value; };
    return node;
  },
  execCommand(command) { assert.equal(command, 'copy'); return true; },
};
const windowListeners = new Map();
let printCount = 0;
const window = {
  isSecureContext: false,
  location: {hash: ''},
  addEventListener(name, callback) { windowListeners.set(name, callback); },
  clearTimeout() {},
  setTimeout() { return 1; },
  print() { printCount += 1; },
};
class FakeEvent { constructor(type) { this.type = type; } }

vm.runInNewContext(script, {document, window, navigator: {}, Event: FakeEvent}, {timeout: 1000});
const details = [evidenceDetails, appendix];
const selected = (nodes) => nodes.map((node) => node.classList.contains('selected'));

assert.equal(root.classList.contains('js'), true);
assert.deepEqual(selected(sqlRows), [true, false]);
assert.deepEqual(selected(sqlDetails), [true, false]);
assert.deepEqual(selected(planPanels), [true, false]);

assert.equal(filteredCount.textContent, '报告展示 2 个会话');
search.value = ' ANALYST ';
fire(search, 'input');
assert.deepEqual(rows.map((row) => row.hidden), [true, false]);
assert.equal(filteredCount.textContent, '筛选命中 1 / 2 个会话');
search.value = '';
filter.value = 'blocking';
fire(filter, 'change');
assert.deepEqual(rows.map((row) => row.hidden), [false, true]);
search.value = 'analyst';
fire(search, 'input');
assert.equal(rows.every((row) => row.hidden), true);
assert.equal(noResults.style.display, 'block');
search.value = '';
filter.value = 'all';
fire(filter, 'change');
assert.equal(rows.every((row) => !row.hidden), true);
assert.equal(noResults.style.display, 'none');

fire(toggle, 'click');
assert.equal(details.every((item) => item.open), true);
assert.equal(toggle.getAttribute('aria-expanded'), 'true');
assert.equal(toggle.children[0].textContent, '收起全部');
fire(toggle, 'click');
assert.equal(details.every((item) => !item.open), true);
assert.equal(toggle.getAttribute('aria-expanded'), 'false');

fire(tabSessions, 'click');
assert.equal(sessions.classList.contains('active'), true);
assert.equal(overview.classList.contains('active'), false);
assert.equal(tabSessions.getAttribute('aria-current'), 'page');
assert.equal(tabOverview.getAttribute('aria-current'), null);
assert.equal(crumb.textContent, '活跃会话');

fire(blockingLink, 'click');
assert.equal(blocking.classList.contains('active'), true);
assert.equal(sessions.classList.contains('active'), false);
assert.equal(evidenceDetails.open, true);
assert.equal(crumb.textContent, '阻塞分析');

fire(sqlLink, 'click');
assert.equal(sqlPage.classList.contains('active'), true);
assert.equal(tabSql.getAttribute('aria-current'), 'page');
assert.deepEqual(selected(sqlDetails), [false, true]);
assert.deepEqual(selected(sqlRows), [false, true]);
fire(sqlRows[0].children[0], 'click');
assert.deepEqual(selected(sqlDetails), [true, false]);
fire(sqlRows[1], 'keydown', {key: 'Enter'});
assert.deepEqual(selected(sqlDetails), [false, true]);
sqlSearch.value = 'alpha';
fire(sqlSearch, 'input');
assert.deepEqual(sqlRows.map((row) => row.hidden), [false, true]);

fire(planItems[1], 'click');
assert.deepEqual(selected(planPanels), [false, true]);
assert.deepEqual(selected(planItems), [false, true]);

assert.equal(rows[0].getAttribute('tabindex'), '0');
fire(rows[0].children[1], 'click');
assert.equal(drawer.hidden, false);
assert.equal(overlay.hidden, false);
assert.equal(drawerTitle.textContent, '会话详情：51');
const drawerCopy = drawerBody.children[0];
assert.equal(drawerCopy.getAttribute('data-session-detail'), null);
assert.equal(drawerCopy.querySelectorAll('[id]').length, 0);
assert.equal(drawerBody.textContent.includes('alpha source'), true);
assert.equal(document.getElementById('detail-anchor-51').parentElement.getAttribute('data-session-detail'), '0');
assert.equal(document.activeElement, drawerClose);
fire(body, 'keydown', {key: 'Escape'});
assert.equal(drawer.hidden, true);
assert.equal(overlay.hidden, true);
assert.equal(document.activeElement, rows[0]);
fire(rows[1], 'keydown', {key: 'Enter'});
assert.equal(drawerTitle.textContent, '会话详情：52');
assert.equal(drawerBody.textContent, 'beta source');
fire(drawerClose, 'click');
assert.equal(drawer.hidden, true);

fire(priorityItem.children[0], 'click');
assert.equal(drawer.hidden, false);
assert.equal(drawerTitle.textContent, '会话详情：52');
fire(overlay, 'click');
fire(rows[0], 'keydown', {key: ' '});
fire(drawerBody.querySelector('a[href^="#"]'), 'click');
assert.equal(drawer.hidden, true);
assert.equal(sqlPage.classList.contains('active'), true);
assert.deepEqual(selected(sqlDetails), [true, false]);

fire(openRecovery, 'click');
assert.equal(modal.hidden, false);
const cloned = recoveryBody.children[0];
assert.equal(cloned.id, '');
assert.equal(cloned.querySelectorAll('[id]').length, 0);
assert.equal(cloned.querySelectorAll('details').every((item) => item.open), true);
assert.equal(document.getElementById('immediate-recovery'), recovery);
fire(cloned.querySelector('[data-copy]'), 'click');
assert.equal(document.selectedText, 'manual reference text');
assert.equal(toast.textContent, '文本已复制，未执行。');
assert.equal(toast.classList.contains('success'), true);
fire(overlay, 'click');
assert.equal(modal.hidden, true);

search.value = 'analyst';
fire(search, 'input');
windowListeners.get('beforeprint')();
assert.equal(details.every((item) => item.open), true);
assert.equal(rows.concat(sqlRows).every((row) => !row.hidden), true);
assert.equal(noResults.style.display, 'none');
fire(printButton, 'click');
assert.equal(printCount, 1);
windowListeners.get('afterprint')();
assert.deepEqual(rows.map((row) => row.hidden), [true, false]);
assert.deepEqual(sqlRows.map((row) => row.hidden), [false, true]);

document.selectedText = '';
fire(copySummary, 'click');
assert.equal(document.selectedText, 'summary text');
assert.equal(copySummary.disabled, false);
assert.equal(copySummary.getAttribute('aria-busy'), null);
assert.equal(copySummary.textContent, '复制摘要');

console.log('SLOW_REPORT_INTERACTIONS_PASS');
"""


def test_slow_report_inline_interactions_with_node_vm() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; no browser or extra Python dependency is required.")

    template_path = Path(__file__).parents[1] / "dba" / "reporting" / "templates" / "slow_report.html"
    result = subprocess.run(
        [node, "-e", _NODE_INTERACTION_HARNESS],
        input=template_path.read_text(encoding="utf-8"),
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SLOW_REPORT_INTERACTIONS_PASS"
