/* Optional real-layout checks using an already installed Playwright, no new dependency.
 * Every browser request is fulfilled/aborted locally. No live server or OpenAI access.
 * Usage: NODE_PATH=<existing-tool-modules> node chatbot/tests/browser/check_widget.cjs /tmp/fixtures
 */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const fixtures = path.resolve(process.argv[2]);
const app = path.resolve(__dirname, '../../..');
const executable = process.env.CHATBOT_TEST_BROWSER || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const mime = {'.css':'text/css','.js':'application/javascript','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.webp':'image/webp','.mp4':'video/mp4'};
const sizes = [[1440,900],[1024,768],[768,1024],[768,360],[390,844],[320,568],[390,320]];
function state(auth, messages=[]) {
  return {conversation:{id:'offline-conversation',status:'active'},messages,
    usage:auth?{limit:null,consumed:null,pending:null,remaining:null}:{limit:10,consumed:1,pending:0,remaining:9}};
}
function response(faqUrl) {
  return [{id:1,request_id:'offline',role:'assistant',status:'completed',created_at:'2026-11-06T12:00:00Z',
    content:'Inicia sesión y completa los datos del objeto.\n\n- Añade fotografías.\n- Envía la propuesta para su revisión.\n'+ 'Texto largo sin desbordamientos. '.repeat(40),
    sources:[{available:true,title:'¿Cómo publicar un objeto?',url:faqUrl,type:'faq'},
      {available:true,title:'Título público muy largo '.repeat(5),url:'/catalogo/?category=deporte',type:'category'},
      {available:false}]}];
}
(async()=>{
  const browser = await chromium.launch({executablePath:executable,headless:true});
  let cases=0;
  try {
    const manifest=JSON.parse(await fs.readFile(path.join(fixtures,'manifest.json'),'utf8'));
    for(const [width,height] of sizes) for(const auth of [false,true]) for(const language of ['es','en']) {
      const context=await browser.newContext({viewport:{width,height},reducedMotion:'reduce',isMobile:width<769,hasTouch:width<769});
      const page=await context.newPage();
      const errors=[]; page.on('pageerror',e=>errors.push(e.message));
      let current=state(auth), posts=0;
      await page.route('**/*',async route=>{
        const request=route.request(), url=new URL(request.url());
        if(url.hostname!=='trueca.test') return route.abort();
        if(url.pathname.startsWith('/chatbot/api/')) {
          if(request.method()==='POST') {
            posts++;
            current=state(auth,response(manifest.faq_url));
          }
          return route.fulfill({contentType:'application/json',body:JSON.stringify(current)});
        }
        if(url.pathname.startsWith('/static/')) {
          const file=path.resolve(app,'static',decodeURIComponent(url.pathname.slice(8)));
          if(!file.startsWith(path.join(app,'static')+path.sep)) return route.abort();
          try {return await route.fulfill({contentType:mime[path.extname(file)]||'application/octet-stream',body:await fs.readFile(file)});}
          catch {return route.fulfill({status:404,body:''});}
        }
        const file=url.pathname==='/faq/'?'faq.html':`${language}-${auth?'user':'anon'}.html`;
        return route.fulfill({contentType:'text/html',body:await fs.readFile(path.join(fixtures,file))});
      });
      await page.goto('http://trueca.test/');
      const launcher=page.locator('[data-chatbot-launcher]');
      await launcher.waitFor({state:'visible'});
      const button=await launcher.boundingBox();
      assert.ok(button.x>=0&&button.y>=0&&button.x+button.width<=width&&button.y+button.height<=height,`launcher ${width}x${height}`);
      const hero=await page.locator('.home-hero').boundingBox();
      await launcher.click();
      const panel=page.locator('[data-chatbot-panel]');
      const close=page.locator('[data-chatbot-close]');
      await panel.waitFor({state:'visible'});
      const box=await panel.boundingBox();
      assert.ok(box.x>=-1&&box.y>=-1&&box.x+box.width<=width+1&&box.y+box.height<=height+1,`panel ${width}x${height}`);
      assert.deepEqual(await page.locator('.home-hero').boundingBox(),hero,'portal layout unchanged');
      await page.locator('[data-chatbot-input]').fill('Resume cómo publicar un objeto');
      await page.locator('[data-chatbot-form]').evaluate(form=>form.requestSubmit());
      await page.locator('.chatbot__sources a').first().waitFor();
      assert.equal(posts,1);
      assert.equal(await page.locator('.chatbot__sources a').count(),2);
      const quota=page.locator('[data-chatbot-quota]');
      assert.equal(await quota.isVisible(),!auth);
      assert.equal(await page.locator('[data-chatbot-suggestions]').isVisible(),false);
      assert.ok(await page.locator('.chatbot__sources').getByText(language==='es'?'Referencia no disponible':'Reference unavailable').isVisible());
      const overflow=await panel.evaluate(p=>p.scrollWidth>p.clientWidth);
      assert.equal(overflow,false,'panel horizontal overflow');
      const messagesOverflow=await page.locator('[data-chatbot-scroll]').evaluate(p=>p.scrollWidth>p.clientWidth);
      assert.equal(messagesOverflow,false,'message horizontal overflow');
      const minimumContrast=await panel.evaluate(p=>{
        const rgb=value=>(value.match(/[\d.]+/g)||[]).slice(0,3).map(Number);
        const luminance=values=>values.map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4).reduce((sum,x,i)=>sum+x*[.2126,.7152,.0722][i],0);
        const ratio=(color,background)=>{const values=[luminance(rgb(color)),luminance(rgb(background))].sort((a,b)=>a-b);return (values[1]+.05)/(values[0]+.05);};
        const background=e=>{while(e){const value=getComputedStyle(e).backgroundColor;if(value!=='rgba(0, 0, 0, 0)'&&value!=='transparent')return value;e=e.parentElement;}return 'rgb(255, 255, 255)';};
        const values=Array.from(p.querySelectorAll('.chatbot__sources a,.chatbot__sources span,.chatbot__bubble time')).map(e=>ratio(getComputedStyle(e).color,background(e)));
        const input=p.querySelector('textarea');values.push(ratio(getComputedStyle(input,'::placeholder').color,background(input)));
        return Math.min(...values);
      });
      assert.ok(minimumContrast>=4.5,'small text contrast');
      const composer=await page.locator('[data-chatbot-input]').boundingBox();
      const closing=await close.boundingBox();
      assert.ok(composer.y>=box.y&&composer.y+composer.height<=box.y+box.height+1,'composer accessible');
      assert.ok(closing.y>=box.y&&closing.y+closing.height<=box.y+box.height,'close visible');
      // A link becomes keyboard focusable and navigates to an actual public FAQ anchor.
      const link=page.locator('.chatbot__sources a').first();
      await link.focus(); assert.equal(await link.evaluate(e=>e===document.activeElement),true);
      if(width===390&&height===844&&auth&&language==='en') {
        await panel.evaluate(p=>{
          const values=Array.from(p.querySelectorAll('p,a,h2,button,textarea')).map(e=>[e,parseFloat(getComputedStyle(e).fontSize)]);
          values.forEach(([e,size])=>e.style.fontSize=`${size*2}px`);
        });
        const enlarged=await page.locator('[data-chatbot-input]').boundingBox();
        assert.ok(enlarged.y+enlarged.height<=height,'enlarged text composer visible');
        assert.equal(await panel.evaluate(p=>p.scrollWidth>p.clientWidth),false,'enlarged text horizontal overflow');
      }
      if(width===390&&height===844&&!auth&&language==='es') {
        await page.setViewportSize({width,height:350});
        await page.waitForFunction(() => document.querySelector('[data-chatbot-input]').getBoundingClientRect().bottom <= window.visualViewport.height + window.visualViewport.offsetTop + 1);
        const inputBox=await page.locator('[data-chatbot-input]').boundingBox();
        assert.ok(inputBox.y+inputBox.height<=350,'visualViewport resize');
        await page.setViewportSize({width,height});
        await page.waitForFunction(h => window.visualViewport.height === h, height);
      }
      await page.keyboard.press('Escape');
      await panel.waitFor({state:'hidden'});
      assert.equal(await launcher.evaluate(e=>e===document.activeElement),true);
      // Reload restores persisted API references; no additional POST is made.
      await page.reload(); await launcher.waitFor({state:'visible'}); await launcher.click();
      await page.locator('.chatbot__sources a').first().waitFor(); assert.equal(posts,1);
      if(width===1440&&!auth&&language==='es') {
        await page.locator('.chatbot__sources a').first().click();
        await page.waitForURL('**/faq/#faq-question-*');
        assert.equal(await page.locator('.faq-item').first().evaluate(e=>e.open),true);
        assert.equal(await page.locator('summary').first().evaluate(e=>e===document.activeElement),true);
      }
      assert.deepEqual(errors,[]);
      cases++; await context.close();
    }
    console.log(`${cases} responsive cases passed: seven viewports × anonymous/authenticated × ES/EN. Chromium only; virtual viewport resize is not a physical keyboard test.`);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
