const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname, '../../../static/js/chatbot.js'), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.childNodes = this.children; this.parent = null;
    this.events = {}; this.attrs = {}; this.hidden = false; this.disabled = false; this.readOnly = false;
    this.value = ''; this.textContent = ''; this.className = ''; this.scrollHeight = 200;
    this.scrollTop = 0; this.clientHeight = 150; this.open = false;
    const classes = new Set();
    this.classList = {add: name => classes.add(name), remove: name => classes.delete(name),
      contains: name => classes.has(name), toggle(name, on) { if (on) classes.add(name); else classes.delete(name); }};
    this.style = {setProperty(name, value) { this[name] = value; }};
  }
  addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
  fire(name, extra = {}) {
    const event = {preventDefault() { this.prevented = true; }, stopPropagation() {}, ...extra};
    for (const fn of this.events[name] || []) fn(event);
    return event;
  }
  append(...elements) { for (const e of elements) { e.parent = this; this.children.push(e); } }
  replaceChildren(...elements) { this.children = []; this.childNodes = this.children; this.append(...elements); }
  remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); }
  querySelector(selector) {
    if (selector === '.chatbot__bubble p') return this.querySelector('.chatbot__bubble')?.querySelector('p');
    const matches = e => selector.startsWith('.') ? e.className.split(' ').includes(selector.slice(1)) : e.tag === selector;
    for (const e of this.children) { if (matches(e)) return e; const nested = e.querySelector(selector); if (nested) return nested; }
    return null;
  }
  setAttribute(name, value) { this.attrs[name] = value; }
  focus() { if (!this.disabled) this.document.activeElement = this; }
  cloneNode() { return new Element(this.tag); }
  contains(element) { return this === element || this.children.some(child => child.contains(element)); }
  show() { this.open = true; this.modal = false; }
  showModal() { this.open = true; this.modal = true; }
  close() { this.open = false; queueMicrotask(() => this.fire('close')); }
}
const flush = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };
function state(messages = [], usage = {}) {
  return {conversation: {id: 'conversation-1', status: 'active'}, messages,
    usage: {limit: 10, consumed: 0, pending: 0, remaining: 10, ...usage}};
}
function turn(role, id, content, status = 'completed') {
  return {role, request_id: id, content, status, created_at: '2026-10-29T12:00:00Z'};
}
function setup({mobile = false, initial = state(), handler = null, remembered = {}, cookie = 'csrftoken=cookie-token'} = {}) {
  const root = new Element(), elements = {};
  for (const key of ['panel','launcher','input','form','send','reset','messages','scroll','status','error','error-text','retry','welcome','suggestions','quota','limit','hint','close']) elements[key] = new Element();
  const avatar = new Element(); avatar.className = 'chatbot__avatar';
  const suggestion = ['How does TRUEC@ work?', 'How can I earn points?', 'What items can I give?'].map(text => {
    const button = new Element('button'), label = new Element('text'); label.textContent = text; button.append(label); return button;
  });
  const hiddenToken = new Element('input'); hiddenToken.value = 'masked-form-token';
  elements.form.querySelector = () => hiddenToken;
  root.querySelector = selector => selector === '.chatbot__header .chatbot__avatar' ? avatar : elements[selector.slice(14, -1)];
  const authLinks = [new Element('a'), new Element('a')];
  root.querySelectorAll = selector => selector === '[data-chatbot-limit] a' ? authLinks : suggestion;
  root.append(elements.launcher, elements.panel);
  elements.panel.append(elements.close, elements.form, elements.scroll);
  elements.form.append(elements.input, elements.send);
  elements.scroll.append(elements.messages);
  const document = new Element(); document.documentElement = new Element(); document.documentElement.lang = 'en';
  document.cookie = cookie; document.visibilityState = 'visible'; document.activeElement = null;
  document.querySelector = () => root;
  const strings = {assistant:'Assistant',you:'You',loading:'Loading',thinking:'Thinking',
    remaining:'{remaining} of {limit} left',lastQuestion:'1 of {limit} left',exhausted:'Quota exhausted',
    failed:'Answer failed; no quota consumed',networkError:'Connection lost',loadError:'Load failed',
    sessionChanged:'Session changed',unavailable:'Unavailable',invalidInput:'Maximum {max}',
    resetRateLimited:'Wait before starting another conversation',rateLimited:'Please wait before sending another question',retry:'Retry',checkAnswer:'Check answer',reconnect:'Reconnect',newConversation:'New conversation',
    directions:'Get directions',sources:'Sources',officialInformation:'View official information',referenceUnavailable:'Reference unavailable'};
  const config = {sessionUrl:'/session/',messagesUrl:'/messages/',resetUrl:'/reset/',maxInputChars:2000,strings,
    sourcePaths:{faq:'/faq/',catalog:'/catalogo/',home:'/'}};
  document.getElementById = () => ({textContent: JSON.stringify(config)});
  document.createElement = tag => { const e = new Element(tag); e.document = document; return e; };
  for (const e of Object.values(elements)) e.document = document;
  const window = new Element(), media = new Element(), reduced = new Element(); media.matches = mobile; reduced.matches = true;
  window.location = {origin:'http://trueca.test'};
  const view = new Element(); Object.assign(view, {height: 700, width:390, offsetTop:0, offsetLeft:0}); window.visualViewport = view;
  const calls = [], timers = new Map(), storage = new Map(Object.entries(remembered)); let nextTimer = 1;
  window.matchMedia = query => query.includes('768') ? media : reduced;
  window.HTMLDialogElement = function() {}; window.crypto = webcrypto;
  window.sessionStorage = {getItem: key => storage.get(key),setItem: (key,value) => storage.set(key,value)};
  window.setTimeout = (fn, ms) => { const id = nextTimer++; timers.set(id,{fn,ms}); return id; };
  window.clearTimeout = id => timers.delete(id);
  window.fetch = async (url, options) => {
    const call = {url, ...options, payload: options.body ? JSON.parse(options.body) : undefined}; calls.push(call);
    const result = handler ? await handler(call) : {code:200,data:initial};
    return {status:result.code,headers:{get:()=>result.type || 'application/json'},json:async()=>JSON.parse(JSON.stringify(result.data))};
  };
  vm.runInNewContext(source, {window,document,AbortController,Uint8Array,Intl,Date,Map,Set,JSON,Array,Number,String,URL,decodeURIComponent,encodeURIComponent});
  return {root,...elements,suggestion,authLinks,document,window,media,view,calls,timers,storage,
    posts:()=>calls.filter(call=>call.method==='POST'),
    async poll() { const entry = [...timers].find(([,v])=>v.ms===1500); assert.ok(entry,'poll scheduled'); timers.delete(entry[0]); await entry[1].fn(); await flush(); }};
}
function answeringBackend(initial = state()) {
  let current = initial;
  return call => {
    if (call.method === 'POST' && call.url === '/messages/') {
      const p = call.payload;
      if (!current.messages.some(m => m.request_id === p.request_id)) {
        current.messages.push(turn('user',p.request_id,p.content), turn('assistant',p.request_id,'Simulated answer'));
        if (current.usage.limit !== null) { current.usage.consumed++; current.usage.remaining--; }
      }
    }
    if (call.url === '/reset/') current = {...current,conversation:{id:'conversation-2',status:'active'},messages:[]};
    return {code:200,data:current};
  };
}

