import { chromium, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const base=process.env.STORE_TEST_URL ?? 'http://localhost:18092';
if (!/^http:\/\/localhost:\d+$/.test(base)) throw Error('Tests restricted to a loopback preview / SSH tunnel.');
const evidence=path.join(root,'docs/woo-store/evidence');
const shots=path.join(root,'docs/woo-store/screenshots');
await mkdir(shots,{recursive:true});
const password=(await readFile(process.env.STORE_TEST_PASSWORD_FILE ?? path.join(root,'infra/woo-store/.private/admin_password'),'utf8')).trim();
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({baseURL:base,locale:'pt-BR',reducedMotion:'reduce',viewport:{width:1440,height:1000}});
const page=await context.newPage();
const report={base,scope:base.includes(':8092')?'VPS through SSH tunnel':'local isolated Docker',checks:[],screenshots:[],accessibility:[],consoleErrors:[]};
page.on('pageerror',e=>report.consoleErrors.push(e.message));
const check=(name,data={})=>{report.checks.push({name,ok:true,...data});};
async function snap(name){const file=name+'.png';await page.screenshot({path:path.join(shots,file),fullPage:true});report.screenshots.push(file);}
async function noOverflow(){expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);}
async function axe(label){
 const scan=await new AxeBuilder({page}).exclude('#wpadminbar').withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
 report.accessibility.push({page:label,violations:scan.violations.map(v=>({id:v.id,impact:v.impact,description:v.description,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))});
}
try{
 const response=await page.goto('/');expect(response.status()).toBe(503);await expect(page.getByRole('heading',{level:1})).toContainText('preparando');
 await snap('visitor-coming-soon-1440');check('Visitor sees no shop or offers');await axe('coming-soon');
 await page.goto('/wp-login.php?redirect_to='+encodeURIComponent(base+'/'));
 await page.locator('#user_login').fill(process.env.STORE_TEST_USER??'store_owner');
 await page.locator('#user_pass').fill(password);await page.locator('#wp-submit').click();
 await page.waitForURL(u=>!u.pathname.includes('wp-login.php'));
 await page.goto('/wp-admin/');await expect(page.locator('#wpadminbar')).toBeVisible();check('Real WordPress administrator login');
 await page.goto('/');await expect(page.locator('.hero-title')).toBeVisible();
 await snap('admin-home-initial');
 const firstDemo=await page.locator('.product-image').first().getAttribute('href');expect(firstDemo).toBeTruthy();
 for(const width of [360,390,768,1024,1440]){
   await page.setViewportSize({width,height:width<600?844:1000});
   for(const [label,route] of [['home','/'],['catalog','/produtos/'],['product',firstDemo]]){
     await page.goto(route);await page.locator('main').waitFor();await noOverflow();await snap(`${label}-${width}`);
     check(`${label}: viewport ${width}, no horizontal overflow`);
     if(width===390||width===1440)await axe(`${label}-${width}`);
   }
 }
 await page.setViewportSize({width:390,height:844});await page.goto('/');
 const menu=page.locator('.wp-block-navigation__responsive-container-open');await menu.click();
 await expect(page.locator('.wp-block-navigation__responsive-container.is-menu-open')).toBeVisible();
 await snap('mobile-navigation-390');await page.keyboard.press('Escape');await expect(menu).toBeFocused();check('Mobile navigation opens, Escape closes and restores focus');
 await page.goto('/produtos/');await page.getByLabel('Buscar no catálogo',{exact:true}).fill('termo-sem-resultado');await page.getByRole('button',{name:'Aplicar filtros'}).click();
 await expect(page.getByRole('heading',{name:/Nenhum resultado/})).toBeVisible();await snap('catalog-no-results-390');check('Catalogue filters submit and empty result is explicit');
 await page.getByRole('link',{name:'Limpar filtros'}).click();await expect(page.getByRole('heading',{name:'Ainda estamos selecionando.'})).toBeVisible();
 await page.goto('/?s=zzzinexistente');await expect(page.getByText('Ainda não encontramos isso.')).toBeVisible();check('WordPress search no-results');await snap('search-no-results-390');
 for(const [name,url]of [['about','/sobre/'],['contact','/contato/'],['faq','/perguntas-frequentes/'],['categories','/categorias/'],['404','/pagina-inexistente/'],['cart','/carrinho/'],['checkout','/finalizar-compra/'],['account','/minha-conta/']]){
   await page.goto(url);await noOverflow();await snap(name+'-390');await axe(name);check(name+' renders');
 }
 await page.goto('/perguntas-frequentes/');await page.getByText('Já posso comprar?',{exact:true}).click();await expect(page.getByText('Ainda não. A loja está em preparação e todos os fluxos comerciais estão bloqueados.')).toBeVisible();check('FAQ disclosure works');
 await page.goto('/');await page.keyboard.press('Tab');
 check('Keyboard focus available',{tag:await page.evaluate(()=>document.activeElement.tagName)});
 expect(report.consoleErrors).toEqual([]);
 const violations=report.accessibility.flatMap(x=>x.violations);
 report.ok=violations.length===0;
 await writeFile(path.join(evidence,'browser-results.json'),JSON.stringify(report,null,2));
 if(violations.length)throw Error(`Accessibility: ${violations.length} violations; see browser-results.json`);
 console.log(JSON.stringify({ok:report.ok,checks:report.checks.length,screenshots:report.screenshots.length,axe_scans:report.accessibility.length,scope:report.scope}));
}catch(e){report.ok=false;report.failure=e.message;await writeFile(path.join(evidence,'browser-results.json'),JSON.stringify(report,null,2));throw e;}
finally{await browser.close();}
