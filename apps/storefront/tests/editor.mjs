import { chromium, expect } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const base=process.env.STORE_TEST_URL??'http://localhost:18092';
if(!/^http:\/\/localhost:\d+$/.test(base))throw Error('Loopback only');
const password=(await readFile(process.env.STORE_TEST_PASSWORD_FILE??path.join(root,'infra/woo-store/.private/admin_password'),'utf8')).trim();
const browser=await chromium.launch();const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const report={scope:base.includes(':8092')?'VPS through SSH':'local',pages:[],pageErrors:errors};
try{
 await page.goto(base+'/wp-login.php');await page.locator('#user_login').fill('store_owner');await page.locator('#user_pass').fill(password);await page.locator('#wp-submit').click();await page.waitForURL(/wp-admin/);
 for(const [label,url]of [['dashboard','/wp-admin/'],['products','/wp-admin/edit.php?post_type=product'],['site-editor','/wp-admin/site-editor.php']]){
   await page.goto(base+url);await expect(page.locator('#wpadminbar')).toBeVisible();await page.waitForTimeout(3000);
   await page.screenshot({path:path.join(root,'docs/woo-store/screenshots/admin-'+label+'-1440.png'),fullPage:true});
   report.pages.push({label,status:'loaded'});
 }
 // Open the home template inside the native Gutenberg editor, without saving a change.
 await page.goto(base+'/wp-admin/site-editor.php?postId=aic-curadoria%2F%2Ffront-page&postType=wp_template&canvas=edit');
 await page.waitForTimeout(5000);
 const welcome=page.getByRole('button',{name:'Comece agora'});
 if(await welcome.isVisible())await welcome.click();
 report.invalidBlocks=await page.evaluate(()=>{
   const flatten=blocks=>blocks.flatMap(b=>[b,...flatten(b.innerBlocks??[])]);
   return flatten(wp.data.select('core/block-editor').getBlocks()).filter(b=>b.isValid===false).map(b=>({name:b.name,attributes:b.attributes,originalContent:b.originalContent?.slice(0,800)}));
 });
 await page.screenshot({path:path.join(root,'docs/woo-store/screenshots/admin-home-editor-1440.png'),fullPage:true});
 report.editorFrames=page.frames().length;
 report.ok=errors.length===0&&report.invalidBlocks.length===0;expect(errors).toEqual([]);expect(report.invalidBlocks).toEqual([]);
 await page.goto(base+'/');
 report.performance=await page.evaluate(()=>{const n=performance.getEntriesByType('navigation')[0],r=performance.getEntriesByType('resource');return{domContentLoaded_ms:Math.round(n.domContentLoadedEventEnd),load_ms:Math.round(n.loadEventEnd),resource_count:r.length,transfer_bytes:r.reduce((a,x)=>a+x.transferSize,0),script_count:r.filter(x=>x.initiatorType==='script').length};});
 console.log(JSON.stringify(report));
}finally{await writeFile(path.join(root,'docs/woo-store/evidence/editor-results.json'),JSON.stringify(report,null,2));await browser.close();}