test('boot fetches server history even while closed; never stores message content', async () => {
  const x=setup({initial:state([turn('assistant','a','Private history')])}); await flush();
  assert.equal(x.calls[0].url,'/session/'); assert.equal(x.panel.open,false);
  assert.equal(x.messages.children[0].querySelector('p').textContent,'Private history');
  assert.equal(x.storage.size,0); assert.equal(x.calls[0].credentials,'same-origin'); assert.equal(x.calls[0].cache,'no-store');
});
test('desktop opens nonmodal, Escape closes and restores launcher focus', async () => {
  const x=setup(); await flush(); x.launcher.fire('click'); await flush();
  assert.equal(x.panel.modal,false); assert.equal(x.document.activeElement,x.input);
  assert.equal(x.launcher.attrs['aria-expanded'],'true');
  x.panel.fire('keydown',{key:'Escape'}); await flush();
  assert.equal(x.panel.open,false); assert.equal(x.document.activeElement,x.launcher);
  assert.deepEqual([...x.storage.keys()].sort(),['trueca-chatbot-hint-seen','trueca-chatbot-open']);
});
test('mobile opens modal without forcing the keyboard; follows visual viewport resize', async () => {
  const x=setup({mobile:true}); await flush(); x.launcher.fire('click'); await flush();
  assert.equal(x.panel.modal,true); assert.equal(x.document.activeElement,x.close);
  assert.equal(x.document.documentElement.classList.contains('chatbot-modal-open'),true);
  Object.assign(x.view,{height:380,offsetTop:110}); x.view.fire('resize');
  assert.equal(x.panel.style['--chatbot-vv-height'],'380px'); assert.equal(x.panel.style['--chatbot-vv-top'],'110px');
  x.panel.fire('cancel'); await flush();
  assert.equal(x.document.documentElement.classList.contains('chatbot-modal-open'),false);
});
test('resizing an open panel changes native modality and keeps it open', async () => {
  const x=setup(); await flush(); x.launcher.fire('click'); await flush();
  x.media.matches=true; x.media.fire('change'); await flush();
  assert.equal(x.panel.open,true); assert.equal(x.panel.modal,true); assert.equal(x.launcher.attrs['aria-expanded'],'true');
});
test('suggested question uses messages endpoint, CSRF and authoritative quota', async () => {
  const x=setup({handler:answeringBackend()}); await flush(); x.suggestion[0].fire('click'); await flush();
  assert.equal(x.posts().length,1); const call=x.posts()[0];
  assert.equal(call.payload.content,'How does TRUEC@ work?'); assert.equal(call.headers['X-CSRFToken'],'cookie-token');
  assert.match(call.payload.request_id,/^[a-f0-9-]{36}$/); assert.equal(x.quota.textContent,'9 of 10 left');
  assert.equal(x.messages.children.length,2); assert.equal(x.suggestions.hidden,true);
});
test('while processing, send/reset are disabled and programmatic submissions cannot duplicate', async () => {
  let release;
  const answer=answeringBackend();
  const x=setup({handler:call=>call.method==='POST'?new Promise(resolve=>{release=()=>resolve(answer(call));}):answer(call)});
  await flush(); x.input.value='Hello'; x.form.fire('submit'); await flush();
  assert.equal(x.send.disabled,true); assert.equal(x.reset.disabled,true); assert.equal(x.input.readOnly,true);
  assert.equal(x.status.textContent,'Thinking'); x.form.fire('submit'); assert.equal(x.posts().length,1);
  release(); await flush(); assert.equal(x.reset.disabled,false); assert.equal(x.input.readOnly,false);
});
test('a lost POST response retries the exact UUID and content without charging twice', async () => {
  const backend=answeringBackend(); let lost=true;
  const x=setup({handler:call=>{const result=backend(call);if(call.method==='POST'&&lost){lost=false;throw new Error('lost');}return result;}});
  await flush(); x.input.value='Safe retry'; x.form.fire('submit'); await flush();
  assert.equal(x.error.hidden,false); assert.equal(x.retry.textContent,'Check answer'); assert.equal(x.send.disabled,true);
  x.retry.fire('click'); await flush();
  assert.deepEqual(x.posts()[0].payload,x.posts()[1].payload); assert.equal(x.quota.textContent,'9 of 10 left');
  assert.equal(x.messages.children.length,2); assert.equal(x.error.hidden,true);
});
test('pending history is polled, does not issue another POST and recovers after completion', async () => {
  const data=state([turn('user','pending','Question','pending')],{pending:1,remaining:9});
  const x=setup({handler:()=>({code:200,data})}); await flush();
  assert.equal(x.send.disabled,true); assert.equal(x.status.textContent,'Thinking');
  data.messages[0].status='completed'; data.messages.push(turn('assistant','pending','Answer'));
  data.usage.pending=0; data.usage.consumed=1; await x.poll();
  assert.equal(x.posts().length,0); assert.equal(x.messages.children.length,2); assert.equal(x.reset.disabled,false);
});
test('failed answers allow a new UUID only after terminal failure is confirmed', async () => {
  let failed=true;
  const backend=answeringBackend();
  const x=setup({handler:call=>{
    if(call.method==='POST'&&failed){failed=false; return {code:502,data:state([turn('user',call.payload.request_id,call.payload.content,'failed')])};}
    return backend(call);
  }});
  await flush(); x.input.value='Retry failure'; x.form.fire('submit'); await flush();
  assert.match(x['error-text'].textContent,/no quota/); assert.equal(x.reset.disabled,false);
  x.retry.fire('click'); await flush(); assert.notEqual(x.posts()[0].payload.request_id,x.posts()[1].payload.request_id);
});
test('quota exhaustion exposes login invitation; reset never restores displayed quota', async () => {
  const x=setup({handler:answeringBackend(state([],{consumed:10,remaining:0}))}); await flush();
  assert.equal(x.limit.hidden,false); assert.equal(x.input.disabled,true); assert.equal(x.quota.textContent,'Quota exhausted');
  x.reset.fire('click'); await flush(); assert.equal(x.input.disabled,true); assert.equal(x.limit.hidden,false);
});
test('authenticated responses hide the anonymous counter and invitation', async () => {
  const x=setup({initial:state([],{limit:null,remaining:null,consumed:null,pending:null})}); await flush();
  assert.equal(x.quota.hidden,true); assert.equal(x.limit.hidden,true); assert.equal(x.input.disabled,false);
});
test('CSRF/session rejection clears private state and reconnects without resending', async () => {
  const x=setup({handler:call=>call.method==='POST'?{code:403,type:'text/html',data:null}:{code:200,data:state()}});
  await flush(); x.input.value='Keep this draft'; x.form.fire('submit'); await flush();
  assert.equal(x.input.value,''); assert.equal(x.input.disabled,true); assert.equal(x.retry.textContent,'Reconnect');
  x.retry.fire('click'); await flush(); assert.equal(x.posts().length,1); assert.equal(x.input.disabled,false);
});
test('load failure is recoverable and fallback CSRF token supports HttpOnly cookies', async () => {
  let failed=true; const backend=answeringBackend();
  const x=setup({cookie:'',handler:call=>{if(failed){failed=false;throw new Error('offline');}return backend(call);}});
  await flush(); assert.equal(x.error.hidden,false); x.retry.fire('click'); await flush();
  x.input.value='Hello'; x.form.fire('submit'); await flush(); assert.equal(x.posts()[0].headers['X-CSRFToken'],'masked-form-token');
});
test('untrusted provider text is inserted as literal text, never HTML', async () => {
  const content='<img src=x onerror=alert(1)><script>bad()</script>';
  const x=setup({initial:state([turn('assistant','unsafe',content)])}); await flush();
  const p=x.messages.children[0].querySelector('p'); assert.equal(p.textContent,content); assert.equal(p.children.length,0);
});
test('composition and Shift+Enter do not submit; ordinary Enter sends', async () => {
  const x=setup({handler:answeringBackend()}); await flush(); x.input.value='Hello';
  x.input.fire('keydown',{key:'Enter',isComposing:true}); x.input.fire('keydown',{key:'Enter',shiftKey:true});
  assert.equal(x.posts().length,0); x.input.fire('keydown',{key:'Enter'}); await flush(); assert.equal(x.posts().length,1);
});
test('only the non-sensitive open preference is restored across navigation', async () => {
  const x=setup({remembered:{'trueca-chatbot-open':'1','trueca-chatbot-hint-seen':'1'}}); await flush();
  assert.equal(x.panel.open,true); assert.equal(x.hint.hidden,true); assert.equal(x.storage.size,2);
});

