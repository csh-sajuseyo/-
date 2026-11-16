const {chromium}=require('playwright');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:390,height:844},deviceScaleFactor:2});
for(const n of process.argv.slice(2)){await p.goto('file://'+__dirname+'/'+n+'.html');await p.evaluate(()=>document.fonts.ready);await p.screenshot({path:__dirname+'/'+n+'.png'});}
await b.close();})();
