import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
const path = new URL('../plugins/guard/index.mjs', import.meta.url)
assert.ok(existsSync(path), 'Agent observation boundary is not implemented')
const { beamGuard } = await import(path)
const hooks = {}
beamGuard.apply({extension:()=>({registerHook:(name,fn)=>{hooks[name]=fn}})})
for (const name of ['read','shell','exec','web','subagent','grep']) {
  assert.equal(hooks.tool_call({name}).allow,false,`Hidden evaluator files must be unreachable through ${name}`)
}
for (const name of ['list_devices','read_device','call_device','set_device','stop_device','watch_device','unwatch_device']) {
  assert.equal(hooks.tool_call({name}).allow,true)
}
console.log('Agent boundary: seven official tools allowed; file/shell/web/subagent tools refused')
