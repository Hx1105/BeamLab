// Isolated control plane using the pinned official SDK. No real credential enters it.
import { readFile, writeFile } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { createClient, memoryJournal } from '../runtime/sdk-bundle.mjs'
const root = process.env.BEAMLAB_ROOT, home = process.env.AGH_HOME, profile = 'local-dev'
const owner = JSON.parse(await readFile(`${home}/data/daemon/owner.json`, 'utf8'))
const client = createClient({transport:{kind:'unix',path:owner.socketPath}, auth:{kind:'local'},journal:memoryJournal()})
await client.initialize()
const common = async () => ({profile,clientId:await client.clientId(),commandId:`beamlab-${randomUUID()}`})
async function settle(receipt) {
  const deadline = Date.now()+60000
  while(Date.now()<deadline) {
    const result = await client.packages.operation.get({profile,operationId:receipt.operationId})
    if(['completed','rolled-back'].includes(result.state)) return result
    if(['failed','cancelled'].includes(result.state)) throw new Error(JSON.stringify(result.error || result))
    await new Promise(resolve=>setTimeout(resolve,250))
  }
  throw new Error('Package operation deadline reached')
}
try {
  const action = process.argv[2]
  if(action === 'activate') {
    const id=process.argv[3] || '@agnes/mhs', ref=process.argv[4] || 'file:./plugins/mhs'
    const inspected=await settle(await client.packages.inspect({...await common(),source:{type:'file',ref}}))
    const p=inspected.preview
    if(!p || p.id!==id || p.blockers.length) throw new Error('Unexpected or blocked plugin preview')
    const old=(await client.packages.list({profile})).packages.find(x=>x.id===id)
    if(!old) await settle(await client.packages.install({...await common(),source:{type:'file',ref},expectedIntegrity:p.integrity}))
    else if(old.integrity!==p.integrity) await settle(await client.packages.update({...await common(),id,source:{type:'file',ref},expectedIntegrity:p.integrity}))
    await settle(await client.packages.trust({...await common(),id,expectedIntegrity:p.integrity,capabilityHash:p.capabilityHash}))
    await settle(await client.packages.enable({...await common(),id}))
    const status=(await client.packages.list({profile})).packages.find(x=>x.id===id)
    await writeFile(`${root}/evidence/plugin-${id.replaceAll('/','-').replaceAll('@','')}.json`,JSON.stringify(status,null,2))
    console.log(JSON.stringify(status))
  } else if(action === 'configure') {
    const before=await client.config.get()
    const req={providerId:'openai',accountId:'beamlab-mimo',baseUrl:'http://127.0.0.1:44482/v1',apiKey:'beamlab-local-placeholder'}
    const tested=await client.config.test(req)
    if(!tested.verified || !tested.models.some(m=>m.id==='mimo-v2.6-pro')) throw new Error('MiMo catalog verification failed')
    const saved=await client.config.save({...req,model:'mimo-v2.6-pro',label:'BeamLab MiMo',enabled:true,makeDefault:true,expectedRevision:before.revision})
    const safe={configured:saved.configured,profile:saved.profile,route:saved.provider?.route,model:saved.provider?.model,accountId:saved.defaultAccountId,revision:saved.revision,actual_inference_verified:false}
    await writeFile(`${root}/evidence/model-configuration.json`,JSON.stringify(safe,null,2))
    console.log(JSON.stringify(safe))
  } else if(action === 'cancel') {
    const sessionId=process.argv[3]
    if(!sessionId?.startsWith('agnes:local:local-dev:cli:workspace:')) throw new Error('Unexpected session scope')
    await client.notify('session/cancel',{sessionId})
    console.log(JSON.stringify({sessionId,cancel_sent:true}))
  } else if(action === 'status') console.log(JSON.stringify(await client.packages.list({profile})))
  else throw new Error('Use activate|configure|status')
} finally { await client.close() }