test('the last reserved slot disables sending without claiming the question was consumed', async () => {
  const x=setup({initial:state([turn('user','pending','Question','pending')],{consumed:9,pending:1,remaining:0})});
  await flush(); assert.equal(x.send.disabled,true); assert.equal(x.limit.hidden,true);
  assert.equal(x.quota.textContent,'0 of 10 left');
});
test('authentication links close the persisted panel so the login form stays accessible', async () => {
  const x=setup({initial:state([],{consumed:10,remaining:0}),remembered:{'trueca-chatbot-open':'1'}});
  await flush(); x.authLinks[0].fire('click'); assert.equal(x.storage.get('trueca-chatbot-open'),'0');
});

test('backend frequency rejection preserves draft and shows a rate message without quota invitation', async () => {
  const x=setup({initial:state([],{limit:null,consumed:null,pending:null,remaining:null}),
    handler:call=>call.method==='POST'?{code:429,data:{error:'rate_limited'}}:
      {code:200,data:state([],{limit:null,consumed:null,pending:null,remaining:null})}});
  await flush(); x.input.value='Keep my question'; x.form.fire('submit'); await flush();
  assert.equal(x.input.value,'Keep my question'); assert.equal(x.limit.hidden,true);
  assert.equal(x.error.hidden,false); assert.equal(x['error-text'].textContent,'Please wait before sending another question');
  assert.equal(x.posts().length,1);
});

