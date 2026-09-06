const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
class Element {
  constructor() { this.hidden=false; this.value=''; this.textContent=''; this.listeners={}; }
  addEventListener(k,fn) { this.listeners[k]=fn; }
  focus() { this.focused=true; } setAttribute() {}
}
async function exercise(outcome) {
  const selectors=['wifi-connect','wifi-connect-button','wifi-ssid-input','wifi-password-input','wifi-connect-status','wifi-status','reconnect-success','reconnect-heading','wifi-networks'];
  const els=Object.fromEntries(selectors.map(s=>[`[data-${s}]`,new Element()]));
  const form=els['[data-wifi-connect]'], panel=els['[data-reconnect-success]'], list=els['[data-wifi-networks]'];
  const heading=els['[data-reconnect-heading]'];
  panel.hidden=true;
  els['[data-wifi-ssid-input]'].value='Fixture Home';
  els['[data-wifi-password-input]'].value='fixture-password';
  const wifiPanel={dataset:{recoveryCsrf:'fixture-csrf'},querySelector:s=>els[s]||null,querySelectorAll:s=>s==='[data-reconnect-controls]'?[form]:[]};
  let complete;
  const pending=new Promise(resolve=>{complete=resolve;});
  const context={wifiPanel,escapeHtml:x=>String(x),fetch:async (url,options)=>{
    if(url==='/api/wifi/status') return {json:async()=>({available:true,networks:[]})};
    assert.equal(panel.hidden,false,'Handoff must precede sending credentials');
    assert.equal(form.hidden,true);
    assert.equal(options.headers['X-Story-Dock-Recovery-CSRF'],'fixture-csrf');
    await pending;
    if(outcome==='lost') throw Error('disconnected');
    return {ok:outcome==='ok',json:async()=>({ok:outcome==='ok',available:true,message:'fixture outcome',networks:[]})};
  }};
  vm.createContext(context);
  const source=fs.readFileSync(process.argv[2],'utf8');
  vm.runInContext(source.slice(source.indexOf('if (wifiPanel) {'),source.indexOf('if (bluetoothPanel) {')),context);
  await new Promise(resolve=>setImmediate(resolve));
  const submitted=form.listeners.submit({preventDefault(){}});
  assert.equal(panel.hidden,false);
  assert.equal(heading.focused,true);
  complete(); await submitted;
  if(outcome==='rejected') {
    assert.equal(panel.hidden,true);
    assert.equal(form.hidden,false);
    assert.equal(list.hidden,false);
    assert.equal(els['[data-wifi-password-input]'].focused,true);
  } else {
    assert.equal(panel.hidden,false);
    assert.equal(form.hidden,true);
    assert.equal(list.hidden,true);
    assert.equal(els['[data-wifi-password-input]'].value,'');
    assert.equal(heading.textContent,'Next, return to your home Wi-Fi.');
  }
}
(async()=>{for(const result of ['ok','lost','rejected']) await exercise(result); console.log('reconnect handoff behavior: ok');})().catch(e=>{console.error(e);process.exitCode=1;});
