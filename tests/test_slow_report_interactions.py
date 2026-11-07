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
  constructor() { this.values = new Set(); }
  add(...values) { values.forEach((value) => this.values.add(value)); }
  remove(...values) { values.forEach((value) => this.values.delete(value)); }
  contains(value) { return this.values.has(value); }
  toggle(value, force) {
    const enabled = force === undefined ? !this.contains(value) : Boolean(force);
    if (enabled) this.add(value); else this.remove(value);
    return enabled;
  }
}

class FakeElement {
  constructor({text = '', attrs = {}, open = false} = {}) {
    this.textContent = text;
    this.attributes = new Map(Object.entries(attrs));
    this.listeners = new Map();
    this.classList = new FakeClassList();
    this.style = {};
    this.children = [];
    this.disabled = false;
    this.hidden = false;
    this.open = open;
    this.value = '';
    this.span = new FakeElementSpan();
  }
  addEventListener(name, callback) {
    const callbacks = this.listeners.get(name) || [];
    callbacks.push(callback);
    this.listeners.set(name, callbacks);
  }
  dispatch(name) {
    (this.listeners.get(name) || []).forEach((callback) => callback({type: name, target: this}));
  }
  dispatchEvent(event) { this.dispatch(event.type); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  querySelector(selector) {
    assert.equal(selector, 'span');
    return this.span;
  }
  select() { document.selectedText = this.value; }
}

class FakeElementSpan {
  constructor() { this.textContent = ''; }
}

const toast = new FakeElement();
const toggle = new FakeElement();
const search = new FakeElement();
const filteredCount = new FakeElement();
const noResults = new FakeElement();
const printButton = new FakeElement();
const copyButton = new FakeElement({text: '复制文本', attrs: {'data-copy': 'manual reference text'}});
const details = [new FakeElement({open: false}), new FakeElement({open: false})];
const rows = [new FakeElement({text: 'session alpha monitor'}), new FakeElement({text: 'session beta analyst'})];
const elements = new Map([
  ['toast', toast], ['toggleDetails', toggle], ['sessionSearch', search],
  ['filteredCount', filteredCount], ['noResults', noResults], ['printReport', printButton],
]);
const document = {
  selectedText: '',
  getElementById(id) {
    assert.ok(elements.has(id), `unexpected element lookup: ${id}`);
    return elements.get(id);
  },
  querySelectorAll(selector) {
    if (selector === '[data-copy]') return [copyButton];
    if (selector === 'details[data-expandable]') return details;
    if (selector === 'table[data-session-search] tbody tr') return rows;
    if (selector === '.nav-links a' || selector === '.main > [id], .main > header[id]') return [];
    throw new Error(`unexpected selector: ${selector}`);
  },
  createElement(tag) {
    assert.equal(tag, 'textarea');
    const area = new FakeElement();
    area.setAttribute('readonly', '');
    area.select = () => { document.selectedText = area.value; };
    return area;
  },
  body: {
    appendChild(node) { this.children.push(node); return node; },
    removeChild(node) { this.children = this.children.filter((item) => item !== node); return node; },
    children: [],
  },
  execCommand(command) { assert.equal(command, 'copy'); return true; },
};
const windowListeners = new Map();
let printCount = 0;
const window = {
  isSecureContext: false,
  addEventListener(name, callback) { windowListeners.set(name, callback); },
  clearTimeout() {},
  setTimeout() { return 1; },
  print() { printCount += 1; },
};
class FakeEvent { constructor(type) { this.type = type; } }

vm.runInNewContext(script, {document, window, navigator: {}, Event: FakeEvent}, {timeout: 1000});

assert.equal(filteredCount.textContent, '报告展示 2 行');
search.value = ' BETA ';
search.dispatch('input');
assert.equal(rows[0].hidden, true);
assert.equal(rows[1].hidden, false);
assert.equal(filteredCount.textContent, '筛选命中 1 / 2 行');
search.value = 'not-present';
search.dispatch('input');
assert.equal(rows[0].hidden, true);
assert.equal(rows[1].hidden, true);
assert.equal(noResults.style.display, 'block');
search.value = '';
search.dispatch('input');
assert.equal(rows.every((row) => !row.hidden), true);
assert.equal(noResults.style.display, 'none');

toggle.dispatch('click');
assert.equal(details.every((item) => item.open), true);
assert.equal(toggle.getAttribute('aria-expanded'), 'true');
assert.equal(toggle.span.textContent, '收起全部');
toggle.dispatch('click');
assert.equal(details.every((item) => !item.open), true);
assert.equal(toggle.getAttribute('aria-expanded'), 'false');

search.value = 'beta';
search.dispatch('input');
windowListeners.get('beforeprint')();
assert.equal(details.every((item) => item.open), true);
assert.equal(rows.every((row) => !row.hidden), true);
assert.equal(noResults.style.display, 'none');
printButton.dispatch('click');
assert.equal(printCount, 1);
windowListeners.get('afterprint')();
assert.equal(details.every((item) => !item.open), true);
assert.equal(rows[0].hidden, true);
assert.equal(rows[1].hidden, false);

copyButton.dispatch('click');
assert.equal(document.selectedText, 'manual reference text');
assert.equal(copyButton.disabled, false);
assert.equal(copyButton.getAttribute('aria-busy'), null);
assert.equal(copyButton.textContent, '复制文本');
assert.equal(toast.textContent, '文本已复制，未执行。');
assert.equal(toast.classList.contains('success'), true);

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