test('pagehide clears private history and drafts before BFCache restores a different session', async () => {
  let current=state([turn('assistant','private','Private account history')]);
  const x=setup({handler:()=>({code:200,data:current})}); await flush();
  x.input.value='Private draft'; x.window.fire('pagehide');
  assert.equal(x.messages.children.length,0); assert.equal(x.input.value,''); assert.equal(x.quota.hidden,true);
  current=state(); current.conversation.id='new-session-conversation';
  x.window.fire('pageshow',{persisted:true}); await flush();
  assert.equal(x.messages.children.length,0); assert.equal(x.input.disabled,false);
});
test('late responses after leaving a page cannot repopulate private content', async () => {
  let release;
  const x=setup({handler:call=>call.method==='POST'?new Promise(resolve=>{release=resolve;}):{code:200,data:state()}});
  await flush(); x.input.value='Private outgoing question'; x.form.fire('submit'); await flush();
  x.window.fire('pagehide');
  release({code:200,data:state([turn('assistant','private','Private late response')])}); await flush();
  assert.equal(x.messages.children.length,0); assert.equal(x.input.value,''); assert.equal(x.input.disabled,true);
});
test('conversation refresh failure hides and discards prior private messages', async () => {
  let fail=false;
  const x=setup({handler:()=>{if(fail)throw new Error('offline');return {code:200,data:state([turn('assistant','a','Private')])};}});
  await flush(); fail=true; x.window.fire('focus'); await flush();
  assert.equal(x.messages.children.length,0); assert.equal(x.error.hidden,false);
});
test('rate limited resets preserve the current conversation and show a recoverable message', async () => {
  const x=setup({handler:call=>call.url==='/reset/'?{code:429,data:{error:'rate_limited'}}:{code:200,data:state()}});
  await flush(); x.reset.fire('click'); await flush();
  assert.equal(x['error-text'].textContent,'Wait before starting another conversation'); assert.equal(x.reset.disabled,false);
});

