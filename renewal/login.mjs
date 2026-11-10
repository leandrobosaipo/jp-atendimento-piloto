const official="https://sistema.appbarber.com.br";
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export async function dependencies(page){
  return page.evaluate(()=>typeof window.jQuery==="function" &&
    !!window.jQuery._data(document.querySelector("#frmLogin"),"events")?.submit?.length &&
    typeof window.grecaptcha?.execute==="function" &&
    typeof window.grecaptcha?.ready==="function");
}
export async function loginWithProvider({credentials,browserFactory,handoff,trace}){
  const b=await browserFactory.launch();
  try{
    const c=await b.newContext({acceptDownloads:false,viewport:{width:1024,height:768}});
    await c.addInitScript(()=>document.addEventListener("submit",e=>e.preventDefault(),true));
    const page=await c.newPage();
    await page.goto(official+"/login.php",{waitUntil:"domcontentloaded",timeout:30000});
    if(page.url()!==official+"/login.php")throw Error("browser_route_held");
    let ready=false;
    for(let i=0;i<30&&!ready;i++){ready=await dependencies(page);if(!ready)await sleep(400);}
    if(!ready)throw Error("login_script_unavailable");
    await page.locator("#login-name").fill(credentials.userid);
    await page.locator("#login-pass").fill(credentials.password);
    await page.getByRole("button",{name:/^acessar$/i}).click();
    const deadline=Date.now()+300000;let since=Date.now(),shown="",dirty=true;
    while(Date.now()<deadline){
      if(trace.hold)throw Error(trace.hold);
      if(trace.success){
        // route.fetch updates the associated cookie jar before rendering.
        const cookies=(await c.cookies(official)).filter(x=>x.name&&x.value&&(x.expires<0||x.expires>Date.now()/1000));
        if(!cookies.length)throw Error("login_not_proven");
        return {origin:official,cookieHeader:cookies.map(x=>x.name+"="+x.value).join("; "),source_expires_at:null};
      }
      const otp=trace.code===8 && await page.locator("#frm2fa #login-2fa-code").first().isVisible();
      const captcha=await page.locator('iframe[src*="/recaptcha/"][src*="bframe"]:visible').count()>0;
      const reason=otp?"otp_required":captcha?"captcha_required":null;
      if(reason){
        if(shown!==reason){await handoff.report({reason_code:reason});shown=reason;dirty=true;}
        if(dirty){await handoff.frame({png_base64:(await page.screenshot()).toString("base64")});dirty=false;}
        const {action:a}=await handoff.poll();
        if(a){if(a.action==="click")await page.mouse.click(a.x,a.y);else if(a.action==="type_text")await page.keyboard.type(a.text);else if(a.action==="press")await page.keyboard.press(a.key);dirty=true;}
        since=Date.now();
      } else if(Date.now()-since>30000){
        throw Error(trace.sent ? "provider_result_unknown":"login_not_submitted");
      }
      await sleep(350);
    }
    throw Error("human_timeout");
  }finally{await b.close();}
}
export function recordReply(trace,body){
  if(body?.error===true){
    trace.code=Number(body.error_action_code);
    if(trace.code===8)return;
    trace.hold=({1:"credentials_rejected",2:"establishment_selection_required",4:"account_contract_action_required",5:"account_plan_action_required",6:"provider_account_verification",7:"provider_account_verification"})[trace.code]||"provider_rejected";
  }else if(body?.error===false && typeof body?.auth?.[0]?.redirect==="string"){
    const u=new URL(body.auth[0].redirect,official);
    if(u.origin===official)trace.success=true;else trace.hold="provider_result_unknown";
  }else trace.hold="provider_result_unknown";
}
