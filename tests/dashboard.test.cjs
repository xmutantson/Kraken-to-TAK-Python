// Node's built-in runner; no browser, packages, timers, or network are started.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const html = fs.readFileSync(path.join(__dirname, '../templates/CanaryTAKDashboard.html'), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function loadFunction(name, context) {
    // These top-level functions close at eight spaces in the shipped inline script.
    const start = source.indexOf('        function ' + name + '(');
    assert.ok(start >= 0);
    const end = source.indexOf('\n        }', start) + '\n        }'.length;
    vm.runInNewContext(source.slice(start, end), context);
    return context[name];
}

class Element {
    constructor() { this.children = []; this.style = {}; this.selectors = {}; }
    appendChild(child) { this.children.push(child); }
    addEventListener() {} // handlers are not invoked during rendering
    querySelector(selector) { return this.selectors[selector] ||= new Element(); }
}

function renderingContext() {
    const roots = {};
    return {
        document: { createElement: () => new Element(),
                    getElementById: id => roots[id] ||= new Element() },
        state: { editingIndex: null }, SERVER_COLORS: ['#ff4444'], roots,
    };
}

test('complete dashboard JavaScript parses', () => { new vm.Script(source); });

test('server list uses literal text for ordinary label punctuation', () => {
    const ctx = renderingContext();
    const label = 'A & B <north> "survey"';
    loadFunction('renderServerList', ctx)([{ ip: 'example.invalid', station: label }]);
    const info = ctx.roots.serverList.children[0].children[0].children[1];
    assert.equal(info.children[0].textContent, 'example.invalid');
    assert.equal(info.children[1].textContent, 'Station ' + label);
    assert.equal(info.innerHTML, undefined);
});

test('status uses literal station text rather than HTML interpolation', () => {
    const ctx = renderingContext();
    const label = 'A & B <north>';
    loadFunction('renderServerStatus', ctx)([{ ip: 'example.invalid', station: label, reachable: false }]);
    const row = ctx.roots.serverStatusRows.children[0];
    assert.equal(row.querySelector('.status-key').textContent, 'Stn ' + label);
    assert.equal(row.querySelector('.status-val').textContent, 'example.invalid — unreachable');
    assert.equal(row.innerHTML.includes(label), false);
});

test('rejected save reloads authoritative settings and shows error', async () => {
    let polls = 0;
    const messages = [];
    const ctx = { state: { firstLoad: false, editingIndex: 0 },
        fetch: async () => ({ ok: false, json: async () => ({ error: 'Could not save settings' }) }),
        toast: (...args) => messages.push(args), pollStatus: () => polls++ };
    await loadFunction('postSettings', ctx)({}, 'Saved');
    assert.equal(polls, 1);
    assert.equal(ctx.state.firstLoad, true);
    assert.equal(ctx.state.editingIndex, null);
    assert.deepEqual(messages, [['Could not save settings', 'error']]);
});

test('network failure reloads settings instead of acknowledging optimistic edits', async () => {
    let polls = 0;
    const messages = [];
    const ctx = { state: {}, fetch: async () => { throw new Error('offline'); },
        toast: (...args) => messages.push(args), pollStatus: () => polls++ };
    await loadFunction('postSettings', ctx)({}, 'Saved');
    assert.equal(polls, 1);
    assert.equal(ctx.state.firstLoad, true);
    assert.equal(messages[0][1], 'error');
});

test('successful save is acknowledged', async () => {
    const messages = [];
    const ctx = { fetch: async () => ({ ok: true }), toast: (...args) => messages.push(args) };
    await loadFunction('postSettings', ctx)({}, 'Saved');
    assert.deepEqual(messages, [['Saved', 'success']]);
});
