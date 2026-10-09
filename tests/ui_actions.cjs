const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
let response, requests = [], toasts = [], timers = [], redirect = '';
const context = vm.createContext({console, Set, URLSearchParams, Error,
  document: {addEventListener() {}}, window: {},
  location: {search: '', set href(value) { redirect = value; }},
  confirm: () => true, setTimeout: callback => timers.push(callback),
  fetch: async (url, options) => { requests.push([url, options]); if (response instanceof Error) throw response;
    return {ok: response.code < 400, status: response.code, clone() {return this;}, json: async () => response.body}; },
});
context.window = context;
vm.runInContext(fs.readFileSync(path.join(root, 'webapp/static/js/app.js'), 'utf8'), context);
context.showToast = (message, type) => toasts.push([message, type]);
function load(name) {
  const template = fs.readFileSync(path.join(root, `webapp/templates/${name}.html`), 'utf8');
  for (const match of template.matchAll(/<script>([\s\S]*?)<\/script>/g))
    vm.runInContext(match[1].replace(/{{[\s\S]*?}}/g, '/tasks'), context);
}
function reset(body, code = 200) {response = {body, code}; requests = []; toasts = []; timers = []; redirect = '';}
(async () => {
  for (const name of ['settings','courses','task_detail','accounts','user_accounts','users','proxies','tasks','user_tasks','update','new_task','logs']) load(name);
  reset({status: false, msg: '操作被拒绝'});
  await assert.rejects(context.apiFetch('/api/example'), /操作被拒绝/);
  reset({detail: [{msg: '非法参数'}]}, 422);
  await assert.rejects(context.apiFetch('/api/example'), /非法参数/);
  response = new Error('offline');
  await assert.rejects(context.apiFetch('/api/example'), /连接失败/);
  reset({detail: '无权保存'}, 403);
  await context.settingsPage().saveTiku();
  assert.equal(toasts.at(-1)[1], 'danger');
  assert.equal(toasts.at(-1)[0], '无权保存');
  reset({status: false, msg: '重启被拒绝'});
  await context.settingsPage().restartService();
  assert.equal(timers.length, 0, '失败不能安排刷新');
  assert.equal(toasts.at(-1)[1], 'danger');
  reset({id: 8});
  const courses = context.coursesPage(3);
  await courses.submitLaunch();
  assert.equal(requests.length, 0, '空选择不能意外启动全部课程');
  courses.courses = [{id: 21, _uid: 'a'}, {id: 22, _uid: 'b'}]; courses.selected = ['a'];
  await courses.submitLaunch();
  assert.equal(JSON.parse(requests[0][1].body).course_ids[0], '21');
  assert.equal(redirect, '/tasks/8');
  reset({status: false, msg: '任务已结束'});
  await context.taskDetail(4).cancel();
  assert.equal(toasts.at(-1)[1], 'danger');
  assert.equal(toasts.at(-1)[0], '任务已结束');
  reset({status: true});
  await context.logout();
  assert.equal(requests[0][0], '/api/logout');
  assert.equal(redirect, '/login');
  reset({detail: '退出失败'}, 500);
  await context.logout();
  assert.equal(redirect, '');
  // All management actions must stop on a rejected request and show the real
  // reason. This catches success fallbacks and ignored enable/disable failures.
  const actionCases = [
    [() => context.accountsPage(), 'submitCreate', [], '/api/accounts'],
    [() => context.accountsPage(), 'relogin', [{id: 3, phone: 'test'}], '/api/accounts/3/login'],
    [() => context.accountsPage(), 'remove', [{id: 3, phone: 'test'}], '/api/accounts/3'],
    [() => context.userAccountsPage(), 'submitCreate', [], '/api/accounts'],
    [() => context.userAccountsPage(), 'relogin', [{id: 3, phone: 'test'}], '/api/accounts/3/login'],
    [() => context.userAccountsPage(), 'remove', [{id: 3, phone: 'test'}], '/api/accounts/3'],
    [() => context.usersPage(), 'createUser', [], '/api/users'],
    [() => context.usersPage(), 'updateStatus', [{id: 3, status: 'disabled'}], '/api/users/3'],
    [() => context.usersPage(), 'deleteUser', [{id: 3, username: 'test'}], '/api/users/3'],
    [() => context.usersPage(), 'unbindAccount', [{id: 3}, {id: 4}], '/api/users/3/accounts/4'],
    [() => context.proxiesPage(), 'enable', [{id: 3}], '/api/proxies/3/enable'],
    [() => context.proxiesPage(), 'disable', [{id: 3}], '/api/proxies/3/disable'],
    [() => context.proxiesPage(), 'remove', [{id: 3}], '/api/proxies/3'],
    [() => context.proxiesPage(), 'retest', [{id: 3}], '/api/proxies/3/test'],
    [() => context.proxiesPage(), 'importScdn', ['http'], '/api/proxies/import/scdn'],
    [() => context.settingsPage(), 'saveProxy', [], '/api/settings/proxy'],
    [() => context.updatePage(), 'applyUpdate', [], '/api/update/apply'],
    [() => context.tasksPage(), 'cancel', [{id: 3}], '/api/tasks/3/cancel'],
    [() => context.userTasksPage(), 'cancel', [{id: 3}], '/api/tasks/3/cancel'],
  ];
  for (const [factory, name, args, url] of actionCases) {
    reset({detail: '真实失败原因'}, 403);
    const page = factory();
    await page[name](...args);
    assert.equal(requests[0][0], url, name);
    assert.equal(toasts.at(-1)[0], '真实失败原因', name);
    assert.equal(toasts.at(-1)[1], 'danger', name);
    assert.equal(page.actionError, '真实失败原因');
    assert.equal(requests.length, 1, '被拒绝后不能执行后续步骤');
  }
  reset({status: false, msg: '更新失败', stderr: '实际诊断'});
  const update = context.updatePage();
  await update.applyUpdate();
  assert.equal(update.updateOutput, '实际诊断');
  // Nested failures propagate to one boundary; independent concurrent action
  // failures must each be reported instead of creating rejected event promises.
  reset({detail: '网络不可用'}, 500);
  const nested = context.actionPage({
    async outer() {await this.inner(); redirect = 'must-not-continue';},
    async inner() {await context.apiFetch('/inner');},
  });
  await nested.outer();
  assert.equal(toasts.length, 1);
  assert.equal(redirect, '');
  console.log(`${actionCases.length} management failure paths checked`);
  console.log('UI action regression checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