test('official sources appear below the safe text and restore from history', async () => {
  const message={...turn('assistant','source','Paragraph one.\n\n- Safe text'), sources:[
    {title:'¿Cómo publicar?',url:'/faq/#faq-question-12',type:'faq',available:true},
    {title:'Deporte',url:'/catalogo/?category=deporte',type:'category',available:true},
    {title:'Centro público',url:'/#puntos-limpios',type:'center',available:true}]};
  const x=setup({initial:state([message])}); await flush();
  const sources=x.messages.children[0].querySelector('.chatbot__sources');
  assert.equal(sources.hidden,false); assert.equal(sources.querySelector('p').textContent,'Sources');
  const rows=sources.querySelector('ul').children;
  assert.equal(rows.length,3); assert.equal(rows[0].querySelector('a').href,'/faq/#faq-question-12');
  assert.equal(rows[0].querySelector('a').attrs['aria-label'],'View official information: ¿Cómo publicar?');
  assert.equal(x.messages.children[0].querySelector('p').textContent,message.content);
});
test('unsafe URLs never become links, including protocol-relative and nonpublic paths', async () => {
  for(const url of ['javascript:alert(1)','data:text/html,evil','//evil.invalid/','/admin/','/faq/?private=1#faq-question-1',
      'https://evil.invalid/','/catalogo/?category=one&extra=secret','/\\evil.invalid/']) {
    const x=setup({initial:state([{...turn('assistant','a','Safe'),sources:[{title:'UNTRUSTED',url,type:'faq',available:true}]}])}); await flush();
    const sources=x.messages.children[0].querySelector('.chatbot__sources');
    assert.equal(sources.querySelector('a'),null,url);
    assert.equal(sources.querySelector('ul').children[0].querySelector('span').textContent,'Reference unavailable');
  }
});
test('withdrawn sources have no stale title and duplicate unavailable labels are compact', async () => {
  const x=setup({initial:state([{...turn('assistant','a','Historical answer'),sources:[{available:false},{available:false}]}])}); await flush();
  const rows=x.messages.children[0].querySelector('.chatbot__sources').querySelector('ul').children;
  assert.equal(rows.length,1); assert.equal(rows[0].querySelector('span').textContent,'Reference unavailable');
  assert.equal(rows[0].querySelector('a'),null);
});
test('structured rules without a public URL render informative text without invented links', async () => {
  const x=setup({initial:state([{...turn('assistant','a','100 puntos'),sources:[
    {available:true,type:'business_rule',title:'Puntos por registro público',url:null}]}])}); await flush();
  const row=x.messages.children[0].querySelector('.chatbot__sources').querySelector('ul').children[0];
  assert.equal(row.querySelector('a'),null); assert.equal(row.querySelector('span').textContent,'Puntos por registro público');
});
test('HTML in generated content and titles stays literal and long text remains intact', async () => {
  const payload='<img src=x onerror=alert(1)>'+ ' long text '.repeat(500);
  const x=setup({initial:state([{...turn('assistant','a',payload),sources:[
    {available:true,type:'faq',title:'<script>evil</script>',url:'/faq/#faq-question-1'}]}])}); await flush();
  const message=x.messages.children[0];
  assert.equal(message.querySelector('p').textContent,payload);
  assert.equal(message.querySelector('script'),null); assert.equal(message.querySelector('img'),null);
  assert.equal(message.querySelector('a').textContent,'<script>evil</script>');
});
test('refresh removes a reference link withdrawn by the server without rewriting the answer', async () => {
  const message={...turn('assistant','a','Old answer'),sources:[{available:true,type:'faq',title:'FAQ',url:'/faq/#faq-question-1'}]};
  const initial=state([message]); const x=setup({handler:()=>({code:200,data:initial})}); await flush();
  assert.ok(x.messages.children[0].querySelector('a'));
  message.sources=[{available:false}]; x.window.fire('focus'); await flush();
  assert.equal(x.messages.children[0].querySelector('a'),null);
  assert.equal(x.messages.children[0].querySelector('p').textContent,'Old answer');
});
test('old clients and messages without sources remain compatible; user sources are never rendered', async () => {
  const x=setup({initial:state([turn('assistant','a','Legacy'),{...turn('user','b','User'),sources:[
    {available:true,type:'faq',title:'Forged',url:'/faq/#faq-question-1'}]}])}); await flush();
  for(const message of x.messages.children) assert.equal(message.querySelector('.chatbot__sources').hidden,true);
});
test('only backend map directions with permitted origin and coordinates become external links', async () => {
  for (const [url,allowed] of [['https://www.google.com/maps/dir/?api=1&destination=28,-16',true],
    ['https://evil.invalid/maps/dir/?api=1&destination=28,-16',false],['javascript:evil',false],
    ['https://www.google.com/maps/dir/?api=1&destination=private-address',false],
    ['https://www.google.com/maps/dir/?api=1&destination=999,-16',false]]) {
    const x=setup({initial:state([{...turn('assistant','a','Public center'),sources:[
      {available:true,type:'center',title:'Centro',url:'/#puntos-limpios',directions_url:url}]}])}); await flush();
    const row=x.messages.children[0].querySelector('.chatbot__sources').querySelector('ul').children[0];
    assert.equal(row.children.length,allowed?2:1);
    if(allowed) {assert.equal(row.children[1].rel,'noopener noreferrer');assert.equal(row.children[1].target,'_blank');}
  }
});
test('following an official internal source leaves the destination portal unobscured', async()=>{
  const x=setup({initial:state([{...turn('assistant','a','Answer'),sources:[
    {available:true,type:'faq',title:'FAQ',url:'/faq/#faq-question-1'}]}])}); await flush();
  x.launcher.fire('click'); await flush();
  x.messages.children[0].querySelector('a').fire('click'); await flush();
  assert.equal(x.storage.get('trueca-chatbot-open'),'0'); assert.equal(x.panel.open,false);
});
